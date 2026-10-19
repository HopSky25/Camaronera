/* Acordeón de documentos legales (19.0.1.9.0).
 *
 * Lo usan el asistente de alta (signup_legal_documents) y la página de
 * aceptación (/my/legal/accept): ambos pintan shrimp_user_registry.legal_doc_item.
 *
 * - Todos cerrados al cargar; abrir uno cierra los demás.
 * - La casilla está fuera del botón que despliega: marcarla no abre ni cierra.
 * - «Acepto» marca la casilla y abre el siguiente documento pendiente.
 * - «He leído y acepto todos» marca o desmarca los VISIBLES (el servidor
 *   sigue guardando una aceptación por documento).
 * Sin JS se ve todo desplegado y el formulario funciona igual.
 */
(function () {
    "use strict";

    function visible(el) { return !el.closest(".d-none"); }

    function setup(box) {
        if (box.dataset.accReady) { return; }
        box.dataset.accReady = "1";
        var items = function () {
            return Array.prototype.slice.call(box.querySelectorAll(".shrimp-legal-item")).filter(visible);
        };
        var all = box.querySelector(".shrimp-legal-all input");
        var progress = box.querySelector(".shrimp-legal-progress");

        function panelOf(item) { return item.querySelector(".shrimp-legal-panel"); }
        function toggleOf(item) { return item.querySelector(".shrimp-legal-toggle"); }
        function checkOf(item) { return item.querySelector(".shrimp-legal-check"); }

        function setOpen(item, open) {
            var p = panelOf(item), t = toggleOf(item);
            if (!p || !t) { return; }
            p.hidden = !open;
            t.setAttribute("aria-expanded", open ? "true" : "false");
            item.classList.toggle("is-open", open);
        }
        function openOnly(item) {
            Array.prototype.slice.call(box.querySelectorAll(".shrimp-legal-item")).forEach(function (it) {
                setOpen(it, it === item);
            });
        }
        function refresh() {
            var vis = items(), ok = 0;
            vis.forEach(function (it) {
                var c = checkOf(it), on = !!(c && c.checked);
                if (on) { ok++; }
                it.classList.toggle("is-accepted", on);
                var ico = it.querySelector(".shrimp-legal-status i");
                if (ico) { ico.className = on ? "fa fa-check-circle" : "fa fa-circle-o"; }
            });
            if (all) {
                all.checked = vis.length > 0 && ok === vis.length;
                all.indeterminate = ok > 0 && ok < vis.length;
            }
            if (progress) {
                progress.textContent = vis.length ? (ok + " de " + vis.length + " aceptados") : "";
                progress.classList.toggle("is-done", vis.length > 0 && ok === vis.length);
            }
        }

        Array.prototype.slice.call(box.querySelectorAll(".shrimp-legal-item")).forEach(function (it) {
            setOpen(it, false);
            var t = toggleOf(it), c = checkOf(it), okBtn = it.querySelector(".shrimp-legal-ok");
            if (t) {
                t.addEventListener("click", function () {
                    var abierto = t.getAttribute("aria-expanded") === "true";
                    if (abierto) { setOpen(it, false); } else { openOnly(it); }
                });
            }
            if (c) {
                c.addEventListener("click", function (e) { e.stopPropagation(); });
                c.addEventListener("change", function () { c.setCustomValidity(""); refresh(); });
            }
            if (okBtn) {
                okBtn.addEventListener("click", function () {
                    if (c) { c.checked = true; c.dispatchEvent(new Event("change", {bubbles: true})); }
                    var vis = items(), i = vis.indexOf(it);
                    var next = vis.slice(i + 1).concat(vis.slice(0, i)).filter(function (x) {
                        var cx = checkOf(x); return cx && !cx.checked;
                    })[0];
                    if (next) {
                        openOnly(next);
                        var tn = toggleOf(next);
                        if (tn) { tn.focus(); tn.scrollIntoView({block: "nearest", behavior: "smooth"}); }
                    } else {
                        setOpen(it, false);
                        if (t) { t.focus(); }
                    }
                });
            }
        });
        if (all) {
            all.addEventListener("change", function () {
                items().forEach(function (it) {
                    var c = checkOf(it);
                    if (c) { c.checked = all.checked; c.setCustomValidity(""); }
                });
                refresh();
            });
        }
        box.shrimpLegal = {
            refresh: refresh,
            /* Documentos visibles obligatorios sin marcar: [{item, label}]. */
            pending: function () {
                return items().filter(function (it) {
                    var c = checkOf(it);
                    return c && !c.checked && c.required;
                }).map(function (it) { return {item: it, label: it.getAttribute("data-label") || ""}; });
            },
            open: openOnly,
        };
        refresh();
    }

    function init() {
        document.querySelectorAll(".shrimp-legal-docs").forEach(setup);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
    window.shrimpLegalAccordionInit = init;
})();
