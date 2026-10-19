/* Mi cuenta (/marketplace/my-account), 19.0.1.28.0.
 *
 * Sin este script la página funciona igual (todas las tarjetas abiertas; los
 * formularios de la empresa, perfiles y certificados se envían normal). Con él:
 *   1. Navegación lateral: marca la sección visible (y la abre en el móvil).
 *   2. Móvil (< 992 px): tarjetas plegables. Abiertas la primera, la del
 *      ancla (#instalaciones...) y las que traen un aviso; el resto, plegadas.
 *   3. «Instalaciones y piscinas»: modales de agregar/editar instalación,
 *      agregar/editar piscina o tanque y confirmar «Archivar». Son
 *      formularios reales que envían a /marketplace/my-facilities/... con
 *      next=account; aquí solo se rellenan y se valida lo básico antes de
 *      enviar (el servidor valida todo otra vez).
 * Modal: el mismo patrón del alta (window.ShrimpSignupUI.Dialog si está
 * cargado; si no, Bootstrap Modal con plan B: foco atrapado, Esc, clic fuera,
 * foco de vuelta). Bloqueo de scroll: el de Bootstrap o, en el plan B,
 * window.ShrimpScrollLock si existe (o la clase modal-open).
 */
(function () {
    "use strict";

    var MOBILE = window.matchMedia ? window.matchMedia("(max-width: 991.98px)") : {matches: false};

    function Dialog(node) {
        if (window.ShrimpSignupUI && window.ShrimpSignupUI.Dialog) { return window.ShrimpSignupUI.Dialog(node); }
        var M = window.Modal || (window.bootstrap && window.bootstrap.Modal);
        var inst = M && M.getOrCreateInstance ? M.getOrCreateInstance(node, {backdrop: true, keyboard: true, focus: true}) : null;
        var opener = null, backdrop = null;
        var lock = window.ShrimpScrollLock;
        node.addEventListener("hidden.bs.modal", function () {
            if (opener && opener.focus && document.contains(opener)) { opener.focus(); }
        });
        function focusables() {
            return Array.prototype.slice.call(node.querySelectorAll(
                "button, [href], input:not([type=hidden]):not([readonly]), select, textarea, [tabindex]:not([tabindex='-1'])"))
                .filter(function (x) { return !x.disabled && x.offsetParent !== null; });
        }
        function fbHide() {
            node.classList.remove("show");
            node.style.display = "none";
            node.setAttribute("aria-hidden", "true");
            if (lock && lock.unlock) { lock.unlock(node); } else { document.body.classList.remove("modal-open"); }
            if (backdrop) { backdrop.remove(); backdrop = null; }
            node.dispatchEvent(new Event("hidden.bs.modal"));
        }
        if (!inst) {
            node.addEventListener("keydown", function (e) {
                if (e.key === "Escape") { fbHide(); return; }
                if (e.key === "Tab") {
                    var f = focusables(); if (!f.length) { return; }
                    var first = f[0], last = f[f.length - 1];
                    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                }
            });
            node.addEventListener("click", function (e) {
                if (e.target === node || (e.target.closest && e.target.closest("[data-ma-close]"))) { fbHide(); }
            });
        }
        return {
            node: node,
            show: function (from) {
                opener = from || document.activeElement;
                if (inst) { inst.show(); return; }
                backdrop = document.createElement("div");
                backdrop.className = "modal-backdrop fade show";
                document.body.appendChild(backdrop);
                node.style.display = "block";
                node.removeAttribute("aria-hidden");
                if (lock && lock.lock) { lock.lock(node); } else { document.body.classList.add("modal-open"); }
                node.classList.add("show");
                var f = focusables(); if (f.length) { f[0].focus(); }
                node.dispatchEvent(new Event("shown.bs.modal"));
            },
            hide: function () { if (inst) { inst.hide(); } else { fbHide(); } },
        };
    }

    /* ------------------------------------------------ tarjetas y navegación */
    function initCards(page) {
        var cards = Array.prototype.slice.call(page.querySelectorAll(".ma-card"));
        var links = Array.prototype.slice.call(page.querySelectorAll(".ma-nav-a"));
        function setOpen(card, open) {
            card.classList.toggle("is-collapsed", !open);
            var tg = card.querySelector(".ma-card-tg");
            if (tg) { tg.setAttribute("aria-expanded", open ? "true" : "false"); }
        }
        cards.forEach(function (card) {
            var head = card.querySelector(".ma-card-h");
            if (!head || !card.querySelector(".ma-card-b")) { return; }
            head.addEventListener("click", function (e) {
                if (!MOBILE.matches) { return; }
                var t = e.target;
                if (t.closest("a, button:not(.ma-card-tg), input, select, label")) { return; }
                setOpen(card, card.classList.contains("is-collapsed"));
            });
        });
        if (MOBILE.matches) {
            var hash = (location.hash || "").slice(1);
            cards.forEach(function (card, i) {
                if (!card.querySelector(".ma-card-b")) { return; }
                var keep = i === 0 || card.id === hash || !!card.querySelector(".pf-alert, .is-focus");
                setOpen(card, keep);
            });
        }
        function openTarget(id) {
            var card = id && document.getElementById(id);
            if (card && card.classList.contains("ma-card")) { setOpen(card, true); }
        }
        links.forEach(function (a) {
            a.addEventListener("click", function () { openTarget((a.getAttribute("href") || "").slice(1)); });
        });
        page.querySelectorAll('a[href^="#"]:not(.ma-nav-a)').forEach(function (a) {
            a.addEventListener("click", function () { openTarget((a.getAttribute("href") || "").slice(1)); });
        });
        window.addEventListener("hashchange", function () { openTarget(location.hash.slice(1)); });

        // Sección visible -> enlace marcado.
        if ("IntersectionObserver" in window && links.length) {
            var visible = {};
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (en) { visible[en.target.id] = en.isIntersecting ? en.intersectionRatio : 0; });
                var best = null, ratio = 0;
                cards.forEach(function (c) { if ((visible[c.id] || 0) > ratio) { ratio = visible[c.id]; best = c.id; } });
                if (!best) { return; }
                links.forEach(function (a) {
                    var on = a.getAttribute("href") === "#" + best;
                    a.classList.toggle("is-current", on);
                    if (on) { a.setAttribute("aria-current", "true"); } else { a.removeAttribute("aria-current"); }
                });
            }, {rootMargin: "-90px 0px -45% 0px", threshold: [0, 0.1, 0.25, 0.5, 0.75, 1]});
            cards.forEach(function (c) { if (c.id) { io.observe(c); } });
        }
    }

    /* ------------------------------------------------ instalaciones */
    function num(v) {
        var n = parseFloat(String(v == null ? "" : v).replace(",", "."));
        return isFinite(n) ? n : 0;
    }
    function fmt(n, d) {
        return n.toLocaleString("es-EC", {minimumFractionDigits: d, maximumFractionDigits: d});
    }
    function showErr(el, msg) {
        if (!el) { return; }
        el.textContent = msg || "";
        el.hidden = !msg;
    }

    function initFacilities(sec) {
        var facNode = document.getElementById("maFacModal");
        var pondNode = document.getElementById("maPondModal");
        var archNode = document.getElementById("maArchModal");
        // Los modales van al final del <body>: ningún contenedor con
        // transform/overflow los recorta y no quedan dentro de otro form.
        [facNode, pondNode, archNode].forEach(function (n) { if (n) { document.body.appendChild(n); } });

        // Resaltar la instalación recién guardada (?fac=<ref>).
        var focus = sec.getAttribute("data-focus");
        if (focus) {
            var art = document.getElementById("fac-" + focus);
            if (art) {
                art.classList.add("is-focus");
                setTimeout(function () { art.classList.remove("is-focus"); }, 4000);
            }
        }

        if (facNode) {
            var facDlg = Dialog(facNode);
            var facForm = document.getElementById("maFacForm");
            var F = function (id) { return document.getElementById(id); };
            var setFac = function (title, action, d) {
                facNode.querySelector(".modal-title span").textContent = title;
                facForm.action = action;
                F("maFacName").value = d.name || "";
                F("maFacCode").value = d.code || "";
                F("maFacType").value = d.type || facForm.getAttribute("data-default-type") || "farm";
                F("maFacProvince").value = d.province || "";
                if (d.province && F("maFacProvince").value !== d.province) {
                    // Provincia escrita a mano antes: se conserva como opción.
                    var o = document.createElement("option"); o.value = o.textContent = d.province;
                    F("maFacProvince").appendChild(o); F("maFacProvince").value = d.province;
                }
                F("maFacCity").value = d.city || "";
                F("maFacAddress").value = d.address || "";
                showErr(F("maFacNameErr"), "");
            };
            sec.querySelectorAll(".ma-fac-add").forEach(function (b) {
                b.addEventListener("click", function () {
                    setFac("Agregar instalación", "/marketplace/my-facilities/facility/create", {});
                    facDlg.show(b);
                });
            });
            sec.querySelectorAll(".ma-fac-edit").forEach(function (b) {
                b.addEventListener("click", function () {
                    setFac("Editar instalación", "/marketplace/my-facilities/facility/" + b.dataset.id + "/update", b.dataset);
                    facDlg.show(b);
                });
            });
            facNode.addEventListener("shown.bs.modal", function () { F("maFacName").focus(); });
            facForm.addEventListener("submit", function (e) {
                if (!F("maFacName").value.trim()) {
                    e.preventDefault();
                    showErr(F("maFacNameErr"), "Escribe el nombre de la instalación.");
                    F("maFacName").focus();
                }
            });
        }

        if (pondNode) {
            var pondDlg = Dialog(pondNode);
            var form = document.getElementById("maPondForm");
            var G = function (id) { return document.getElementById(id); };
            var noun = form.getAttribute("data-noun") || "piscina";
            var calc = G("maPondCalc");
            var mode = function () {
                var r = form.querySelector('input[name="capacity_mode"]:checked');
                return r ? r.value : "dimensions";
            };
            var applyMode = function () {
                var vol = mode() === "volume";
                form.querySelectorAll(".ma-pm-dim").forEach(function (x) { x.hidden = vol; });
                form.querySelectorAll(".ma-pm-vol").forEach(function (x) { x.hidden = !vol; });
                // Al enviar, el modo que no se usa va vacío.
                ["maPondLength", "maPondWidth", "maPondDepth"].forEach(function (i) { G(i).disabled = vol; });
                G("maPondVolume").disabled = !vol;
                updCalc();
            };
            var updCalc = function () {
                if (!calc) { return; }
                var txt = "";
                if (mode() === "volume") {
                    var v = num(G("maPondVolume").value);
                    if (v > 0) { txt = "Volumen: " + fmt(v, 1) + " m³"; }
                } else {
                    var a = num(G("maPondLength").value) * num(G("maPondWidth").value);
                    if (a > 0) {
                        txt = "Área: " + fmt(a, 1) + " m² (" + fmt(a / 10000, 2) + " ha)";
                        var dep = num(G("maPondDepth").value);
                        if (dep > 0) { txt += " · volumen: " + fmt(a * dep, 1) + " m³"; }
                    }
                }
                calc.textContent = txt;
            };
            form.querySelectorAll('input[name="capacity_mode"]').forEach(function (r) { r.addEventListener("change", applyMode); });
            ["maPondLength", "maPondWidth", "maPondDepth", "maPondVolume"].forEach(function (i) { G(i).addEventListener("input", updCalc); });
            var setPond = function (title, action, d) {
                pondNode.querySelector(".modal-title span").textContent = title;
                form.action = action;
                var sel = G("maPondFac");
                if (sel) {
                    sel.value = d.facility || "";
                    if (!sel.value && sel.options.length) { sel.selectedIndex = 0; }
                }
                G("maPondName").value = d.name || "";
                G("maPondCode").value = d.code || "";
                G("maPondType").value = d.type || form.getAttribute("data-default-type") || "earth";
                var m = d.mode || form.getAttribute("data-default-mode") || "dimensions";
                form.querySelectorAll('input[name="capacity_mode"]').forEach(function (r) { r.checked = r.value === m; });
                G("maPondLength").value = d.length || "";
                G("maPondWidth").value = d.width || "";
                G("maPondDepth").value = d.depth || "";
                G("maPondVolume").value = d.volume || "";
                G("maPondLoc").value = d.location || "";
                G("maPondImage").value = "";
                G("maPondImgHint").textContent = d.hasImage ? "(deja vacío para conservar la actual)" : "(opcional)";
                ["maPondNameErr", "maPondDimErr", "maPondVolErr"].forEach(function (i) { showErr(G(i), ""); });
                applyMode();
            };
            sec.querySelectorAll(".ma-pond-add").forEach(function (b) {
                b.addEventListener("click", function () {
                    setPond("Agregar " + noun, "/marketplace/my-facilities/pond/create", {facility: b.dataset.facility});
                    pondDlg.show(b);
                });
            });
            sec.querySelectorAll(".ma-pond-edit").forEach(function (b) {
                b.addEventListener("click", function () {
                    setPond("Editar " + noun, "/marketplace/my-facilities/pond/" + b.dataset.id + "/update", b.dataset);
                    pondDlg.show(b);
                });
            });
            pondNode.addEventListener("shown.bs.modal", function () { G("maPondName").focus(); });
            form.addEventListener("submit", function (e) {
                var bad = null;
                showErr(G("maPondNameErr"), ""); showErr(G("maPondDimErr"), ""); showErr(G("maPondVolErr"), "");
                if (!G("maPondName").value.trim()) {
                    showErr(G("maPondNameErr"), noun === "tanque" ? "Escribe el nombre del tanque." : "Escribe el nombre de la " + noun + ".");
                    bad = bad || G("maPondName");
                }
                if (mode() === "volume") {
                    var v = G("maPondVolume");
                    if (v.value && (num(v.value) < 0 || num(v.value) > 10000000)) {
                        showErr(G("maPondVolErr"), "El volumen debe estar entre 0 y 10.000.000 m³.");
                        bad = bad || v;
                    }
                } else {
                    var limits = {maPondLength: 100000, maPondWidth: 100000, maPondDepth: 50};
                    Object.keys(limits).forEach(function (i) {
                        var el = G(i);
                        if (el.value && (num(el.value) < 0 || num(el.value) > limits[i])) {
                            showErr(G("maPondDimErr"), "Revisa las medidas: no pueden ser negativas (profundidad máx. 50 m).");
                            bad = bad || el;
                        }
                    });
                }
                if (bad) { e.preventDefault(); bad.focus(); }
            });
        }

        if (archNode) {
            var archDlg = Dialog(archNode);
            var archForm = document.getElementById("maArchForm");
            sec.querySelectorAll(".ma-arch-btn").forEach(function (b) {
                b.addEventListener("click", function () {
                    archForm.action = b.dataset.url;
                    document.getElementById("maArchName").textContent = b.dataset.name || "";
                    document.getElementById("maArchMsg").textContent = b.dataset.msg || "";
                    archDlg.show(b);
                });
            });
        }
    }

    function boot() {
        var page = document.querySelector(".ma-page");
        if (!page || page.dataset.maReady) { return; }
        page.dataset.maReady = "1";
        initCards(page);
        var sec = document.getElementById("instalaciones");
        if (sec) { initFacilities(sec); }
    }
    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", boot); } else { boot(); }
})();
