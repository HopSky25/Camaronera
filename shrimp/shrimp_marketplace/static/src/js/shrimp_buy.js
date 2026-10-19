/* CamaronMarket — Página de compra (/marketplace/buy/<ref>).
 *
 * Lo que antes vivía en scripts sueltos de la plantilla buy_product:
 *   - total = cantidad × precio, avisos de cantidad (0 o más que lo
 *     disponible) y copia de la cantidad a los campos ocultos «qty» de los
 *     formularios (#qty_hidden_buy);
 *   - pago simulado de la compra directa (#buyForm + #payOverlay, 5 s).
 *   (El chequeo legado se retiró por completo: decisión D-28.)
 * Y lo nuevo del rediseño:
 *   - resumen del pedido (subtotal, servicio de la plataforma, «pagas
 *     ahora», «al concluir», total) según la forma de compra elegida;
 *   - un solo botón visible (#bp_cta y su gemelo móvil #bp_mcta) que dispara
 *     el envío real: #buyForm (compra directa) o #btn_verify (verificación,
 *     del módulo shrimp_verification, que abre su propio desglose).
 * Los nombres de los campos que viajan en los POST no cambian.
 */
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }
    // Mismo formato que el servidor (fmtq): coma de miles, punto decimal.
    function money(n) {
        return (isNaN(n) ? 0 : n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function fmtQty(n) {
        return (Math.round(n * 100) / 100).toLocaleString("en-US", { maximumFractionDigits: 2 });
    }

    function init() {
        var page = $("bp_page");
        if (!page || page.dataset.bpInit) { return; }
        page.dataset.bpInit = "1";

        var qtyInput = $("qty_input");
        var priceEl = $("unit_price");
        var availEl = $("available_qty");
        if (!qtyInput || !priceEl || !availEl) { return; }
        page.classList.add("bp-js");

        var price = parseFloat(priceEl.dataset.price || "0") || 0;
        var available = parseFloat(availEl.dataset.available || "0") || 0;

        var buyForm = $("buyForm");
        var btnBuy = $("btn_buy");
        var hidBuy = $("qty_hidden_buy");
        var vForm = document.querySelector('form[action$="/verify"]');
        var btnVerify = $("btn_verify");
        var cfgEl = $("svFeeCfg");
        // La tarifa se lee en cada cálculo: shrimp_verification la cambia por
        // la de la verificadora elegida (D-40, tarifa propia de cada una).
        function readCfg() {
            return cfgEl ? {
                base: parseFloat(cfgEl.dataset.base || "0") || 0,
                cents: parseFloat(cfgEl.dataset.cents || "0") || 0,
                min: parseFloat(cfgEl.dataset.min || "0") || 0,
                max: parseFloat(cfgEl.dataset.max || "0") || 0,
                selected: cfgEl.dataset.selected,
            } : null;
        }

        var cta = $("bp_cta");
        var mcta = $("bp_mcta");
        var why = $("bp_cta_why");
        var msgEl = $("buyMsg");
        var qtyField = $("bp_qty_f");
        var bar = $("bp_bar_fill");

        function qty() { return parseFloat(qtyInput.value || "0") || 0; }

        function mode() {
            if (!vForm) { return ""; }
            var m = vForm.querySelector('input[name="verification_mode"]:checked');
            return m ? m.value : "";
        }

        // Mismo cálculo que el servidor (shrimp.verification.fee.compute).
        function feeFor(q) {
            var cfg = readCfg();
            if (!cfg) { return 0; }
            var v = cfg.base + (q * cfg.cents / 100);
            if (cfg.min) { v = Math.max(v, cfg.min); }
            if (cfg.max) { v = Math.min(v, cfg.max); }
            return Math.round(v * 100) / 100;
        }

        // 'direct' | 'platform' | 'declared' | 'choose' (verificación
        // obligatoria sin modo elegido todavía).
        function path() {
            var m = mode();
            if (m) { return m; }
            if (buyForm) { return "direct"; }
            return vForm ? "choose" : "none";
        }

        function say(text) {
            if (!msgEl) { return; }
            msgEl.textContent = text;
            msgEl.style.display = text ? "block" : "none";
            if (text) { msgEl.scrollIntoView({ behavior: "smooth", block: "center" }); }
        }

        function setText(id, v) { var el = $(id); if (el) { el.textContent = v; } }

        function recalc() {
            var q = qty();
            var over = q > available;
            var zero = q <= 0;
            var valid = !over && !zero;

            if (hidBuy) { hidBuy.value = q; }

            var sub = q * price;
            setText("total_value", money(sub));
            setText("bp_s_qty", q > 0 ? fmtQty(q) : "—");

            var err = $("qty_error"), zeroErr = $("qty_zero_error");
            var touched = qtyInput.value !== "";
            if (err) { err.classList.toggle("d-none", !over); }
            if (zeroErr) { zeroErr.classList.toggle("d-none", !(zero && touched)); }
            if (qtyField) { qtyField.classList.toggle("pf-err", over || (zero && touched)); }
            qtyInput.setAttribute("aria-invalid", (over || (zero && touched)) ? "true" : "false");
            if (bar) {
                bar.style.width = (available > 0 ? Math.min(100, (q / available) * 100) : 0) + "%";
                bar.parentNode.classList.toggle("is-over", over);
            }

            // Botones reales (como antes): deshabilitados con cantidad inválida.
            [btnBuy].forEach(function (b) {
                if (!b) { return; }
                if (valid) { b.removeAttribute("disabled"); } else { b.setAttribute("disabled", "disabled"); }
            });

            // Resumen según la forma de compra.
            var p = path();
            var fee = 0, now = sub, later = 0;
            var feeLbl = "Servicio de la plataforma";
            var feeNote = "Sin cargo para ti: la comisión de la venta la paga el vendedor.";
            var feeTxt = "$" + money(0);
            var label = "Pagar y comprar";
            if (p === "platform") {
                var elegida = !cfgEl || cfgEl.dataset.selected !== "";
                fee = valid && elegida ? feeFor(q) : 0;
                feeLbl = "Honorario de verificación";
                feeNote = elegida ? "Tarifa de la verificadora elegida. Se cobra al enviar y lo factura la plataforma."
                                  : "Elige una verificadora para ver su honorario.";
                feeTxt = valid && elegida ? "$" + money(fee) : "—";
                now = fee; later = sub;
                label = "Enviar a verificación";
            } else if (p === "declared") {
                feeLbl = "Verificación declarada";
                feeNote = "Sin honorario de verificación.";
                now = 0; later = sub;
                label = "Enviar a verificación";
            } else if (p === "choose") {
                feeLbl = "Verificación en campo";
                feeNote = "Elige cómo se verifica para ver el costo.";
                feeTxt = "—";
                now = 0; later = sub;
                label = "Enviar a verificación";
            }
            var total = sub + fee;
            setText("bp_s_fee_lbl", feeLbl);
            setText("bp_s_fee_note", feeNote);
            setText("bp_s_fee", feeTxt);
            setText("bp_s_now", "$" + money(now));
            setText("bp_s_later", "$" + money(later));
            var laterRow = $("bp_s_later_row");
            if (laterRow) { laterRow.hidden = !(p === "platform" || p === "declared" || p === "choose"); }
            setText("bp_s_total", money(total));
            setText("bp_m_total", money(total));
            setText("bp_m_sub", valid ? (fmtQty(q) + " · pagas ahora $" + money(now))
                                      : (over ? "Supera lo disponible" : "Ingresa la cantidad"));

            if (cta) {
                var t = cta.querySelector(".bp-cta-t");
                if (t) { t.textContent = label; }
                cta.disabled = !valid;
            }
            if (mcta) { mcta.disabled = !valid; }
            if (why) {
                why.textContent = over ? "La cantidad supera lo disponible."
                    : zero ? "Ingresa la cantidad para continuar."
                    : (p === "direct" ? "La compra se confirma al pagar."
                       : "La cantidad queda reservada; el producto se paga al concluir.");
                why.classList.toggle("is-bad", over);
            }
        }

        function go() {
            var q = qty();
            if (!q || q <= 0) { say("Ingresa una cantidad válida antes de pagar."); qtyInput.focus(); return; }
            if (q > available) { say("La cantidad supera lo disponible."); qtyInput.focus(); return; }
            say("");
            var p = path();
            if (p === "direct" && buyForm) {
                if (buyForm.requestSubmit) { buyForm.requestSubmit(btnBuy || undefined); }
                else if (btnBuy) { btnBuy.click(); }
            } else if (btnVerify) {
                btnVerify.removeAttribute("disabled");
                btnVerify.click();
            }
        }

        qtyInput.addEventListener("input", recalc);
        document.addEventListener("change", function (ev) {
            if (ev.target && (ev.target.name === "verification_mode" || ev.target.name === "verifier_ref")) { recalc(); }
        });
        document.addEventListener("svb:fee", recalc);
        if (cta) { cta.addEventListener("click", go); }
        if (mcta) { mcta.addEventListener("click", go); }
        var all = $("bp_qty_all");
        if (all) {
            all.addEventListener("click", function () {
                qtyInput.value = all.dataset.qty || available;
                qtyInput.dispatchEvent(new Event("input", { bubbles: true }));
            });
        }

        // ---- Compra directa: pago simulado (5 s) y envío ----
        if (buyForm) {
            buyForm.addEventListener("submit", function (ev) {
                if (buyForm.dataset.paid === "1") { return; }
                ev.preventDefault();
                var q = parseFloat((hidBuy && hidBuy.value) || "0");
                if (!q || q <= 0) { say("Ingresa una cantidad válida antes de pagar."); return; }
                var overlay = $("payOverlay");
                var countEl = $("payCount");
                if (btnBuy) { btnBuy.disabled = true; }
                if (cta) { cta.disabled = true; }
                if (mcta) { mcta.disabled = true; }
                if (overlay) { overlay.style.display = "flex"; }
                var left = 5;
                if (countEl) { countEl.textContent = left; }
                var timer = setInterval(function () {
                    left -= 1;
                    if (countEl) { countEl.textContent = left; }
                    if (left <= 0) {
                        clearInterval(timer);
                        buyForm.dataset.paid = "1";
                        buyForm.submit();
                    }
                }, 1000);
            });
        }

        // Error del servidor: llevarlo a la vista.
        var errBox = $("bp_error");
        if (errBox) { errBox.focus({ preventScroll: true }); }

        recalc();
    }

    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); }
    else { init(); }
})();
