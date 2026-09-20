/* Shrimp Marketplace — animaciones de scroll (estilo Apple).
   Revela elementos .s-reveal cuando entran en viewport. Sin dependencias. */
(function () {
    "use strict";

    // Marca que el JS está activo: habilita el ocultamiento previo a la animación.
    // Si este archivo no cargara, .s-reveal queda visible (fallback seguro).
    document.documentElement.classList.add("s-js");

    function reveal() {
        var els = document.querySelectorAll(".s-reveal");
        if (!els.length) {
            return;
        }
        if (!("IntersectionObserver" in window)) {
            els.forEach(function (el) { el.classList.add("is-visible"); });
            return;
        }
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    io.unobserve(entry.target);
                }
            });
        }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });

        els.forEach(function (el) { io.observe(el); });
    }

    // Conteo animado para cifras con [data-count] (hero y banda de stats).
    function countUp() {
        var nums = document.querySelectorAll("[data-count]");
        if (!nums.length) { return; }
        var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        function animate(el) {
            var target = parseFloat(el.getAttribute("data-count"));
            var suffix = el.getAttribute("data-suffix") || "";
            if (isNaN(target) || reduce) { return; }
            var dur = 1100, start = null;
            function step(ts) {
                if (start === null) { start = ts; }
                var p = Math.min((ts - start) / dur, 1);
                var eased = 1 - Math.pow(1 - p, 3);          // easeOutCubic
                el.textContent = Math.round(target * eased) + suffix;
                if (p < 1) { requestAnimationFrame(step); }
            }
            requestAnimationFrame(step);
        }

        if (!("IntersectionObserver" in window)) {
            nums.forEach(animate);
            return;
        }
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    animate(entry.target);
                    io.unobserve(entry.target);
                }
            });
        }, { threshold: 0.5 });
        nums.forEach(function (el) { io.observe(el); });
    }

    // Combobox con buscador (.sf-combo): select filtrable sin texto libre.
    function initCombos() {
        var combos = document.querySelectorAll(".sf-combo");
        Array.prototype.forEach.call(combos, function (combo) {
            if (combo.dataset.comboInit) { return; }
            combo.dataset.comboInit = "1";

            var hidden = combo.querySelector("input[type=hidden]");
            var control = combo.querySelector(".sf-combo-control");
            var valueEl = combo.querySelector(".sf-combo-value");
            var search = combo.querySelector(".sf-combo-search");
            var empty = combo.querySelector(".sf-combo-empty");
            var list = combo.querySelector(".sf-combo-list");
            if (!hidden || !control || !valueEl || !list) { return; }
            var opts = Array.prototype.slice.call(list.querySelectorAll(".sf-combo-opt"));

            function close() { combo.classList.remove("is-open"); }
            function open() {
                combo.classList.add("is-open");
                if (search) { search.value = ""; filter(""); setTimeout(function () { search.focus(); }, 0); }
            }
            function selectOpt(opt) {
                hidden.value = opt.getAttribute("data-value");
                valueEl.textContent = opt.textContent;
                valueEl.classList.remove("is-placeholder");
                opts.forEach(function (o) { o.classList.remove("is-selected"); });
                opt.classList.add("is-selected");
                close();
            }
            function filter(q) {
                q = (q || "").toLowerCase().trim();
                var any = false;
                opts.forEach(function (o) {
                    var match = o.textContent.toLowerCase().indexOf(q) !== -1;
                    o.style.display = match ? "" : "none";
                    if (match) { any = true; }
                });
                if (empty) { empty.style.display = any ? "none" : "block"; }
            }

            control.addEventListener("click", function (e) {
                e.preventDefault();
                if (combo.classList.contains("is-open")) { close(); } else { open(); }
            });
            if (search) { search.addEventListener("input", function () { filter(search.value); }); }
            list.addEventListener("click", function (e) {
                var o = e.target.closest(".sf-combo-opt");
                if (o) { selectOpt(o); }
            });
            document.addEventListener("click", function (e) {
                if (!combo.contains(e.target)) { close(); }
            });

            // Preselección (formulario repoblado / edición): si el hidden ya trae valor.
            if (hidden.value) {
                var pre = opts.filter(function (o) { return o.getAttribute("data-value") === hidden.value; })[0];
                if (pre) { selectOpt(pre); }
            }
        });
    }

    function init() { reveal(); countUp(); initCombos(); }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
