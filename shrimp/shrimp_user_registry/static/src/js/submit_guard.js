/* Envío protegido (19.0.1.10.2): para formularios con data-shrimp-submit-guard.
 *
 * Cuando el envío SÍ sale (ningún script lo canceló: el asistente valida en
 * su propio listener «submit» del formulario y este va en el documento, que
 * corre después):
 *   - desactiva los botones de envío y bloquea un segundo envío (Enter, doble
 *     clic);
 *   - muestra una capa modal que no se puede cerrar (sin ×, Esc desactivado,
 *     el resto de la página queda inerte) con un spinner y el texto de
 *     data-guard-title / data-guard-text;
 *   - a los 60 s sin respuesta (data-guard-wait en ms) ofrece «Reintentar» y
 *     «Volver al formulario».
 * El formulario se envía igual que siempre (POST normal, sin XHR): por eso no
 * hay barra de progreso de la subida, solo el spinner.
 * Al volver con «Atrás» (bfcache, pageshow) todo vuelve a su estado inicial;
 * si el servidor responde con el formulario y un error, la página es nueva.
 */
(function () {
    "use strict";

    var SEL = "form[data-shrimp-submit-guard]";
    var WAIT_MS = 60000;
    var state = null;   // {form, overlay, timer, inerted: [], buttons: []}

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text != null) { e.textContent = text; }
        return e;
    }

    function buildOverlay(form) {
        var ov = el("div", "shrimp-sg");
        ov.setAttribute("role", "dialog");
        ov.setAttribute("aria-modal", "true");
        ov.setAttribute("aria-labelledby", "shrimpSgTitle");
        ov.setAttribute("aria-describedby", "shrimpSgText");
        var box = el("div", "shrimp-sg-box");
        box.tabIndex = -1;
        var spin = el("span", "shrimp-sg-spin");
        spin.setAttribute("aria-hidden", "true");
        var title = el("h2", "shrimp-sg-title", form.getAttribute("data-guard-title") || "Enviando…");
        title.id = "shrimpSgTitle";
        var text = el("p", "shrimp-sg-text", form.getAttribute("data-guard-text") ||
            "Estamos guardando tus datos. No cierres esta ventana.");
        text.id = "shrimpSgText";
        var live = el("p", "shrimp-sg-late");
        live.setAttribute("role", "status");
        live.setAttribute("aria-live", "polite");
        live.hidden = true;
        var acts = el("div", "shrimp-sg-acts");
        acts.hidden = true;
        var retry = el("button", "shrimp-sg-btn is-primary", "Reintentar");
        retry.type = "button";
        var back = el("button", "shrimp-sg-btn", "Volver al formulario");
        back.type = "button";
        acts.appendChild(retry);
        acts.appendChild(back);
        box.appendChild(spin);
        box.appendChild(title);
        box.appendChild(text);
        box.appendChild(live);
        box.appendChild(acts);
        ov.appendChild(box);

        retry.addEventListener("click", function () {
            try { window.stop(); } catch (e) { /* navegador antiguo */ }
            reset();
            if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
        });
        back.addEventListener("click", function () {
            try { window.stop(); } catch (e) { /* navegador antiguo */ }
            reset();
        });
        // No se cierra con Esc y el foco no sale de la capa.
        ov.addEventListener("keydown", function (e) {
            if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); return; }
            if (e.key === "Tab") {
                var f = Array.prototype.slice.call(box.querySelectorAll("button")).filter(function (b) {
                    return !b.closest("[hidden]");
                });
                if (!f.length) { e.preventDefault(); box.focus(); return; }
                var first = f[0], last = f[f.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                else if (f.indexOf(document.activeElement) === -1) { e.preventDefault(); first.focus(); }
            }
        });
        return {node: ov, box: box, live: live, acts: acts, retry: retry};
    }

    function lock(form) {
        var ov = buildOverlay(form);
        var buttons = Array.prototype.slice.call(form.querySelectorAll("button[type='submit'], input[type='submit']"));
        buttons.forEach(function (b) { b.disabled = true; b.setAttribute("aria-busy", "true"); });
        var inerted = Array.prototype.slice.call(document.body.children).filter(function (c) {
            return !c.inert && c.tagName !== "SCRIPT";
        });
        // Bloqueo de scroll compartido (shrimp_marketplace/.../shrimp_scroll_lock.js).
        if (window.ShrimpScrollLock) { window.ShrimpScrollLock.lock(ov.node); }
        document.body.appendChild(ov.node);
        inerted.forEach(function (c) { c.inert = true; });
        document.documentElement.classList.add("shrimp-sg-open");
        form.dataset.sgBusy = "1";
        state = {form: form, ov: ov, buttons: buttons, inerted: inerted, timer: null};
        state.timer = window.setTimeout(function () {
            if (!state) { return; }
            ov.node.classList.add("is-late");
            ov.live.textContent = "Está tardando más de lo normal. Puede ser la conexión: " +
                "reintenta o vuelve al formulario (tus datos siguen ahí).";
            ov.live.hidden = false;
            ov.acts.hidden = false;
            ov.retry.focus();
        }, parseInt(form.getAttribute("data-guard-wait"), 10) || WAIT_MS);
        // El foco entra en la capa (después de que el navegador procese el envío).
        window.setTimeout(function () { if (state) { ov.box.focus(); } }, 0);
    }

    function reset() {
        if (!state) {
            // Página restaurada sin estado (bfcache con la capa ya pintada).
            document.querySelectorAll(".shrimp-sg").forEach(function (n) { n.remove(); });
            document.querySelectorAll(SEL).forEach(function (f) {
                delete f.dataset.sgBusy;
                f.querySelectorAll("button[type='submit'], input[type='submit']").forEach(function (b) {
                    b.disabled = false; b.removeAttribute("aria-busy");
                });
            });
            document.documentElement.classList.remove("shrimp-sg-open");
            return;
        }
        var s = state;
        state = null;
        window.clearTimeout(s.timer);
        s.inerted.forEach(function (c) { c.inert = false; });
        s.buttons.forEach(function (b) { b.disabled = false; b.removeAttribute("aria-busy"); });
        if (s.ov.node.parentNode) { s.ov.node.parentNode.removeChild(s.ov.node); }
        if (window.ShrimpScrollLock) { window.ShrimpScrollLock.unlock(s.ov.node); }
        delete s.form.dataset.sgBusy;
        document.documentElement.classList.remove("shrimp-sg-open");
        var b = s.buttons[0];
        if (b && b.offsetParent) { b.focus(); }
    }

    // Captura: un envío mientras otro está en curso se cancela antes que nada.
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (form && form.matches && form.matches(SEL) && form.dataset.sgBusy) {
            e.preventDefault();
            e.stopImmediatePropagation();
        }
    }, true);
    // Burbuja: corre después de la validación del formulario.
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || !form.matches || !form.matches(SEL) || e.defaultPrevented) { return; }
        lock(form);
    });
    window.addEventListener("pageshow", function () { reset(); });

    // lock(form): muestra la capa sin enviar (otros scripts que envían por su cuenta).
    window.ShrimpSubmitGuard = {lock: function (form) { if (!state) { lock(form); } }, reset: reset};
})();
