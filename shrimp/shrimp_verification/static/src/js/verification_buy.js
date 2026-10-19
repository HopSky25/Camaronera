/* CamaronMarket — bloque «Verificación» de la página de compra (rediseño 2026-10).
 *
 * Decisiones del usuario (finales):
 *   - Una fila «Utilizar servicio de verificación» con interruptor.
 *     Obligatoria: apagado = declarada por las partes; encendido = verificadora
 *     de la plataforma (empieza encendido, sin verificadora preelegida).
 *     Opcional: apagado = compra directa (empieza apagado); encendido =
 *     verificadora de la plataforma; «¿Ya tienen su propia verificadora?»
 *     abre la declarada.
 *   - Verificadoras en tarjetas apiladas: las 3 recomendadas y «Ver todas»,
 *     que abre un modal con búsqueda, filtros y orden. Elegir en el modal
 *     marca la tarjeta de atrás (y la muestra como elegida).
 *   - Honorario (D-40): la tarifa publicada por cada verificadora, calculada
 *     en vivo con la cantidad; la del resumen es la de la elegida.
 * Lo que viaja en el POST no cambia: verification_mode (radios ocultos que
 * mueve el interruptor; se dispara «change» para shrimp_buy.js),
 * verifier_ref, declared_source, external_verifier_*.
 */
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }
    function money(n) {
        return (isNaN(n) ? 0 : n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }

    function init() {
        var box = $("svb");
        if (!box || box.dataset.svbInit) { return; }
        box.dataset.svbInit = "1";
        var f = $("svForm");
        if (!f) { return; }
        var mand = box.dataset.policy === "mandatory";
        var uom = box.dataset.uom || "";

        var toggle = $("svb_toggle");
        var stateTxt = $("svb_state_txt");
        var pBox = $("sv_platform_box");
        var dBox = $("sv_declared_box");
        var direct = $("svb_direct");
        var plazo = $("svb_plazo");
        var qtyInput = $("qty_input");
        var qtyVerify = $("qty_hidden_verify");
        var availEl = $("available_qty");
        var available = availEl ? num(availEl.dataset.available) : 0;
        var priceEl = $("unit_price");
        var price = priceEl ? num(priceEl.dataset.price) : 0;
        var msgEl = $("svMsg");
        var cfgEl = $("svFeeCfg");
        var globalCfg = cfgEl ? { base: cfgEl.dataset.base, cents: cfgEl.dataset.cents, min: cfgEl.dataset.min, max: cfgEl.dataset.max } : null;
        var cards = Array.prototype.slice.call(f.querySelectorAll("label.svb-card"));

        function qty() { return qtyInput ? num(qtyInput.value) : 0; }
        function radio(v) { return f.querySelector('input[name="verification_mode"][value="' + v + '"]'); }
        function mode() { var m = f.querySelector('input[name="verification_mode"]:checked'); return m ? m.value : ""; }
        function chosen() { return f.querySelector('input[name="verifier_ref"]:checked'); }
        function cardOf(input) { return input ? input.closest("label.svb-card") : null; }

        function say(text) {
            if (!msgEl) { return; }
            msgEl.textContent = text || "";
            msgEl.style.display = text ? "block" : "none";
            if (text) { msgEl.scrollIntoView({ behavior: "smooth", block: "center" }); }
        }

        // Honorario con la tarifa de la tarjeta (D-40): base + cantidad ×
        // por unidad; mínimo y tope solo en la tarifa global de respaldo.
        function feeOf(card, q) {
            var v = num(card.dataset.base) + Math.max(0, q) * num(card.dataset.unit);
            var mn = num(card.dataset.min), mx = num(card.dataset.max);
            if (mn) { v = Math.max(v, mn); }
            if (mx) { v = Math.min(v, mx); }
            return Math.round(v * 100) / 100;
        }

        function setMode(v) {
            var r = radio(v);
            if (!r || r.disabled) { return; }
            r.checked = true;
            sync();
            r.dispatchEvent(new Event("change", { bubbles: true }));
        }

        function updateCfg() {
            if (!cfgEl) { return; }
            var c = cardOf(chosen());
            if (c && mode() === "platform") {
                cfgEl.dataset.base = c.dataset.base;
                cfgEl.dataset.cents = String(num(c.dataset.unit) * 100);
                cfgEl.dataset.min = c.dataset.min || "0";
                cfgEl.dataset.max = c.dataset.max || "0";
                cfgEl.dataset.selected = c.dataset.ref;
            } else if (globalCfg) {
                cfgEl.dataset.base = globalCfg.base; cfgEl.dataset.cents = globalCfg.cents;
                cfgEl.dataset.min = globalCfg.min; cfgEl.dataset.max = globalCfg.max;
                cfgEl.dataset.selected = "";
            }
        }

        function paint() {
            var q = qty();
            var sel = chosen();
            cards.forEach(function (c) {
                var r = c.querySelector('input[name="verifier_ref"]');
                var on = !!(r && r.checked);
                c.classList.toggle("is-selected", on);
                // Las 3 recomendadas y la elegida (aunque venga del modal).
                c.hidden = !(c.classList.contains("is-top") || on);
                var fv = c.querySelector(".svb-fee-v");
                if (fv) { fv.textContent = "$" + money(feeOf(c, q)); }
            });
            var more = $("svb_more");
            if (more) {
                var t = more.querySelector(".svb-card-meta span");
                if (t && sel && !cardOf(sel).classList.contains("is-top")) {
                    t.textContent = "Elegiste una de la lista completa · cambiar";
                }
            }
        }

        function sync() {
            var m = mode();
            if (toggle) { toggle.checked = m === "platform"; }
            if (pBox) { pBox.hidden = m !== "platform"; }
            if (dBox) { dBox.hidden = m !== "declared"; }
            if (direct) { direct.hidden = m !== ""; }
            if (plazo) { plazo.hidden = !m; }
            if (stateTxt) {
                stateTxt.textContent = m === "platform"
                    ? "Una verificadora acreditada revisa el lote en planta (con honorario)."
                    : m === "declared"
                        ? "Verificación declarada por las partes, sin honorario."
                        : "Compra directa, sin verificar.";
            }
            f.querySelectorAll('input[name="verifier_ref"]').forEach(function (r) {
                if (m === "platform") { r.setAttribute("required", "required"); } else { r.removeAttribute("required"); }
            });
            updateCfg();
            paint();
        }

        if (toggle) {
            toggle.addEventListener("change", function () {
                say("");
                setMode(toggle.checked ? "platform" : (mand ? "declared" : ""));
            });
        }
        var own = $("svb_own"), back = $("svb_own_back");
        if (own) { own.addEventListener("click", function () { setMode("declared"); var i = dBox && dBox.querySelector("input[type=text]"); if (i) { i.focus(); } }); }
        if (back) { back.addEventListener("click", function () { setMode(""); if (own) { own.focus(); } }); }
        f.querySelectorAll('input[name="verifier_ref"]').forEach(function (r) {
            r.addEventListener("change", function () {
                say("");
                updateCfg(); paint();
                // Para el resumen de la derecha (shrimp_buy.js).
                document.dispatchEvent(new CustomEvent("svb:fee", { bubbles: true }));
            });
        });
        if (qtyInput) { qtyInput.addEventListener("input", function () { paint(); renderAll(); }); }

        // ---------------- Modal «Ver todas» ----------------
        var modal = $("svbAll");
        var list = $("svb_all");
        var q = $("svb_q"), sortSel = $("svb_sort"), fRev = $("svb_f_reviews"), fOwn = $("svb_f_own");
        var empty = $("svb_all_empty"), countEl = $("svb_all_n");
        var opener = null;

        function renderAll() {
            if (!modal || !list) { return; }
            var text = ((q && q.value) || "").trim().toLowerCase();
            var qq = qty();
            var rows = cards.filter(function (c) {
                if (text && (c.dataset.buscar || "").indexOf(text) === -1) { return false; }
                if (fRev && fRev.checked && !num(c.dataset.reviews)) { return false; }
                if (fOwn && fOwn.checked && c.dataset.src !== "verifier") { return false; }
                return true;
            });
            var how = sortSel ? sortSel.value : "rating";
            rows.sort(function (a, b) {
                if (how === "fee_asc") { return feeOf(a, qq) - feeOf(b, qq); }
                if (how === "fee_desc") { return feeOf(b, qq) - feeOf(a, qq); }
                if (how === "done") { return num(b.dataset.done) - num(a.dataset.done); }
                if (how === "name") { return (a.dataset.name || "").localeCompare(b.dataset.name || "", "es"); }
                return num(a.dataset.idx) - num(b.dataset.idx);   // orden del servidor: calificación
            });
            var sel = chosen();
            list.innerHTML = "";
            rows.forEach(function (c) {
                var li = document.createElement("li");
                var on = !!(sel && cardOf(sel) === c);
                li.className = "svb-all-i" + (on ? " is-selected" : "");
                var meta = [];
                meta.push(num(c.dataset.reviews) ? "★ " + num(c.dataset.rating).toFixed(1) + " (" + c.dataset.reviews + ")" : "Sin reseñas");
                if (c.dataset.zones) { meta.push(c.dataset.zones); }
                meta.push(c.dataset.until ? "Acreditada hasta " + c.dataset.until : "Acreditada");
                li.innerHTML = '<div class="svb-all-n"><b></b><span></span></div>' +
                    '<div class="svb-all-fee"><b></b><small></small></div>' +
                    '<button type="button" class="s-btn btn-sm"></button>';
                li.querySelector(".svb-all-n b").textContent = c.dataset.name;
                li.querySelector(".svb-all-n span").textContent = meta.join(" · ");
                li.querySelector(".svb-all-fee b").textContent = "$" + money(feeOf(c, qq));
                li.querySelector(".svb-all-fee small").textContent = c.dataset.src === "verifier"
                    ? "$" + money(num(c.dataset.base)) + " + $" + num(c.dataset.unit) + "/" + uom : "tarifa de la plataforma";
                var btn = li.querySelector("button");
                btn.className = "s-btn btn-sm " + (on ? "s-btn-primary" : "s-btn-ghost");
                btn.innerHTML = on ? '<i class="fa fa-check me-1"></i>Elegida' : "Elegir";
                btn.setAttribute("aria-pressed", on ? "true" : "false");
                btn.setAttribute("aria-label", (on ? "Elegida: " : "Elegir ") + c.dataset.name);
                btn.addEventListener("click", function () {
                    var r = c.querySelector('input[name="verifier_ref"]');
                    if (r) { r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); }
                    closeAll();
                    c.scrollIntoView({ behavior: "smooth", block: "center" });
                });
                list.appendChild(li);
            });
            if (empty) { empty.hidden = rows.length > 0; }
            if (countEl) { countEl.textContent = rows.length; }
        }
        function openAll() {
            if (!modal) { return; }
            opener = document.activeElement;
            renderAll();
            modal.classList.add("open");
            if (q) { q.focus(); }
        }
        function closeAll() {
            if (!modal) { return; }
            modal.classList.remove("open");
            if (opener && opener.focus) { opener.focus(); }
        }
        var more = $("svb_more");
        if (more) { more.addEventListener("click", openAll); }
        if (modal) {
            modal.querySelectorAll("[data-svb-close]").forEach(function (b) { b.addEventListener("click", closeAll); });
            modal.addEventListener("click", function (e) { if (e.target === modal) { closeAll(); } });
            [q, sortSel, fRev, fOwn].forEach(function (el) {
                if (el) { el.addEventListener(el.tagName === "INPUT" && el.type === "search" ? "input" : "change", renderAll); }
            });
            document.addEventListener("keydown", function (e) {
                if (e.key !== "Escape" || !modal.classList.contains("open")) { return; }
                // La ficha (ojito) o el visor del certificado abiertos encima: los cierran ellos.
                if (document.querySelector(".sv-info-modal.open") || ($("certModal") && $("certModal").classList.contains("open"))) { return; }
                closeAll();
            });
        }

        // ---------------- Desglose y envío ----------------
        var dlg = $("svModal");
        var btnVerify = $("btn_verify");
        function validQty(q) { return q > 0 && q <= available; }
        function abrirDesglose() {
            var q = qty();
            var m = mode();
            if (!validQty(q)) { say("Ingresa una cantidad válida (entre 1 y " + available + ") antes de continuar."); return; }
            if (!m) { say("Activa el servicio de verificación o compra directo."); return; }
            var sel = chosen();
            if (m === "platform" && !sel) {
                say("Elige una verificadora antes de continuar.");
                if (pBox) { pBox.scrollIntoView({ behavior: "smooth", block: "center" }); }
                return;
            }
            say("");
            var declarada = m === "declared";
            var c = cardOf(sel);
            var fee = declarada || !c ? 0 : feeOf(c, q);
            var feeRow = $("svFeeRow");
            if (feeRow) { feeRow.style.display = declarada ? "none" : ""; }
            var txt = $("svModalTxt");
            if (txt) {
                txt.innerHTML = declarada
                    ? "Verificación <strong>declarada por las partes</strong>: tú o el vendedor cargan el informe y la otra parte lo confirma. Sin honorario de verificación. Al confirmar se <strong>reserva la cantidad</strong> indicada."
                    : "Una verificadora acreditada irá a campo a inspeccionar el lote. Al confirmar se <strong>reserva la cantidad</strong> indicada y se cobra su honorario.";
            }
            $("svVerifLbl").textContent = declarada ? "Modo:" : "Verificadora:";
            $("svVerif").textContent = declarada ? "Verificación declarada por las partes" : (c ? c.dataset.name : "—");
            $("svQty").textContent = q;
            $("svProd").textContent = money(q * price);
            $("svFee").textContent = money(fee);
            $("svTotal").textContent = money(q * price + fee);
            var det = $("svFeeDetail");
            if (det && c) {
                det.textContent = "(" + (c.dataset.src === "verifier"
                    ? "$" + money(num(c.dataset.base)) + " + $" + num(c.dataset.unit) + " × " + q
                    : "tarifa de la plataforma") + ")";
            }
            if (dlg) { dlg.classList.add("open"); var ok = $("svConfirm"); if (ok) { ok.focus(); } }
        }
        if (btnVerify) { btnVerify.addEventListener("click", abrirDesglose); }
        var cancel = $("svCancel");
        if (cancel) { cancel.addEventListener("click", function () { if (dlg) { dlg.classList.remove("open"); } if (btnVerify) { btnVerify.focus(); } }); }
        if (dlg) { dlg.addEventListener("click", function (e) { if (e.target === dlg) { dlg.classList.remove("open"); } }); }
        var confirm = $("svConfirm");
        if (confirm) {
            confirm.addEventListener("click", function () {
                if (qtyVerify) { qtyVerify.value = qty(); }
                confirm.setAttribute("disabled", "disabled");
                if (dlg) { dlg.classList.remove("open"); }
                // Pago simulado (demo): 5 s antes de enviar.
                var overlay = $("payOverlay"), countEl2 = $("payCount");
                if (overlay) { overlay.style.display = "flex"; }
                var left = 5;
                if (countEl2) { countEl2.textContent = left; }
                var timer = setInterval(function () {
                    left -= 1;
                    if (countEl2) { countEl2.textContent = left; }
                    if (left <= 0) { clearInterval(timer); f.submit(); }
                }, 1000);
            });
        }
        f.addEventListener("submit", function (ev) {
            var q = qty();
            if (!validQty(q)) { ev.preventDefault(); say("Ingresa una cantidad válida (entre 1 y " + available + ") antes de enviar a verificación."); return; }
            if (qtyVerify) { qtyVerify.value = q; }
        });

        sync();
    }

    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); }
    else { init(); }
})();
