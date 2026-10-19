/* «Estado real del lote»: modal «Registrar observación» (crear / corregir)
 * y confirmación de borrado. Modales de Bootstrap (bloquean el scroll del
 * fondo por sí mismos); los datos del lote llegan en los data-evo-* del
 * botón que abre el modal. Sin dependencias. */
(function () {
    "use strict";

    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function nowLocal() {
        var d = new Date();
        return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
            "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
    }
    function num(v) {
        if (v === null || v === undefined || String(v).trim() === "") { return null; }
        var n = parseFloat(String(v).replace(",", "."));
        return isNaN(n) ? NaN : n;
    }
    function fmt(n) {
        if (n === null || isNaN(n)) { return "—"; }
        return Number(n).toLocaleString("es-EC", {maximumFractionDigits: 2});
    }
    function setErr(input, on) {
        var f = input.closest(".pf-f");
        if (!f) { return; }
        f.classList.toggle("pf-err", !!on);
        var m = f.querySelector(".pf-emsg");
        if (m) { m.hidden = !on; }
        input.setAttribute("aria-invalid", on ? "true" : "false");
    }

    function initObsModal(modal) {
        var form = modal.querySelector("#evoObsForm");
        if (!form) { return; }
        var q = function (sel) { return form.querySelector(sel); };
        var stage = q("#evoStage");
        var qty = q("#evoQty");
        var warn = q(".evo-qty-warn");
        var publishedQty = 0;

        function qtyWarn() {
            var v = num(qty.value);
            warn.hidden = !(v !== null && !isNaN(v) && v > 0 && v < publishedQty);
        }
        qty.addEventListener("input", qtyWarn);

        modal.addEventListener("show.bs.modal", function (ev) {
            var t = ev.relatedTarget;
            if (!t || !t.dataset) { return; }
            var d = t.dataset;
            form.reset();
            Array.prototype.forEach.call(form.querySelectorAll(".pf-f"), function (f) {
                f.classList.remove("pf-err");
                var m = f.querySelector(".pf-emsg");
                if (m) { m.hidden = true; }
            });
            form.setAttribute("action", d.evoAction || "#");
            // Etapas del tipo de lote
            var tipos = (d.evoTipos || "").split(",").filter(Boolean);
            Array.prototype.forEach.call(stage.options, function (o) {
                if (!o.value) { return; }
                var ok = !tipos.length || tipos.indexOf(o.dataset.tipo) !== -1;
                o.hidden = !ok;
                o.disabled = !ok;
            });
            var unit = d.evoUnit || "mg";
            form.querySelector("[data-evo-unit]").textContent = unit;
            form.querySelector("[data-evo-size-lbl]").textContent = unit === "g" ? "Peso promedio" : "Talla promedio";
            publishedQty = parseFloat(d.evoQty || "0") || 0;
            form.querySelector("[data-evo-uom]").textContent = d.evoUom || "";
            form.querySelector("[data-evo-stock]").textContent = "publicado: " + fmt(publishedQty) + " " + (d.evoUom || "");
            form.querySelector("[data-evo-name]").textContent = d.evoName ? d.evoName + "." : "";
            form.querySelector("[data-evo-published]").textContent = d.evoStageName ? "Publicada: " + d.evoStageName : "";
            q("#evoDate").setAttribute("max", nowLocal());
            var obs = null;
            try { obs = d.evoObs ? JSON.parse(d.evoObs) : null; } catch (e) { obs = null; }
            if (obs) {
                q("#evoDate").value = obs.date || nowLocal();
                stage.value = obs.stage || "";
                q("#evoSize").value = obs.size;
                q("#evoSurv").value = obs.survival;
                q("#evoHealth").value = obs.health;
                q("#evoNote").value = obs.note;
                q("#evoTemp").value = obs.temp;
                q("#evoSal").value = obs.salinity;
                q("#evoO2").value = obs.oxygen;
                q("#evoPh").value = obs.ph;
                qty.value = obs.qty;
            } else {
                q("#evoDate").value = nowLocal();
                stage.value = d.evoStage || "";
                if (stage.selectedOptions[0] && stage.selectedOptions[0].disabled) { stage.value = ""; }
            }
            modal.querySelector("[data-evo-title]").textContent = obs ? "Corregir observación" : "Registrar observación";
            form.querySelector("[data-evo-save]").textContent = "Guardar";
            qtyWarn();
        });

        form.addEventListener("submit", function (ev) {
            var first = null;
            var mark = function (input, bad) {
                setErr(input, bad);
                if (bad && !first) { first = input; }
            };
            var date = q("#evoDate");
            var dv = date.value;
            // Una hora de margen (relojes desfasados), igual que el servidor.
            var future = dv && (new Date(dv).getTime() > Date.now() + 3600 * 1000);
            mark(date, !dv || future);
            mark(stage, !stage.value);
            Array.prototype.forEach.call(form.querySelectorAll("input[data-min]"), function (inp) {
                var v = num(inp.value);
                var bad = false;
                if (v !== null) {
                    var mn = parseFloat(inp.dataset.min);
                    var mx = inp.dataset.max !== undefined ? parseFloat(inp.dataset.max) : null;
                    bad = isNaN(v) || v < mn || (mx !== null && v > mx);
                }
                mark(inp, bad);
            });
            if (first) {
                ev.preventDefault();
                first.focus();
                return;
            }
            var btn = form.querySelector("#evoObsSave");
            if (btn) { btn.disabled = true; }
        });
        Array.prototype.forEach.call(form.querySelectorAll("input, select"), function (inp) {
            inp.addEventListener("input", function () {
                if (inp.closest(".pf-err")) { setErr(inp, false); }
            });
        });
    }

    function initDelModal(modal) {
        modal.addEventListener("show.bs.modal", function (ev) {
            var t = ev.relatedTarget;
            if (!t || !t.dataset) { return; }
            modal.querySelector("#evoDelForm").setAttribute("action", t.dataset.evoAction || "#");
            modal.querySelector("[data-evo-del-date]").textContent = t.dataset.evoDate || "";
        });
    }

    function init() {
        var obs = document.getElementById("evoObsModal");
        var del = document.getElementById("evoDelModal");
        // Al <body>: así ningún contenedor con transform/overflow los recorta.
        [obs, del].forEach(function (m) { if (m && m.parentNode !== document.body) { document.body.appendChild(m); } });
        if (obs) { initObsModal(obs); }
        if (del) { initDelModal(del); }
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
