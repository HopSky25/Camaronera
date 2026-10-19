/* ShrimpScrollLock: bloqueo de scroll de la página mientras hay un modal,
 * cajón u overlay abierto (19.0.1.29.0).
 *
 * Problema que resuelve: con un modal abierto la página de atrás seguía
 * desplazándose (rueda / dedo sobre el fondo, o al llegar al final del
 * cuerpo del modal). Los modales de Bootstrap ya bloqueaban, pero los
 * propios (.sf-modal, .buy-modal, visor .cv-modal, .sv-info-modal,
 * .pl-modal, #payOverlay…) no hacían nada, y el cajón de filtros y la capa
 * «Enviando…» ponían overflow:hidden también en <html>: como en Odoo
 * html/body miden 100% del viewport, la página saltaba arriba del todo.
 *
 * Cómo bloquea (sin position:fixed en body, para que el header fijo y los
 * elementos sticky no salten y no haya que restaurar la posición):
 *   - overflow:hidden en <body> (Odoo hace de <html> el scroller y el
 *     overflow del body se propaga al viewport, igual que hace Bootstrap); si
 *     algún día <html> tuviera overflow propio, se bloquea <html> directamente;
 *     si #wrapwrap fuera un scroller (overflow auto), también se bloquea;
 *   - el hueco de la barra de scroll se compensa con padding-right en body y
 *     en los elementos fijos pegados al borde derecho (header fijo de Odoo,
 *     barras inferiores, .fixed-top…), todo por clase + variable CSS (sin
 *     guardar/restaurar estilos en línea, así convive con el ScrollBarHelper
 *     de Bootstrap en cualquier orden de apertura/cierre);
 *   - en táctil (iOS Safari ignora a veces el overflow del body) y con la
 *     rueda, un guardián cancela el gesto si no hay dentro un elemento con
 *     scroll propio que pueda moverse en esa dirección (evita también el
 *     encadenamiento del scroll del modal hacia la página).
 *
 * Contador de referencias por «dueño» (elemento o token): con modales
 * anidados la página no se libera hasta cerrar el último.
 *
 * Enganche automático:
 *   - eventos de Bootstrap show/shown/hidden .bs.modal y .bs.offcanvas;
 *   - un MutationObserver que detecta los overlays propios abiertos
 *     (clase .open / .is-open / display) y los que se quitan del DOM.
 * API para overlays nuevos:
 *   var t = ShrimpScrollLock.lock([dueño]);  …  ShrimpScrollLock.unlock(t);
 *   ShrimpScrollLock.isLocked(); ShrimpScrollLock.refresh();
 *   o marcar el overlay con data-shrimp-scroll-lock (bloquea mientras se ve).
 */
(function () {
    "use strict";
    if (window.ShrimpScrollLock) { return; }

    var doc = document;
    var html = doc.documentElement;
    // Las hojas con bloqueo propio de respaldo (cajón, capa «Enviando…») lo
    // desactivan con esta clase: el bloqueo lo hace este script.
    html.classList.add("shrimp-sl-ready");

    // Overlays propios: abiertos cuando se ven (display != none); el cajón,
    // que siempre está en el DOM con visibility, por su clase .is-open.
    var CANDIDATES = [
        ".sf-modal", ".buy-modal", ".cv-modal", ".sv-info-modal", ".pl-modal",
        ".s-pay-overlay", ".shrimp-sg", ".s-drawer", "[data-shrimp-scroll-lock]",
    ].join(",");
    // Elementos fijos a los que compensar el ancho de la barra de scroll.
    var FIXED = [
        "header#top", ".o_header_affixed", ".fixed-top", ".fixed-bottom", ".is-fixed",
        ".pd-mbar", ".pf-mbar", ".s-bottomnav", "[data-shrimp-scroll-lock-fixed]",
    ].join(",");

    var owners = new Set();     // dueños activos
    var auto = new Set();       // dueños puestos por el observador / Bootstrap
    var fixedPadded = [];
    var touch = null;           // último punto del dedo

    function px(v) { return parseFloat(v) || 0; }

    function isBootstrap(el) {
        return el.classList && (el.classList.contains("modal") || el.classList.contains("offcanvas"));
    }

    function isOpen(el) {
        if (!el || !el.isConnected) { return false; }
        if (isBootstrap(el)) {
            var c = el.classList;
            return !!el.__shrimpSlPending || c.contains("show") || c.contains("showing") ||
                c.contains("hiding") || el.style.display === "block";
        }
        if (el.classList.contains("s-drawer")) { return el.classList.contains("is-open"); }
        var cs = window.getComputedStyle(el);
        return cs.display !== "none" && cs.visibility !== "hidden";
    }

    // ------------------------------------------------------------ aplicar
    function apply() {
        var body = doc.body;
        if (!body) { return; }
        var gap = Math.max(0, window.innerWidth - html.clientWidth);
        var hcs = window.getComputedStyle(html);
        // El overflow del body solo se propaga al viewport si <html> es visible.
        var rootMode = hcs.overflowY !== "visible" || hcs.overflowX !== "visible";
        html.style.setProperty("--shrimp-sl-gap", gap + "px");
        body.style.setProperty("--shrimp-sl-pr0", window.getComputedStyle(body).paddingRight);
        html.classList.toggle("shrimp-sl-root", rootMode);
        var wrap = doc.getElementById("wrapwrap");
        if (wrap) {
            var wcs = window.getComputedStyle(wrap);
            var scroller = /(auto|scroll|overlay)/.test(wcs.overflowY) && wrap.scrollHeight > wrap.clientHeight + 1;
            wrap.classList.toggle("shrimp-sl-scroller", scroller);
        }
        fixedPadded = [];
        if (gap > 0) {
            doc.querySelectorAll(FIXED).forEach(function (el) {
                var cs = window.getComputedStyle(el);
                if (cs.position !== "fixed" || cs.display === "none") { return; }
                var r = el.getBoundingClientRect();
                if (r.right < html.clientWidth - 1) { return; }   // no llega al borde derecho
                el.style.setProperty("--shrimp-sl-pr0", cs.paddingRight);
                el.classList.add("shrimp-sl-fixed");
                fixedPadded.push(el);
            });
        }
        html.classList.add("shrimp-sl-on");
    }

    function release() {
        html.classList.remove("shrimp-sl-on", "shrimp-sl-root");
        html.style.removeProperty("--shrimp-sl-gap");
        if (doc.body) { doc.body.style.removeProperty("--shrimp-sl-pr0"); }
        var wrap = doc.getElementById("wrapwrap");
        if (wrap) { wrap.classList.remove("shrimp-sl-scroller"); }
        fixedPadded.forEach(function (el) {
            el.classList.remove("shrimp-sl-fixed");
            el.style.removeProperty("--shrimp-sl-pr0");
        });
        fixedPadded = [];
        touch = null;
    }

    function acquire(key) {
        if (owners.has(key)) { return key; }
        owners.add(key);
        if (owners.size === 1) { apply(); }
        return key;
    }

    function drop(key) {
        if (!owners.delete(key)) { return; }
        if (owners.size === 0) { release(); }
    }

    // ----------------------------------------------- sincronizar con el DOM
    function sync() {
        var open = new Set();
        doc.querySelectorAll(CANDIDATES).forEach(function (el) { if (isOpen(el)) { open.add(el); } });
        auto.forEach(function (el) {
            // Los de Bootstrap se sueltan en hidden.bs.* (después de que
            // Bootstrap devuelva su barra); aquí solo si salen del DOM.
            if (isBootstrap(el) ? !el.isConnected : (!open.has(el) && !isOpen(el))) {
                auto.delete(el);
                drop(el);
            }
        });
        open.forEach(function (el) {
            if (!auto.has(el)) { auto.add(el); acquire(el); }
        });
    }

    var queued = false;
    function schedule() {
        if (queued) { return; }
        queued = true;
        Promise.resolve().then(function () { queued = false; sync(); });
    }

    function relevantSelf(node) {
        return node.nodeType === 1 && (auto.has(node) || node.matches(CANDIDATES));
    }
    function relevantTree(node) {
        if (node.nodeType !== 1) { return false; }
        // Un subárbol insertado/quitado que sea o contenga un overlay.
        return node.matches(CANDIDATES) || (!!node.firstElementChild && !!node.querySelector(CANDIDATES));
    }

    var observer = new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
            var r = records[i];
            if (r.type === "attributes") {
                if (relevantSelf(r.target)) { schedule(); return; }
                continue;
            }
            var lists = [r.addedNodes, r.removedNodes];
            for (var k = 0; k < 2; k++) {
                for (var j = 0; j < lists[k].length; j++) {
                    if (relevantTree(lists[k][j])) { schedule(); return; }
                }
            }
            if (auto.size && r.removedNodes.length) {
                // Se quitó un ancestro de un dueño: isConnected lo dirá.
                var gone = false;
                auto.forEach(function (el) { if (!el.isConnected) { gone = true; } });
                if (gone) { schedule(); return; }
            }
        }
    });

    // ------------------------------------------------------- Bootstrap
    function onShow(ev) {
        var el = ev.target;
        if (!isBootstrap(el)) { return; }
        if (el.classList.contains("offcanvas") && el.getAttribute("data-bs-scroll") === "true") { return; }
        el.__shrimpSlPending = true;
        auto.add(el);
        acquire(el);   // antes de que Bootstrap mida la barra: compensamos nosotros
        // Si otro listener cancela la apertura no llegará hidden.bs.modal.
        window.setTimeout(function () {
            if (ev.defaultPrevented) {
                el.__shrimpSlPending = false;
                if (!isOpen(el)) { auto.delete(el); drop(el); }
            }
        }, 0);
    }
    function onShown(ev) {
        var el = ev.target;
        if (!isBootstrap(el)) { return; }
        el.__shrimpSlPending = false;
        if (el.classList.contains("offcanvas") && el.getAttribute("data-bs-scroll") === "true") { return; }
        auto.add(el);
        acquire(el);
    }
    function onHidden(ev) {
        var el = ev.target;
        if (!isBootstrap(el)) { return; }
        el.__shrimpSlPending = false;
        auto.delete(el);
        drop(el);
    }

    // ------------------------------------------- guardián rueda / táctil
    function canScroll(el, dx, dy) {
        // dx/dy: sentido del contenido (positivo = hacia abajo / derecha).
        var cs = window.getComputedStyle(el);
        if (dy !== 0 && /(auto|scroll|overlay)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 1) {
            if (dy > 0 && Math.ceil(el.scrollTop + el.clientHeight) < el.scrollHeight - 1) { return true; }
            if (dy < 0 && el.scrollTop > 0) { return true; }
        }
        if (dx !== 0 && /(auto|scroll|overlay)/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 1) {
            var left = Math.abs(el.scrollLeft);   // RTL: scrollLeft negativo
            if (dx > 0 && Math.ceil(left + el.clientWidth) < el.scrollWidth - 1) { return true; }
            if (dx < 0 && left > 0) { return true; }
        }
        return false;
    }

    function allowed(target, dx, dy) {
        var el = target && target.nodeType === 1 ? target : (target && target.parentElement);
        if (!el) { return false; }
        if (el.closest && el.closest("input[type=range], select, [data-shrimp-scroll-allow]")) { return true; }
        var body = doc.body;
        while (el && el !== body && el !== html) {
            if (canScroll(el, dx, dy)) { return true; }
            el = el.parentElement;
        }
        return false;
    }

    function onWheel(ev) {
        if (!owners.size || ev.ctrlKey || !ev.cancelable) { return; }
        var dx = ev.deltaX, dy = ev.deltaY;
        if (ev.shiftKey && !dx) { dx = dy; dy = 0; }
        if (Math.abs(dx) > Math.abs(dy)) { dy = 0; } else { dx = 0; }
        if (!dx && !dy) { return; }
        if (!allowed(ev.target, dx, dy)) { ev.preventDefault(); }
    }

    function onTouchStart(ev) {
        if (!owners.size || ev.touches.length !== 1) { touch = null; return; }
        touch = {x: ev.touches[0].clientX, y: ev.touches[0].clientY};
    }

    function onTouchMove(ev) {
        if (!owners.size || !ev.cancelable) { return; }
        if (ev.touches.length !== 1) { return; }   // pellizco: zoom libre
        var t = ev.touches[0];
        if (!touch) { touch = {x: t.clientX, y: t.clientY}; return; }
        // El dedo sube => el contenido va hacia abajo.
        var dx = touch.x - t.clientX, dy = touch.y - t.clientY;
        if (Math.abs(dx) > Math.abs(dy)) { dy = 0; } else { dx = 0; }
        if (!dx && !dy) { return; }
        touch = {x: t.clientX, y: t.clientY};
        if (!allowed(ev.target, dx, dy)) { ev.preventDefault(); }
    }

    // ------------------------------------------------------------- API
    var seq = 0;
    var api = {
        /** Bloquea el scroll; devuelve el token para unlock(). */
        lock: function (owner) { return acquire(owner || {shrimpScrollLock: ++seq}); },
        unlock: function (owner) {
            if (owner) { auto.delete(owner); drop(owner); }
        },
        isLocked: function () { return owners.size > 0; },
        refresh: function () { sync(); },
        _owners: function () { return owners.size; },
    };
    window.ShrimpScrollLock = api;

    doc.addEventListener("show.bs.modal", onShow, true);
    doc.addEventListener("shown.bs.modal", onShown, true);
    doc.addEventListener("hidden.bs.modal", onHidden, true);
    doc.addEventListener("show.bs.offcanvas", onShow, true);
    doc.addEventListener("shown.bs.offcanvas", onShown, true);
    doc.addEventListener("hidden.bs.offcanvas", onHidden, true);
    window.addEventListener("wheel", onWheel, {passive: false});
    doc.addEventListener("touchstart", onTouchStart, {passive: true, capture: true});
    doc.addEventListener("touchmove", onTouchMove, {passive: false, capture: true});
    // bfcache: al volver con «Atrás» se recalcula desde el DOM.
    window.addEventListener("pageshow", function () { sync(); });

    function start() {
        observer.observe(html, {
            subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style"],
        });
        sync();
    }
    if (doc.readyState === "loading") {
        doc.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
