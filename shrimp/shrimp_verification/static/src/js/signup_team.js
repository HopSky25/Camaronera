/* Equipo de campo de la verificadora en el asistente de alta (19.0.1.18.0).
 *
 * Como los certificados del paso «Documentos»: «Agregar técnico» abre un modal
 * (#shrimpTeamModal, mismo Dialog que el de certificados: foco atrapado, Esc,
 * foco de vuelta) y lo guardado queda en una tabla con «Editar» y «Eliminar».
 * En el móvil la tabla se ve como tarjetas y el modal ocupa la pantalla.
 *
 * Lo que se envía no cambia: cada fila lleva sus campos ocultos
 * team_<n>_name / _email / _password / _role (n = 0, 1, 2… en el orden de la
 * tabla), que lee ShrimpRegistryVerifier._post_registration. El modal valida
 * lo mismo que el servidor (nombre, correo con @, contraseña de 8 o más,
 * correo no repetido) para que ninguna fila se descarte en silencio.
 */
(function () {
    "use strict";

    var MIN_PW = 8;
    var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    function boot() {
        var box = document.getElementById("shrimpTeam");
        var node = document.getElementById("shrimpTeamModal");
        var UI = window.ShrimpSignupUI;
        if (!box || !node || !UI || box.dataset.ready) { return; }
        box.dataset.ready = "1";
        var el = UI.el, hidden = UI.hidden;

        var form = document.getElementById("shrimpRegisterForm");
        var body = document.getElementById("teamBody");
        var wrap = document.getElementById("teamWrap");
        var empty = document.getElementById("teamEmpty");
        var add = document.getElementById("teamAdd");
        var addHint = document.getElementById("teamAddHint");
        var count = document.getElementById("teamCount");
        var live = document.getElementById("teamLive");
        var MAX = parseInt(box.getAttribute("data-max"), 10) || 20;

        // El modal va fuera del <form> (junto al de certificados, para heredar
        // los estilos del asistente): Enter en sus campos no envía el alta y
        // la validación de pasos no lo ve.
        if (form && form.parentNode) { form.parentNode.insertBefore(node, form.nextSibling); }
        else { document.body.appendChild(node); }
        var dlg = UI.Dialog(node);
        var f = {
            title: node.querySelector("#shrimpTmTitle span"),
            name: document.getElementById("shrimpTmName"),
            email: document.getElementById("shrimpTmEmail"),
            role: document.getElementById("shrimpTmRole"),
            pw: document.getElementById("shrimpTmPw"),
            pw2: document.getElementById("shrimpTmPw2"),
            eye: document.getElementById("shrimpTmEye"),
            gen: document.getElementById("shrimpTmGen"),
            copy: document.getElementById("shrimpTmCopy"),
            help: document.getElementById("shrimpTmPwHelp"),
            save: document.getElementById("shrimpTmSave"),
        };
        var helpText = f.help.textContent;

        var entries = [];        // {name, email, password, role, roleLabel}
        var editing = null;      // fila en edición (null = nueva)
        var focusAfter = null;   // a dónde va el foco al cerrar el modal

        function roleLabel(ref) {
            var o = Array.prototype.filter.call(f.role.options, function (x) { return x.value === ref; })[0];
            return o && ref ? o.textContent.trim() : "Técnico de campo";
        }
        function say(msg) { live.textContent = ""; window.setTimeout(function () { live.textContent = msg; }, 30); }

        /* Filas devueltas por el servidor tras un error (sin contraseña). */
        try {
            (JSON.parse(box.getAttribute("data-initial") || "[]") || []).slice(0, MAX).forEach(function (r) {
                if (!r || !(r.name || r.email)) { return; }
                var ok = Array.prototype.some.call(f.role.options, function (x) { return x.value === (r.role || ""); });
                entries.push({name: r.name || "", email: r.email || "", password: "",
                              role: ok ? (r.role || "") : "", roleLabel: roleLabel(ok ? r.role : "")});
            });
        } catch (e) { entries = []; }

        /* ------------------------------------------------ tabla */
        function cell(label, text, cls) {
            var td = el("td", cls || "", text);
            td.setAttribute("data-label", label);
            return td;
        }

        function actions(td, entry, i) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts");
            var ed = el("button", "shrimp-cert-btn", "Editar");
            ed.type = "button";
            ed.setAttribute("aria-label", "Editar a " + (entry.name || entry.email));
            ed.addEventListener("click", function () { open(entry, ed); });
            var rm = el("button", "shrimp-cert-btn is-del", "Eliminar");
            rm.type = "button";
            rm.setAttribute("aria-label", "Eliminar a " + (entry.name || entry.email));
            rm.addEventListener("click", function () { confirmDel(td, entry, i); });
            acts.appendChild(ed);
            acts.appendChild(rm);
            td.appendChild(acts);
            td.appendChild(dataOf(entry, i));
        }

        /* Confirmación pequeña dentro de la misma fila. */
        function confirmDel(td, entry, i) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts is-confirm");
            acts.setAttribute("role", "group");
            acts.setAttribute("aria-label", "Confirmar eliminación");
            acts.appendChild(el("span", "shrimp-tech-q", "¿Eliminar?"));
            var yes = el("button", "shrimp-cert-btn is-del is-solid", "Sí, eliminar");
            yes.type = "button";
            var no = el("button", "shrimp-cert-btn", "No");
            no.type = "button";
            yes.addEventListener("click", function () {
                var k = entries.indexOf(entry);
                if (k === -1) { return; }
                entries.splice(k, 1);
                render();
                say("Se eliminó a " + (entry.name || entry.email) + ".");
                var btns = body.querySelectorAll(".shrimp-tech-acts .shrimp-cert-btn.is-del");
                var target = btns[Math.min(i, btns.length - 1)];
                (target && !add.hidden ? target : add).focus();
            });
            function cancel() {
                actions(td, entry, i);
                var b = td.querySelector(".is-del");
                if (b) { b.focus(); }
            }
            no.addEventListener("click", cancel);
            acts.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); cancel(); } });
            acts.appendChild(yes);
            acts.appendChild(no);
            td.appendChild(acts);
            td.appendChild(dataOf(entry, i));
            no.focus();
        }

        /* Lo que se envía (nombres de siempre). */
        function dataOf(entry, i) {
            var d = el("div", "d-none");
            d.appendChild(hidden("team_" + i + "_name", entry.name));
            d.appendChild(hidden("team_" + i + "_email", entry.email));
            d.appendChild(hidden("team_" + i + "_password", entry.password));
            d.appendChild(hidden("team_" + i + "_role", entry.role));
            return d;
        }

        function render() {
            body.innerHTML = "";
            entries.forEach(function (entry, i) {
                var tr = el("tr", entry.password ? "" : "is-missing");
                var tdName = cell("Nombre", entry.name, "shrimp-tech-name");
                if (!entry.password) {
                    tdName.appendChild(document.createTextNode(" "));
                    tdName.appendChild(el("span", "shrimp-chip is-req", "Falta la contraseña"));
                }
                tr.appendChild(tdName);
                tr.appendChild(cell("Correo", entry.email, "shrimp-tech-mail"));
                tr.appendChild(cell("Cargo", entry.roleLabel));
                var td = cell("Acciones", null, "shrimp-tech-acts-c");
                actions(td, entry, i);
                tr.appendChild(td);
                body.appendChild(tr);
            });
            var n = entries.length;
            wrap.hidden = !n;
            empty.hidden = !!n;
            count.textContent = n ? "· " + n + (n === 1 ? " técnico" : " técnicos") : "";
            add.hidden = n >= MAX;
            addHint.textContent = n ? (MAX - n) + " más como máximo" : "hasta " + MAX;
        }

        /* ------------------------------------------------ modal */
        function err(inp, msg) {
            var box2 = document.getElementById(inp.id + "Err");
            if (box2) { box2.textContent = msg || ""; box2.hidden = !msg; }
            inp.classList.toggle("is-invalid", !!msg);
            if (msg) { inp.setAttribute("aria-invalid", "true"); } else { inp.removeAttribute("aria-invalid"); }
        }
        function showPw(on) {
            f.pw.type = f.pw2.type = on ? "text" : "password";
            f.eye.setAttribute("aria-pressed", on ? "true" : "false");
            var t = on ? "Ocultar contraseña" : "Mostrar contraseña";
            f.eye.title = t;
            f.eye.querySelector(".visually-hidden").textContent = t;
            f.eye.querySelector(".fa").className = "fa " + (on ? "fa-eye-slash" : "fa-eye");
        }
        function accountEmail() {
            var a = document.getElementById("reg_email");
            return a ? a.value.trim().toLowerCase() : "";
        }

        function open(entry, from) {
            editing = entry || null;
            f.title.textContent = entry ? "Editar técnico" : "Agregar técnico";
            f.name.value = entry ? entry.name : "";
            f.email.value = entry ? entry.email : "";
            f.role.value = entry ? entry.role : "";
            f.pw.value = f.pw2.value = entry ? entry.password : "";
            [f.name, f.email, f.pw, f.pw2].forEach(function (i) { err(i, ""); });
            showPw(false);
            f.copy.hidden = true;
            f.help.textContent = helpText;
            focusAfter = null;
            dlg.show(from);
        }

        /* Mismas reglas que _post_registration (más el formato de correo y la
         * confirmación); el servidor sigue siendo quien decide. */
        function validate() {
            var first = null;
            function bad(inp, msg) { err(inp, msg); if (msg && !first) { first = inp; } }
            var name = f.name.value.trim().replace(/\s+/g, " ");
            var email = f.email.value.trim().toLowerCase();
            bad(f.name, name ? "" : "Escribe el nombre del técnico.");
            var em = "";
            if (!email) { em = "Escribe el correo del técnico."; }
            else if (!EMAIL_RE.test(email)) { em = "El correo no es válido (ej. tecnico@empresa.com)."; }
            else if (email === accountEmail()) { em = "Es el correo de tu cuenta: el técnico necesita uno propio."; }
            else if (entries.some(function (x) { return x !== editing && x.email.toLowerCase() === email; })) {
                em = "Ya agregaste un técnico con ese correo.";
            }
            bad(f.email, em);
            var pw = f.pw.value;
            bad(f.pw, !pw ? "Escribe una contraseña o genera una." :
                (pw.length < MIN_PW ? "La contraseña debe tener al menos " + MIN_PW + " caracteres." : ""));
            bad(f.pw2, pw && pw.length >= MIN_PW && f.pw2.value !== pw ? "Las contraseñas no coinciden." : "");
            if (first) { first.focus(); return null; }
            return {name: name, email: email, password: pw, role: f.role.value, roleLabel: roleLabel(f.role.value)};
        }

        function save() {
            var v = validate();
            if (!v) { return; }
            var isNew = !editing, entry = editing || {};
            Object.keys(v).forEach(function (k) { entry[k] = v[k]; });
            if (isNew) { entries.push(entry); }
            render();
            var i = entries.indexOf(entry);
            var row = body.children[i];
            // Nuevo: vuelve a «Agregar técnico» (si ya no hay cupo, a su fila).
            focusAfter = (isNew && !add.hidden) ? add : (row && row.querySelector(".shrimp-cert-btn"));
            dlg.hide();
            say((isNew ? "Se agregó a " : "Se actualizó a ") + entry.name + ".");
        }

        /* Contraseña aleatoria de 12 caracteres (sin 0/O, 1/l/I). */
        function generate() {
            var sets = ["ABCDEFGHJKLMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789", "-_.!?#"];
            var all = sets.join(""), out = [];
            var rnd = new Uint32Array(16);
            (window.crypto || window.msCrypto).getRandomValues(rnd);
            sets.forEach(function (s, k) { out.push(s.charAt(rnd[k] % s.length)); });
            for (var k = 4; k < 12; k++) { out.push(all.charAt(rnd[k] % all.length)); }
            for (var j = out.length - 1; j > 0; j--) {          // mezcla
                var r = rnd[12 + (j % 4)] % (j + 1), t = out[j];
                out[j] = out[r]; out[r] = t;
            }
            f.pw.value = f.pw2.value = out.join("");
            showPw(true);
            err(f.pw, ""); err(f.pw2, "");
            f.copy.hidden = !(navigator.clipboard && window.isSecureContext);
            f.help.textContent = "Contraseña generada: anótala o cópiala y compártela con el técnico.";
            f.pw.focus();
            f.pw.select();
        }

        node.querySelectorAll("[data-cm-close]").forEach(function (b) {
            b.addEventListener("click", function () { dlg.hide(); });
        });
        f.save.addEventListener("click", save);
        f.eye.addEventListener("click", function () { showPw(f.pw.type === "password"); });
        f.gen.addEventListener("click", generate);
        f.copy.addEventListener("click", function () {
            navigator.clipboard.writeText(f.pw.value).then(function () {
                f.help.textContent = "Copiada. Compártela con el técnico.";
            }, function () {});
        });
        // Enter en un campo = Guardar (el modal no está dentro del <form>).
        node.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); }
        });
        [f.name, f.email, f.pw, f.pw2].forEach(function (i) {
            i.addEventListener("input", function () { if (i.classList.contains("is-invalid")) { err(i, ""); } });
        });
        node.addEventListener("shown.bs.modal", function () { f.name.focus(); });
        node.addEventListener("hidden.bs.modal", function () {
            // Después del Dialog (que devuelve el foco al botón que abrió):
            // tras guardar, el foco va a la fila o a «Agregar técnico».
            if (focusAfter && document.contains(focusAfter)) { focusAfter.focus(); }
            focusAfter = null;
            f.pw.value = f.pw2.value = "";
        });
        add.addEventListener("click", function () { if (entries.length < MAX) { open(null, add); } });

        /* Una fila sin contraseña (devuelta por el servidor) no se crearía:
         * se avisa antes de salir del paso. */
        function pending(stepEl) {
            if (stepEl && !stepEl.contains(box)) { return null; }
            // Perfil «Verificadora» no marcado: su bloque está oculto (el paso
            // en sí también lo está cuando no es el actual, eso no cuenta).
            var sec = box.closest(".shrimp-prof-sec");
            if (sec && sec.classList.contains("d-none")) { return null; }
            var falta = entries.filter(function (x) { return !x.password; });
            var mail = accountEmail();
            var propia = entries.filter(function (x) { return mail && x.email.toLowerCase() === mail; });
            if (!falta.length && !propia.length) { return null; }
            var det = document.getElementById("verTeamSub");
            if (det) { det.open = true; }
            if (falta.length) {
                return "Falta la contraseña de: " + falta.map(function (x) { return x.name || x.email; }).join(", ") +
                    ". Ábrelo con «Editar» o elimínalo del equipo de campo.";
            }
            return "Un técnico usa el correo de tu cuenta (" + mail + "): cámbialo en el equipo de campo.";
        }
        if (form && form.shrimpWizard) { form.shrimpWizard.validators.push(pending); }

        render();
        if (entries.length) {
            var det = document.getElementById("verTeamSub");
            if (det) { det.open = true; }
        }
    }

    // Con defer este script corre ANTES de DOMContentLoaded y después de
    // signup_wizard.js, que arranca en DOMContentLoaded: se espera a ese
    // evento para que form.shrimpWizard ya exista (boot es idempotente).
    if (document.readyState === "complete") {
        boot();
    } else {
        document.addEventListener("DOMContentLoaded", boot);
        window.addEventListener("load", boot);
    }
})();
