/* CamaronMarket — cajón de filtros reutilizable (patrón de /marketplace).

   Lo usan todas las páginas que llaman a las plantillas
   shrimp_marketplace.s_filter_toolbar / s_filter_tags / s_filter_drawer.
   Sin dependencias; todo se configura con atributos en el HTML:

   #sFilterDrawer  .s-drawer           el cajón (role=dialog dentro)
   #sFilterPanel   form.s-drawer-form  el formulario del cajón
                   data-keep="q,sort"  campos que «Limpiar» no vacía
   #sFilterApply   botón «Ver N …» / «Aplicar»
                   data-count-url      ruta JSON {"count": N} (sin ella: «Aplicar»)
                   data-total          conteo de la página (punto de partida)
                   data-one / data-many / data-label   textos del botón
   #sMktSearch     búsqueda de la barra superior (viaja con los filtros)
   .js-filter-open botones que abren el cajón (sin JS: ancla :target)
   select[data-s-dep="campo"]  opciones con data-dep: solo las que casan con
                   el valor elegido en «campo» (p. ej. tallas según presentación)
   [data-s-clears="a,b"]       al cambiar este campo se vacían los campos a, b
                   (p. ej. el estadío al cambiar de tipo)
   .s-view-toggle[data-view-key][data-view-target][data-view-path]
                   selector Lista/Cuadrícula: cambia al instante, se recuerda en
                   localStorage y se conserva en enlaces (a[data-vista-link]) y
                   formularios (.js-vista-input).
*/
(function () {
    "use strict";

    function initView() {
        var toggle = document.querySelector(".s-view-toggle[data-view-key]");
        if (!toggle) { return; }
        var KEY = toggle.getAttribute("data-view-key");
        var path = toggle.getAttribute("data-view-path") || window.location.pathname;
        var grid = document.getElementById(toggle.getAttribute("data-view-target") || "");
        var btns = toggle.querySelectorAll(".js-vista-btn");
        if (!btns.length) { return; }
        function stored() { try { return window.localStorage.getItem(KEY); } catch (e) { return null; } }
        function store(v) { try { window.localStorage.setItem(KEY, v); } catch (e) { /* sin almacenamiento */ } }
        function withVista(href, v) {
            try {
                var u = new URL(href, window.location.href);
                if (u.pathname !== path) { return href; }
                u.searchParams.set("vista", v);
                return u.pathname + u.search + u.hash;
            } catch (e) { return href; }
        }
        function apply(v, pushUrl) {
            var lista = v === "lista";
            if (grid) { grid.classList.toggle("s-products--list", lista); }
            btns.forEach(function (b) {
                var on = b.getAttribute("data-vista") === v;
                b.classList.toggle("is-active", on);
                b.setAttribute("aria-pressed", on ? "true" : "false");
            });
            document.querySelectorAll(".js-vista-input").forEach(function (i) { i.value = v; });
            document.querySelectorAll("a[data-vista-link]").forEach(function (a) {
                a.setAttribute("href", withVista(a.getAttribute("href"), v));
            });
            if (pushUrl && window.history && window.history.replaceState) {
                window.history.replaceState(window.history.state, "", withVista(window.location.href, v));
            }
        }
        var fromUrl = new URLSearchParams(window.location.search).get("vista");
        var valid = fromUrl === "lista" || fromUrl === "grid";
        var actual = valid ? fromUrl : (stored() === "lista" ? "lista" : "grid");
        if (valid) { store(fromUrl); }
        if (fromUrl || actual === "lista") { apply(actual, false); }
        btns.forEach(function (b) {
            b.addEventListener("click", function (ev) {
                ev.preventDefault();
                var v = b.getAttribute("data-vista");
                store(v);
                apply(v, true);
            });
        });
    }

    function initDrawer() {
        var drawer = document.getElementById("sFilterDrawer");
        var form = document.getElementById("sFilterPanel");
        if (!drawer || !form || drawer.getAttribute("data-s-ready")) { return; }
        drawer.setAttribute("data-s-ready", "1");
        document.documentElement.classList.add("s-js");
        var panel = drawer.querySelector(".s-drawer-panel");
        var apply = document.getElementById("sFilterApply");
        var live = document.getElementById("sFilterLive");
        var search = document.getElementById("sMktSearch");
        var openers = document.querySelectorAll(".js-filter-open");
        var countUrl = apply ? apply.getAttribute("data-count-url") : "";
        var initialCount = apply ? apply.getAttribute("data-total") : "";
        var one = (apply && apply.getAttribute("data-one")) || "resultado";
        var many = (apply && apply.getAttribute("data-many")) || "resultados";
        var plain = (apply && apply.getAttribute("data-label")) || ("Ver " + many);
        var keep = (form.getAttribute("data-keep") || "q,sort,vista").split(",");
        var lastFocus = null, timer = null, ctrl = null, isOpen = false;

        // ---- Opciones que dependen de otro campo (tallas según presentación) ----
        function valueOf(name) {
            var r = form.querySelector('input[name="' + name + '"]:checked');
            if (r) { return r.value; }
            var s = form.querySelector('select[name="' + name + '"]');
            return s ? s.value : "";
        }
        function syncDeps() {
            form.querySelectorAll("select[data-s-dep]").forEach(function (sel) {
                var dep = valueOf(sel.getAttribute("data-s-dep"));
                for (var i = 0; i < sel.options.length; i++) {
                    var op = sel.options[i];
                    if (!op.value) { op.hidden = false; continue; }
                    var match = !dep || op.getAttribute("data-dep") === dep;
                    op.hidden = !match;
                    op.disabled = !match;
                    if (!match && op.selected) { sel.value = ""; }
                }
            });
        }

        // ---- Botón «Ver N …» con el conteo en vivo ----
        function label(n) {
            n = parseInt(n, 10);
            if (isNaN(n)) { return plain; }
            if (n === 1) { return "Ver 1 " + one; }
            return "Ver " + n.toLocaleString("es") + " " + many;
        }
        function setCount(n) {
            if (!apply || !countUrl) { return; }
            apply.textContent = label(n);
            apply.classList.toggle("is-zero", parseInt(n, 10) === 0);
            if (live) {
                live.textContent = parseInt(n, 10) === 0 ? "Sin " + many + " con estos filtros" : label(n).replace("Ver ", "");
            }
        }
        function params() {
            var p = new URLSearchParams();
            new FormData(form).forEach(function (v, k) {
                if (v && k !== "vista" && k !== "best") { p.append(k, v); }
            });
            if (search) {
                var n = search.getAttribute("name") || "q";
                p.delete(n);
                if (search.value.trim()) { p.set(n, search.value.trim()); }
            }
            return p;
        }
        function refreshCount() {
            if (!countUrl) { return; }
            clearTimeout(timer);
            timer = setTimeout(function () {
                if (ctrl) { ctrl.abort(); }
                ctrl = window.AbortController ? new AbortController() : null;
                if (apply) { apply.setAttribute("aria-busy", "true"); }
                fetch(countUrl + "?" + params().toString(), {
                    headers: {"X-Requested-With": "XMLHttpRequest"},
                    credentials: "same-origin",
                    signal: ctrl ? ctrl.signal : undefined
                }).then(function (r) { return r.json(); }).then(function (d) {
                    setCount(d.count);
                }).catch(function () { /* abortado o sin red: se queda el último */ }).then(function () {
                    if (apply) { apply.removeAttribute("aria-busy"); }
                });
            }, 250);
        }

        // ---- Abrir / cerrar ----
        function focusables() {
            return Array.prototype.filter.call(panel.querySelectorAll(
                'a[href]:not([tabindex="-1"]), button:not([disabled]):not([tabindex="-1"]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea'),
                function (el) { return el.getClientRects().length > 0; });
        }
        function lockScroll(on) {
            var root = document.documentElement;
            if (on) {
                var sb = window.innerWidth - root.clientWidth;
                root.classList.add("s-drawer-lock");
                if (sb > 0) { document.body.style.paddingRight = sb + "px"; }
            } else {
                root.classList.remove("s-drawer-lock");
                document.body.style.paddingRight = "";
            }
        }
        function syncSearch() {
            if (!search) { return; }
            var hq = form.querySelector('input[name="' + (search.getAttribute("name") || "q") + '"]');
            if (hq) { hq.value = search.value.trim(); }
        }
        function open(ev) {
            if (ev) { ev.preventDefault(); }
            if (isOpen) { return; }
            isOpen = true;
            lastFocus = document.activeElement;
            syncSearch();
            drawer.classList.add("is-open");
            openers.forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
            lockScroll(true);
            setCount(initialCount);
            if (search && search.value.trim() !== (search.defaultValue || "").trim()) { refreshCount(); }
            setTimeout(function () { panel.focus(); }, 30);
            document.addEventListener("keydown", onKey, true);
        }
        function close(ev, keepChanges) {
            if (ev) { ev.preventDefault(); }
            if (!isOpen) { return; }
            isOpen = false;
            clearTimeout(timer);
            if (ctrl) { ctrl.abort(); }
            drawer.classList.remove("is-open");
            openers.forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
            lockScroll(false);
            document.removeEventListener("keydown", onKey, true);
            if (!keepChanges) {
                // Cerrar sin aplicar descarta los cambios del cajón.
                form.reset();
                syncDeps();
            }
            if (window.location.hash === "#sFilterDrawer" && window.history.replaceState) {
                window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
            }
            if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
        }
        function onKey(ev) {
            if (ev.key === "Escape" || ev.key === "Esc") { close(ev); return; }
            if (ev.key !== "Tab") { return; }
            var f = focusables();
            if (!f.length) { ev.preventDefault(); return; }
            var first = f[0], last = f[f.length - 1], act = document.activeElement;
            if (ev.shiftKey && (act === first || act === panel || !panel.contains(act))) { ev.preventDefault(); last.focus(); }
            else if (!ev.shiftKey && (act === last || !panel.contains(act))) { ev.preventDefault(); first.focus(); }
        }
        openers.forEach(function (b) { b.addEventListener("click", open); });
        drawer.querySelectorAll(".js-filter-close").forEach(function (b) {
            b.addEventListener("click", function (ev) { close(ev); });
        });

        // «Limpiar»: vacía la selección del cajón (sin aplicar todavía).
        var reset = drawer.querySelector(".js-filter-reset");
        if (reset) {
            reset.addEventListener("click", function (ev) {
                ev.preventDefault();
                form.querySelectorAll("input, select").forEach(function (el) {
                    var n = el.name;
                    if (!n || keep.indexOf(n) !== -1) { return; }
                    if (el.type === "radio") { el.checked = el.value === ""; }
                    else if (el.type === "checkbox") { el.checked = false; }
                    else if (el.tagName === "SELECT") { el.value = ""; }
                    else if (el.type !== "submit" && el.type !== "button") { el.value = ""; }
                });
                syncDeps();
                refreshCount();
            });
        }

        form.addEventListener("change", function (ev) {
            var t = ev.target;
            syncDeps();
            var clears = t && t.getAttribute && t.getAttribute("data-s-clears");
            if (clears) {
                clears.split(",").forEach(function (n) {
                    form.querySelectorAll('input[name="' + n.trim() + '"]').forEach(function (el) {
                        if (el.type === "radio" || el.type === "checkbox") { el.checked = el.value === ""; }
                        else { el.value = ""; }
                    });
                });
            }
            refreshCount();
        });
        form.addEventListener("input", function (ev) {
            if (ev.target && ev.target.tagName === "INPUT" && /^(text|number|search|date)$/.test(ev.target.type)) { refreshCount(); }
        });
        // Al aplicar: la búsqueda escrita arriba viaja con los filtros y los
        // campos vacíos no ensucian la URL.
        form.addEventListener("submit", function () {
            syncSearch();
            form.querySelectorAll("input, select").forEach(function (el) {
                if (el.name && !el.value && el.type !== "submit" && !el.disabled) {
                    el.disabled = true;
                    el.setAttribute("data-s-off", "1");
                }
            });
            if (isOpen) { drawer.classList.remove("is-open"); lockScroll(false); }
        });
        window.addEventListener("pageshow", function () {
            form.querySelectorAll("[data-s-off]").forEach(function (el) {
                el.disabled = false;
                el.removeAttribute("data-s-off");
            });
            syncDeps();
        });
        syncDeps();
        if (window.location.hash === "#sFilterDrawer") { open(); }
    }

    function init() {
        initView();
        initDrawer();
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
