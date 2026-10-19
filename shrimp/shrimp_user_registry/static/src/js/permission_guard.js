/* Permisos de los subusuarios (CR-12).
 *
 * El servidor pinta en #shrimpPermissionDeny las rutas que este usuario NO
 * puede usar (expresiones regulares de la matriz central). Aquí solo se
 * desactivan en pantalla: los formularios POST a esas rutas quedan con sus
 * campos y botones inactivos y un candado, y los enlaces a formularios GET
 * cerrados se apagan. La comprobación de verdad la hace el servidor (403).
 */
(function () {
    "use strict";
    function init() {
        var nodo = document.getElementById("shrimpPermissionDeny");
        if (!nodo) {
            return;
        }
        var datos;
        try {
            datos = JSON.parse(nodo.getAttribute("data-deny") || "{}");
        } catch (e) {
            return;
        }
        var post = (datos.post || []).map(function (r) { return new RegExp(r); });
        var get = (datos.get || []).map(function (r) { return new RegExp(r); });
        var aviso = "Tu usuario no tiene permiso para esta acción (pídeselo al administrador de tu cuenta).";
        function ruta(url) {
            try {
                var u = new URL(url, window.location.href);
                if (u.origin !== window.location.origin) {
                    return null;
                }
                // Sin prefijo de idioma (/en/...).
                return u.pathname.replace(/^\/[a-z]{2}(_[A-Za-z]{2,4})?(?=\/)/, "");
            } catch (e) {
                return null;
            }
        }
        function coincide(lista, url) {
            var p = ruta(url);
            if (p === null) {
                return false;
            }
            return lista.some(function (re) { return re.test(p); });
        }
        document.querySelectorAll("form").forEach(function (form) {
            var metodo = (form.getAttribute("method") || "get").toLowerCase();
            var lista = metodo === "post" ? post : get;
            if (!form.getAttribute("action") || !coincide(lista, form.action)) {
                return;
            }
            form.classList.add("shrimp-perm-denied");
            form.setAttribute("title", aviso);
            form.addEventListener("submit", function (ev) {
                ev.preventDefault();
                ev.stopImmediatePropagation();
                window.alert(aviso);
            }, true);
            form.querySelectorAll("input, select, textarea, button").forEach(function (el) {
                if (el.type === "hidden") {
                    return;
                }
                el.disabled = true;
            });
            form.querySelectorAll("button[type=submit], input[type=submit], button:not([type])").forEach(function (b) {
                if (!b.querySelector(".fa-lock")) {
                    var i = document.createElement("i");
                    i.className = "fa fa-lock ms-1";
                    i.setAttribute("aria-hidden", "true");
                    b.appendChild(i);
                }
            });
        });
        document.querySelectorAll("a[href]").forEach(function (a) {
            if (!coincide(get, a.href)) {
                return;
            }
            a.classList.add("disabled", "shrimp-perm-denied");
            a.setAttribute("aria-disabled", "true");
            a.setAttribute("title", aviso);
            a.addEventListener("click", function (ev) {
                ev.preventDefault();
                window.alert(aviso);
            });
        });
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
