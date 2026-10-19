/* Asistente de alta (shrimp_user_registry.registry_form).
 *
 * Sin este script el formulario funciona igual (todos los pasos visibles y
 * el servidor valida todo). Con él: un paso a la vez, los bloques de cada
 * perfil y su anexo solo si el perfil está marcado, y los certificados en una
 * tabla única con modal (los obligatorios ya aparecen como «Falta adjuntar»).
 *
 * Pasos dinámicos (19.0.1.9.0): un paso con data-step-roles="a b" solo se
 * muestra si hay marcado alguno de esos perfiles (p. ej. «Instalaciones», que
 * agrega shrimp_marketplace). Las pestañas (#shrimpSteps li) van en el mismo
 * orden que las secciones .shrimp-step y se renumeran.
 *
 * Ganchos para otros módulos (scripts que se cargan después):
 *   form.addEventListener("shrimp:roles", e => e.detail.roles)
 *   form.shrimpWizard.validators.push(function (stepEl) { return "msg" | null; })
 *   window.ShrimpSignupUI.Dialog(modalNode)   (modal reutilizable, 19.0.1.10.0)
 */
(function () {
    "use strict";

    var MAX_BYTES = 5 * 1024 * 1024;
    var OK_EXT = /\.(pdf|jpe?g|png)$/i;
    var OK_MIME = /^(application\/pdf|image\/jpeg|image\/png)$/i;

    function esc(v) {
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) { e.className = cls; }
        if (text != null) { e.textContent = text; }
        return e;
    }
    function hidden(name, value) {
        var i = document.createElement("input");
        i.type = "hidden"; i.name = name; i.value = value || "";
        return i;
    }
    function fmtSize(n) {
        if (n >= 1024 * 1024) { return (n / 1024 / 1024).toFixed(1).replace(".", ",") + " MB"; }
        return Math.max(1, Math.round(n / 1024)) + " KB";
    }
    function fmtDate(v) {
        var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || "");
        return m ? m[3] + "/" + m[2] + "/" + m[1] : "";
    }
    function fileKind(f) {
        if (!f) { return ""; }
        if (/pdf$/i.test(f.type) || /\.pdf$/i.test(f.name)) { return "PDF"; }
        if (/png$/i.test(f.type) || /\.png$/i.test(f.name)) { return "PNG"; }
        return "JPG";
    }
    function fileError(f) {
        if (!f) { return "Adjunta el archivo del certificado."; }
        if (!(OK_MIME.test(f.type || "") || OK_EXT.test(f.name || ""))) {
            return "El archivo debe ser PDF, JPG o PNG.";
        }
        if (f.size > MAX_BYTES) { return "El archivo pesa " + fmtSize(f.size) + ": el máximo es 5 MB."; }
        if (!f.size) { return "El archivo está vacío."; }
        return "";
    }

    /* Modal de Bootstrap 5 (global en el frontend de Odoo) con plan B:
     * foco atrapado, Esc y clic fuera cierran, el foco vuelve al botón. */
    function Dialog(node) {
        var M = window.Modal || (window.bootstrap && window.bootstrap.Modal);
        var inst = M && M.getOrCreateInstance ? M.getOrCreateInstance(node, {backdrop: true, keyboard: true, focus: true}) : null;
        var opener = null, backdrop = null;
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
            document.body.classList.remove("modal-open");
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
            node.addEventListener("click", function (e) { if (e.target === node) { fbHide(); } });
        }
        return {
            node: node,
            show: function (from) {
                opener = from || document.activeElement;
                if (inst) { inst.show(); return; }
                backdrop = el("div", "modal-backdrop fade show");
                document.body.appendChild(backdrop);
                node.style.display = "block";
                node.removeAttribute("aria-hidden");
                document.body.classList.add("modal-open");
                node.classList.add("show");
                node.dispatchEvent(new Event("shown.bs.modal"));
            },
            hide: function () { if (inst) { inst.hide(); } else { fbHide(); } },
        };
    }

    /* Ayudas compartidas con los bloques de otros módulos (p. ej. el equipo de
     * campo de la verificadora, shrimp_verification/static/src/js/signup_team.js):
     * el mismo modal (foco atrapado, Esc, foco de vuelta) y los mismos
     * constructores de nodos. Se cargan antes que esos scripts (defer). */
    window.ShrimpSignupUI = {Dialog: Dialog, el: el, hidden: hidden, esc: esc};

    function init() {
        var form = document.getElementById("shrimpRegisterForm");
        if (!form || form.dataset.wizardReady) { return !!form; }
        form.dataset.wizardReady = "1";

        var steps = Array.prototype.slice.call(form.querySelectorAll(".shrimp-step"));
        var marks = Array.prototype.slice.call(document.querySelectorAll("#shrimpSteps li"));
        var prev = document.getElementById("shrimpPrev");
        var next = document.getElementById("shrimpNext");
        var submit = document.getElementById("submitBtn");
        var alertBox = document.getElementById("formAlert");
        var alertText = document.getElementById("formAlertText");
        var legalBox = document.getElementById("shrimpLegalDocs");
        var current = 0;          // índice en activeSteps()
        var validators = [];
        form.shrimpWizard = {validators: validators};

        function showAlert(msg) {
            if (!alertBox) { return; }
            alertText.textContent = msg;
            alertBox.classList.remove("d-none");
            alertBox.scrollIntoView({behavior: "smooth", block: "start"});
        }
        function clearAlert() { if (alertBox) { alertBox.classList.add("d-none"); } }

        function checkedRoles() {
            return Array.prototype.slice.call(form.querySelectorAll(".shrimp-prof-check:checked"))
                .map(function (c) { return c.value; });
        }

        /* ------------------------------------------------ pasos activos */
        function stepActive(step) {
            var need = (step.getAttribute("data-step-roles") || "").split(/\s+/).filter(Boolean);
            if (!need.length) { return true; }
            var roles = checkedRoles();
            return need.some(function (r) { return roles.indexOf(r) !== -1; });
        }
        function activeSteps() { return steps.filter(stepActive); }
        function renumber() {
            var act = activeSteps(), total = act.length;
            steps.forEach(function (s, i) {
                var on = act.indexOf(s) !== -1, n = act.indexOf(s) + 1;
                if (marks[i]) {
                    marks[i].classList.toggle("d-none", !on);
                    var num = marks[i].querySelector(".shrimp-step-num");
                    if (num && on) { num.textContent = n; }
                }
                var eb = s.querySelector(".shrimp-step-eyebrow");
                if (eb && on) { eb.textContent = "Paso " + n + " de " + total; }
            });
        }

        /* ------------------------------------------------ perfiles */
        function toggleRoles() {
            var roles = checkedRoles();
            form.querySelectorAll(".shrimp-prof-sec, .shrimp-prof-dep").forEach(function (el) {
                var on = roles.indexOf(el.getAttribute("data-role")) !== -1;
                el.classList.toggle("d-none", !on);
                el.querySelectorAll("[data-req='1']").forEach(function (inp) {
                    if (on) { inp.setAttribute("required", "required"); } else { inp.removeAttribute("required"); }
                });
            });
            form.querySelectorAll(".shrimp-profile-card").forEach(function (card) {
                var inp = card.querySelector("input");
                card.classList.toggle("is-checked", !!(inp && inp.checked));
            });
            if (legalBox && legalBox.shrimpLegal) { legalBox.shrimpLegal.refresh(); }
            renderCerts();
            renumber();
            form.dispatchEvent(new CustomEvent("shrimp:roles", {detail: {roles: roles}}));
        }

        /* Campos que se copian de otro si están vacíos (p. ej. el teléfono
         * del verificador desde el celular del paso 1). */
        function prefill() {
            form.querySelectorAll("[data-prefill-from]").forEach(function (inp) {
                var src = document.getElementById(inp.getAttribute("data-prefill-from"));
                if (src && !inp.value && src.value) { inp.value = src.value.trim(); }
            });
        }

        /* ------------------------------------------------ tipo de persona
         * Paso 2 (19.0.1.8.0): antes de elegir solo se ve el selector; al
         * elegir aparecen los campos de ese tipo. Los del otro tipo quedan
         * ocultos y DESHABILITADOS (ni se validan ni se envían). */
        var ptWrap = document.getElementById("shrimpPersonFields");
        var vatInput = document.getElementById("reg_vat");
        var repCed = document.getElementById("reg_rep_ced");
        var tradeName = document.getElementById("reg_name");
        var contactInput = document.getElementById("reg_contact");
        var fullName = document.getElementById("reg_nombres");
        var lastCopied = null;
        var ptShown = null;

        function personType() {
            var sel = form.querySelector("input[name='shrimp_person_type']:checked");
            return sel ? sel.value : "";
        }

        function setText(el, pt) {
            if (!el) { return; }
            if (el.dataset.orig == null) { el.dataset.orig = el.textContent; }
            var t = pt ? el.getAttribute("data-" + pt) : el.getAttribute("data-none");
            el.textContent = t != null ? t : el.dataset.orig;
        }

        function copyContactName() {
            if (!fullName || !contactInput) { return; }
            // Se copia el nombre del paso 1 mientras no lo hayan corregido a mano.
            if (!fullName.value || fullName.value === lastCopied) {
                fullName.value = contactInput.value.trim().replace(/\s+/g, " ");
                lastCopied = fullName.value;
            }
        }

        function togglePersonType() {
            var pt = personType();
            form.querySelectorAll(".shrimp-choice").forEach(function (c) {
                var inp = c.querySelector("input");
                c.classList.toggle("is-checked", !!(inp && inp.checked));
            });
            if (ptWrap) {
                ptWrap.classList.toggle("d-none", !pt);
                if (pt && ptShown === "") {
                    // Recién elegido: se revela con una animación corta.
                    ptWrap.classList.remove("is-revealing");
                    void ptWrap.offsetWidth;
                    ptWrap.classList.add("is-revealing");
                }
            }
            form.querySelectorAll("[data-pt]").forEach(function (el) {
                var on = !!pt && el.getAttribute("data-pt") === pt;
                el.classList.toggle("d-none", !on);
                el.querySelectorAll("input, select, textarea").forEach(function (inp) { inp.disabled = !on; });
            });
            form.querySelectorAll("[data-pt-mark]").forEach(function (m) {
                m.classList.toggle("d-none", m.getAttribute("data-pt-mark") !== pt);
            });
            form.querySelectorAll(".shrimp-pt-text").forEach(function (el) { setText(el, pt); });
            ["shrimpStep2Title", "shrimpStep2Lead", "shrimpStep2Tab"].forEach(function (id) {
                setText(document.getElementById(id), pt);
            });
            form.querySelectorAll("[data-natural-placeholder]").forEach(function (inp) {
                if (inp.dataset.origPlaceholder == null) { inp.dataset.origPlaceholder = inp.placeholder || ""; }
                inp.placeholder = (pt && inp.getAttribute("data-" + pt + "-placeholder")) || inp.dataset.origPlaceholder;
            });
            if (tradeName) {
                if (pt === "juridica") { tradeName.setAttribute("required", "required"); }
                else { tradeName.removeAttribute("required"); }
            }
            if (pt === "natural") { copyContactName(); }
            if (vatInput && vatInput.value) { checkId(vatInput, true); }
            ptShown = pt;
        }

        /* Cédula / RUC de Ecuador (mismo criterio que el servidor, que es
         * quien decide: python-stdnum). */
        function digits(v) { return String(v || "").replace(/\D/g, ""); }
        function provinceOk(n) {
            var p = parseInt(n.slice(0, 2), 10);
            return (p >= 1 && p <= 24) || p === 30 || p === 50;
        }
        function isCedula(n) {
            if (!/^\d{10}$/.test(n) || !provinceOk(n) || n.charAt(2) > "6") { return false; }
            var sum = 0;
            for (var i = 0; i < 9; i++) {
                var v = parseInt(n.charAt(i), 10) * (i % 2 === 0 ? 2 : 1);
                sum += v > 9 ? v - 9 : v;
            }
            return (10 - sum % 10) % 10 === parseInt(n.charAt(9), 10);
        }
        function mod11(n, weights) {
            var sum = 0;
            for (var i = 0; i < weights.length; i++) { sum += weights[i] * parseInt(n.charAt(i), 10); }
            return sum % 11;
        }
        function isRucNatural(n) {
            return /^\d{13}$/.test(n) && n.charAt(2) <= "6" && n.slice(-3) !== "000" && isCedula(n.slice(0, 10));
        }
        function isRucSociedad(n) {
            if (!/^\d{13}$/.test(n) || !provinceOk(n)) { return false; }
            if (n.charAt(2) === "9") {
                return n.slice(-3) !== "000" && mod11(n, [4, 3, 2, 7, 6, 5, 4, 3, 2, 1]) === 0;
            }
            if (n.charAt(2) === "6") {
                return n.slice(-4) !== "0000" && mod11(n, [3, 2, 7, 6, 5, 4, 3, 2, 1]) === 0;
            }
            return false;
        }
        function idError(raw, pt) {
            var n = digits(raw);
            if (!n) { return ""; }
            if (n !== String(raw).replace(/[\s.-]/g, "")) { return "La cédula o el RUC solo llevan números."; }
            if (pt === "juridica") {
                if (isRucSociedad(n)) { return ""; }
                if (n.length === 10) { return "Una persona jurídica se identifica con el RUC de la sociedad (13 dígitos), no con una cédula."; }
                if (isRucNatural(n)) { return "Ese RUC es de una persona natural. Si la cuenta es tuya, elige «Persona natural»."; }
                return "El RUC no es válido: 13 dígitos, tercer dígito 9 (sociedad) o 6 (pública) y su dígito verificador.";
            }
            if ((n.length === 10 && isCedula(n)) || isRucNatural(n)) { return ""; }
            if (isRucSociedad(n)) { return "Ese RUC es de una sociedad. Elige «Persona jurídica (empresa)»."; }
            return "La cédula o el RUC de persona natural no son válidos: revisa los dígitos (cédula: 10; RUC: 13, termina en 001).";
        }
        function feedback(inp, msg) {
            var fb = inp.parentNode.querySelector(".invalid-feedback");
            if (!fb && msg) {
                fb = document.createElement("div");
                fb.className = "invalid-feedback";
                inp.insertAdjacentElement("afterend", fb);
            }
            if (fb) { fb.textContent = msg || ""; }
            inp.classList.toggle("is-invalid", !!msg);
            inp.setCustomValidity(msg || "");
        }
        function checkId(inp, show) {
            var msg = inp === repCed
                ? (inp.value.trim() && !isCedula(digits(inp.value)) ? "La cédula del representante no es válida." : "")
                : idError(inp.value, personType());
            inp.setCustomValidity(msg);
            if (show || !msg) { feedback(inp, msg); }
            return !msg;
        }
        [vatInput, repCed].forEach(function (inp) {
            if (!inp) { return; }
            inp.addEventListener("blur", function () { checkId(inp, true); });
            inp.addEventListener("input", function () { checkId(inp, inp.classList.contains("is-invalid")); });
        });
        if (contactInput) {
            contactInput.addEventListener("input", function () { if (personType() === "natural") { copyContactName(); } });
        }

        /* ------------------------------------------------ certificados
         * Tabla ÚNICA (19.0.1.10.1) con los certificados de todos los
         * perfiles marcados: Certificado, Perfil, N.º, Vence, Archivo, Estado,
         * Acciones (mismo patrón modal + tabla que el equipo de campo de la
         * verificadora). El catálogo viene en #shrimpCerts[data-catalog]
         * ({perfil: [...]}, los de «Todos» en cada perfil) y los perfiles en
         * data-profiles. Los obligatorios de cada perfil marcado son filas
         * «Falta adjuntar» con «Adjuntar». Lo que se envía vive en
         * #shrimpCertData: campos ocultos cert_line_<n>_* (incluido _role) y
         * el <input type="file"> real que se eligió en el modal (se MUEVE: un
         * archivo no se puede copiar a otro input). Los certificados de un
         * perfil desmarcado se conservan pero ni se ven ni se envían. */
        var certBox = document.getElementById("shrimpCerts");
        var catalog = {}, certProfiles = [];
        try { catalog = JSON.parse((certBox && certBox.getAttribute("data-catalog")) || "{}") || {}; } catch (e) { catalog = {}; }
        try { certProfiles = JSON.parse((certBox && certBox.getAttribute("data-profiles")) || "[]") || []; } catch (e) { certProfiles = []; }
        var certBody = document.getElementById("shrimpCertBody");
        var certWrap = document.getElementById("shrimpCertWrap");
        var certEmpty = document.getElementById("shrimpCertEmpty");
        var certAdd = document.getElementById("shrimpCertAdd");
        var certCount = document.getElementById("shrimpCertCount");
        var certData = document.getElementById("shrimpCertData");
        var certLive = document.getElementById("shrimpCertLive");
        var certEntries = [];     // {idx, role, ref, name, required, number, issue, expiry, fileInput, fileName, fileSize, kind}
        var certIndex = 0;
        var cm = null;            // modal
        var cmState = null;       // {entry, locked}

        function catOf(role) { return catalog[role] || []; }
        function catFind(role, ref) {
            return catOf(role).filter(function (c) { return c.ref === ref; })[0] || null;
        }
        function profLabel(code) {
            var p = certProfiles.filter(function (x) { return x.code === code; })[0];
            return p ? p.label : code;
        }
        function certRoles() { return checkedRoles(); }
        function sayCert(msg) {
            if (!certLive) { return; }
            certLive.textContent = "";
            window.setTimeout(function () { certLive.textContent = msg; }, 30);
        }
        function certCell(label, text, cls) {
            var td = el("td", cls || "", text);
            td.setAttribute("data-label", label);
            return td;
        }
        function chip(cls, text) { return el("span", "shrimp-chip " + cls, text); }

        function dataOf(entry) {
            var d = el("div", "");
            var p = "cert_line_" + entry.idx;
            d.appendChild(hidden(p + "_id", entry.ref));
            d.appendChild(hidden(p + "_role", entry.role));
            d.appendChild(hidden(p + "_number", entry.number));
            d.appendChild(hidden(p + "_issue_date", entry.issue));
            d.appendChild(hidden(p + "_expiry_date", entry.expiry));
            entry.fileInput.name = p + "_file";
            entry.fileInput.classList.add("d-none");
            entry.fileInput.removeAttribute("id");
            entry.fileInput.disabled = false;
            d.appendChild(entry.fileInput);
            return d;
        }

        function nameCell(name, required) {
            var td = certCell("Certificado", null, "shrimp-tech-name");
            var w = el("span", "shrimp-cert-tname");   // un solo bloque: en el móvil la celda es una rejilla
            w.appendChild(el("span", "", name));
            if (required) { w.appendChild(document.createTextNode(" ")); w.appendChild(chip("is-req", "Obligatorio")); }
            td.appendChild(w);
            return td;
        }

        function missingRow(role, c) {
            var tr = el("tr", "is-missing");
            tr.appendChild(nameCell(c.name, true));
            tr.appendChild(certCell("Perfil", profLabel(role)));
            tr.appendChild(certCell("N.º", "—", "shrimp-cert-num shrimp-cert-dim"));
            tr.appendChild(certCell("Vence", "—", "shrimp-cert-dim"));
            tr.appendChild(certCell("Archivo", "—", "shrimp-cert-dim"));
            var st = certCell("Estado", null);
            st.appendChild(chip("is-missing", "Falta adjuntar"));
            tr.appendChild(st);
            var td = certCell("Acciones", null, "shrimp-tech-acts-c");
            var acts = el("div", "shrimp-tech-acts");
            var at = el("button", "btn btn-sm btn-primary shrimp-cert-attach", "Adjuntar");
            at.type = "button";
            at.setAttribute("aria-label", "Adjuntar " + c.name + " (" + profLabel(role) + ")");
            at.addEventListener("click", function () { openCertModal({role: role, ref: c.ref, locked: true}, at); });
            acts.appendChild(at);
            td.appendChild(acts);
            tr.appendChild(td);
            return tr;
        }

        /* Botón compacto: icono + texto. En escritorio el texto queda oculto
         * (solo para lectores de pantalla; el nombre accesible es aria-label
         * y el title da la pista al pasar el ratón); en el móvil se ve. */
        function iconBtn(cls, icon, text, label) {
            var b = el("button", "shrimp-cert-btn shrimp-cert-ibtn " + cls);
            b.type = "button";
            var i = el("i", "fa " + icon);
            i.setAttribute("aria-hidden", "true");
            b.appendChild(i);
            b.appendChild(el("span", "shrimp-cert-ibtn-t", text));
            b.setAttribute("aria-label", label);
            b.title = text;
            return b;
        }

        function entryActions(td, entry) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts");
            var ed = iconBtn("", "fa-pencil", "Editar", "Editar " + entry.name);
            ed.addEventListener("click", function () { openCertModal({entry: entry}, ed); });
            var rm = iconBtn("is-del", "fa-trash-o", "Eliminar", "Eliminar " + entry.name);
            rm.addEventListener("click", function () { confirmCertDel(td, entry); });
            acts.appendChild(ed);
            acts.appendChild(rm);
            td.appendChild(acts);
        }

        /* Confirmación pequeña dentro de la misma fila (como los técnicos). */
        function confirmCertDel(td, entry) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts is-confirm");
            acts.setAttribute("role", "group");
            acts.setAttribute("aria-label", "Confirmar eliminación");
            acts.appendChild(el("span", "shrimp-tech-q", "¿Eliminar?"));
            // En escritorio se ve «Sí» (la columna es estrecha); en el móvil, «Sí, eliminar».
            var yes = el("button", "shrimp-cert-btn is-del is-solid", "Sí");
            yes.appendChild(el("span", "shrimp-cert-yes-x", ", eliminar"));
            yes.type = "button";
            yes.setAttribute("aria-label", "Sí, eliminar " + entry.name);
            var no = el("button", "shrimp-cert-btn", "No");
            no.type = "button";
            yes.addEventListener("click", function () {
                var k = certEntries.indexOf(entry);
                if (k === -1) { return; }
                certEntries.splice(k, 1);
                renderCerts();
                sayCert("Se eliminó " + entry.name + ".");
                var target = certBody.querySelector(".shrimp-cert-attach, .shrimp-cert-btn.is-del");
                (target || certAdd).focus();
            });
            function cancel() {
                entryActions(td, entry);
                var b = td.querySelector(".is-del");
                if (b) { b.focus(); }
            }
            no.addEventListener("click", cancel);
            acts.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); cancel(); } });
            acts.appendChild(yes);
            acts.appendChild(no);
            td.appendChild(acts);
            no.focus();
        }

        function entryRow(entry) {
            var tr = el("tr", "");
            tr.appendChild(nameCell(entry.name, entry.required));
            tr.appendChild(certCell("Perfil", profLabel(entry.role)));
            var num = certCell("N.º", null, entry.number ? "shrimp-cert-num" : "shrimp-cert-num shrimp-cert-dim");
            var numTxt = el("span", "shrimp-cert-numv", entry.number || "—");
            if (entry.number) { numTxt.title = entry.number; }
            num.appendChild(numTxt);
            tr.appendChild(num);
            tr.appendChild(certCell("Vence", entry.expiry ? fmtDate(entry.expiry) : "—", entry.expiry ? "" : "shrimp-cert-dim"));
            var fi = certCell("Archivo", null, "shrimp-cert-file");
            var ico = el("i", "fa " + (entry.kind === "PDF" ? "fa-file-pdf-o" : "fa-file-image-o") + " me-1");
            ico.setAttribute("aria-hidden", "true");
            var fwrap = el("span", "shrimp-cert-fname");
            fwrap.appendChild(ico);
            fwrap.appendChild(el("span", "shrimp-cert-fn", entry.fileName));
            fwrap.title = entry.fileName + " (" + entry.fileSize + ")";
            fi.appendChild(fwrap);
            fi.appendChild(el("span", "shrimp-cert-fsize", entry.fileSize));
            tr.appendChild(fi);
            var st = certCell("Estado", null);
            st.appendChild(chip("is-pending", "Pendiente de revisión"));
            tr.appendChild(st);
            var td = certCell("Acciones", null, "shrimp-tech-acts-c");
            entryActions(td, entry);
            tr.appendChild(td);
            return tr;
        }

        function renderCerts() {
            if (!certBody) { return; }
            var roles = certRoles();
            certBody.innerHTML = "";
            certData.innerHTML = "";
            var rows = 0, faltan = 0, adjuntos = 0;
            roles.forEach(function (role) {
                catOf(role).filter(function (c) { return c.required; }).forEach(function (c) {
                    var has = certEntries.some(function (e) { return e.role === role && e.ref === c.ref; });
                    if (!has) { faltan++; rows++; certBody.appendChild(missingRow(role, c)); }
                });
            });
            certEntries.forEach(function (e) {
                if (roles.indexOf(e.role) === -1) { return; }
                rows++; adjuntos++;
                certBody.appendChild(entryRow(e));
                certData.appendChild(dataOf(e));
            });
            var anyCat = roles.some(function (r) { return catOf(r).length; });
            var texto = "";
            if (!roles.length) {
                texto = "Elige tus perfiles en el paso «Perfil» para ver sus certificados.";
            } else if (!anyCat) {
                texto = "El catálogo no tiene certificados para tus perfiles: no necesitas adjuntar nada.";
            } else if (!rows) {
                var vistos = {}, opc = [];
                roles.forEach(function (r) {
                    catOf(r).forEach(function (c) { if (!vistos[c.ref]) { vistos[c.ref] = 1; opc.push(c.name); } });
                });
                texto = "Ninguno es obligatorio. Agrega los que tengas, por ejemplo: " +
                    opc.slice(0, 4).join(", ") + (opc.length > 4 ? "…" : ".");
            }
            certEmpty.textContent = texto;
            certEmpty.hidden = !texto;
            certWrap.hidden = !rows;
            certAdd.hidden = !anyCat;
            if (certCount) {
                var partes = [];
                if (adjuntos) { partes.push(adjuntos + (adjuntos === 1 ? " adjunto" : " adjuntos")); }
                if (faltan) { partes.push(faltan + (faltan === 1 ? " obligatorio pendiente" : " obligatorios pendientes")); }
                certCount.textContent = partes.join(" · ");
                certCount.classList.toggle("is-missing", !!faltan);
            }
        }

        /* --- modal --- */
        var cmNode = document.getElementById("shrimpCertModal");
        var cmRole = document.getElementById("shrimpCmRole");
        var cmSel = document.getElementById("shrimpCmCert");
        var cmIssuer = document.getElementById("shrimpCmIssuer");
        var cmNumber = document.getElementById("shrimpCmNumber");
        var cmIssue = document.getElementById("shrimpCmIssue");
        var cmExpiry = document.getElementById("shrimpCmExpiry");
        var cmExpReq = document.getElementById("shrimpCmExpReq");
        var cmDrop = document.getElementById("shrimpCmDrop");
        var cmInfo = document.getElementById("shrimpCmFileInfo");
        var cmSlot = document.getElementById("shrimpCmFileSlot");
        var cmKeep = document.getElementById("shrimpCmFileKeep");
        var cmTitle = document.querySelector("#shrimpCmTitle span");
        var cmProf = document.getElementById("shrimpCmProf");
        var cmFile = null;

        function cmErr(id, msg) {
            var box = document.getElementById(id);
            if (!box) { return; }
            box.textContent = msg || "";
            box.hidden = !msg;
        }
        function newFileInput() {
            cmSlot.innerHTML = "";
            cmFile = document.createElement("input");
            cmFile.type = "file";
            cmFile.accept = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
            cmFile.tabIndex = -1;
            cmFile.setAttribute("aria-hidden", "true");
            cmFile.addEventListener("change", showFile);
            cmSlot.appendChild(cmFile);
        }
        function chosen() { return cmFile && cmFile.files && cmFile.files[0]; }
        function showFile() {
            var f = chosen();
            cmDrop.classList.toggle("has-file", !!f);
            if (f) {
                var err = fileError(f);
                cmInfo.innerHTML = '<b>' + esc(f.name) + '</b> · ' + fmtSize(f.size) + ' · ' + fileKind(f);
                cmErr("shrimpCmFileErr", err);
                cmDrop.classList.toggle("is-bad", !!err);
                cmKeep.hidden = true;
            } else if (cmState && cmState.entry) {
                cmInfo.innerHTML = 'Archivo actual: <b>' + esc(cmState.entry.fileName) + '</b> · ' + esc(cmState.entry.fileSize);
                cmKeep.hidden = false;
                cmDrop.classList.add("has-file");
            } else {
                cmInfo.innerHTML = 'Arrastra el archivo aquí o <b>elige uno</b>';
                cmKeep.hidden = true;
            }
        }
        /* Opciones del «Certificado» según el perfil elegido en el modal. */
        function fillCertOptions(role, selected) {
            var entry = cmState && cmState.entry;
            var usados = certEntries.filter(function (e) { return e !== entry; }).map(function (e) { return e.ref; });
            cmSel.innerHTML = "";
            var o0 = el("option", "", role ? "Elige del catálogo…" : "Primero elige el perfil");
            o0.value = "";
            cmSel.appendChild(o0);
            catOf(role).forEach(function (c) {
                var ya = usados.indexOf(c.ref) !== -1;
                var o = el("option", "", c.name + (c.required ? " (obligatorio)" : "") + (ya ? " — ya agregado" : ""));
                o.value = c.ref;
                o.disabled = ya;
                cmSel.appendChild(o);
            });
            cmSel.value = selected && catFind(role, selected) ? selected : "";
            if (!cmState || !cmState.locked) { cmSel.disabled = !role; }
            syncCert();
        }
        function syncCert() {
            var c = cmState && catFind(cmRole.value, cmSel.value);
            cmIssuer.value = (c && c.issuer) || "";
            var exp = !!(c && c.expires);
            cmExpReq.hidden = !exp;
            cmExpiry.required = exp;
            cmErr("shrimpCmCertErr", "");
        }

        function openCertModal(opts, from) {
            if (!cm) { return; }
            var entry = opts.entry || null;
            var roles = certRoles();
            cmState = {entry: entry, locked: !!opts.locked || !!(entry && entry.required)};
            var role = entry ? entry.role : (opts.role || (roles.length === 1 ? roles[0] : ""));
            cmRole.innerHTML = "";
            if (roles.length !== 1 || !role) {
                var r0 = el("option", "", "Elige el perfil…");
                r0.value = "";
                cmRole.appendChild(r0);
            }
            roles.forEach(function (r) {
                var o = el("option", "", profLabel(r));
                o.value = r;
                cmRole.appendChild(o);
            });
            cmRole.value = role || "";
            cmRole.disabled = cmState.locked;
            cmSel.disabled = cmState.locked;
            fillCertOptions(cmRole.value, entry ? entry.ref : (opts.ref || ""));
            cmNumber.value = entry ? entry.number : "";
            cmIssue.value = entry ? entry.issue : "";
            cmExpiry.value = entry ? entry.expiry : "";
            cmTitle.textContent = entry ? "Editar certificado" : (opts.locked ? "Adjuntar certificado" : "Agregar certificado");
            cmProf.textContent = cmState.locked
                ? "Obligatorio para el perfil " + profLabel(role) + "."
                : "Elige el perfil y el certificado del catálogo.";
            ["shrimpCmRoleErr", "shrimpCmCertErr", "shrimpCmExpiryErr", "shrimpCmFileErr"].forEach(function (i) { cmErr(i, ""); });
            cmDrop.classList.remove("has-file", "is-bad", "is-over");
            newFileInput();
            showFile();
            cm.show(from);
        }

        function saveCert() {
            var role = cmRole.value;
            var c = catFind(role, cmSel.value), ok = true, first = null;
            if (!role) { cmErr("shrimpCmRoleErr", "Elige el perfil al que aplica."); ok = false; first = cmRole; }
            else { cmErr("shrimpCmRoleErr", ""); }
            if (role && !c) { cmErr("shrimpCmCertErr", "Elige el certificado del catálogo."); ok = false; first = first || cmSel; }
            if (c && certEntries.some(function (e) { return e !== cmState.entry && e.ref === c.ref; })) {
                cmErr("shrimpCmCertErr", "Ese certificado ya está en la tabla."); ok = false; first = first || cmSel;
            }
            if (c && c.expires && !cmExpiry.value) {
                cmErr("shrimpCmExpiryErr", "Este certificado vence: indica la fecha."); ok = false; first = first || cmExpiry;
            } else if (cmIssue.value && cmExpiry.value && cmExpiry.value < cmIssue.value) {
                cmErr("shrimpCmExpiryErr", "El vencimiento no puede ser anterior a la emisión."); ok = false; first = first || cmExpiry;
            } else { cmErr("shrimpCmExpiryErr", ""); }
            var f = chosen();
            var ferr = f ? fileError(f) : (cmState.entry ? "" : fileError(null));
            cmErr("shrimpCmFileErr", ferr);
            if (ferr) { ok = false; first = first || cmDrop; }
            if (!ok) { if (first) { first.focus(); } return; }
            var entry = cmState.entry, nuevo = !entry;
            if (!entry) {
                entry = {idx: certIndex++};
                certEntries.push(entry);
            }
            entry.role = role;
            entry.ref = c.ref;
            entry.name = c.name;
            entry.required = !!c.required;
            entry.number = cmNumber.value.trim();
            entry.issue = cmIssue.value;
            entry.expiry = cmExpiry.value;
            if (f) {
                if (entry.fileInput) { entry.fileInput.remove(); }
                entry.fileInput = cmFile;           // se mueve a #shrimpCertData
                entry.fileInput.removeEventListener("change", showFile);
                entry.fileName = f.name;
                entry.fileSize = fmtSize(f.size);
                entry.kind = fileKind(f);
                cmFile = null;
            }
            cm.hide();
            renderCerts();
            sayCert((nuevo ? "Se agregó " : "Se actualizó ") + entry.name + " (" + profLabel(role) + ").");
        }

        if (cmNode) {
            // El modal vive fuera del <form>: sus campos sin name no se envían.
            cm = Dialog(cmNode);
            cmNode.querySelectorAll("[data-cm-close]").forEach(function (b) {
                b.addEventListener("click", function () { cm.hide(); });
            });
            cmRole.addEventListener("change", function () {
                cmErr("shrimpCmRoleErr", "");
                fillCertOptions(cmRole.value, cmSel.value);
            });
            cmSel.addEventListener("change", syncCert);
            document.getElementById("shrimpCmSave").addEventListener("click", saveCert);
            cmDrop.addEventListener("click", function () { if (cmFile) { cmFile.click(); } });
            cmDrop.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (cmFile) { cmFile.click(); } }
            });
            ["dragenter", "dragover"].forEach(function (ev) {
                cmDrop.addEventListener(ev, function (e) { e.preventDefault(); cmDrop.classList.add("is-over"); });
            });
            ["dragleave", "drop"].forEach(function (ev) {
                cmDrop.addEventListener(ev, function () { cmDrop.classList.remove("is-over"); });
            });
            cmDrop.addEventListener("drop", function (e) {
                e.preventDefault();
                var files = e.dataTransfer && e.dataTransfer.files;
                if (!files || !files.length || !cmFile) { return; }
                try {
                    var dt = new DataTransfer();
                    dt.items.add(files[0]);
                    cmFile.files = dt.files;
                } catch (err) {
                    cmErr("shrimpCmFileErr", "Tu navegador no permite arrastrar: usa «elige uno».");
                    return;
                }
                showFile();
            });
            cmNode.addEventListener("shown.bs.modal", function () {
                (cmRole.disabled ? cmNumber : (cmRole.value && !cmSel.value ? cmSel : cmRole)).focus();
            });
        }
        if (certAdd) {
            certAdd.addEventListener("click", function () { openCertModal({}, certAdd); });
        }

        function missingCerts() {
            var faltan = [];
            certRoles().forEach(function (role) {
                catOf(role).filter(function (c) { return c.required; }).forEach(function (c) {
                    if (!certEntries.some(function (e) { return e.role === role && e.ref === c.ref; })) {
                        faltan.push(c.name + " (" + profLabel(role) + ")");
                    }
                });
            });
            return faltan;
        }

        /* ------------------------------------------------ pasos */
        function visibleInvalid(step) {
            var bad = null;
            step.querySelectorAll("input, select, textarea").forEach(function (el) {
                if (bad || el.disabled || el.closest(".d-none")) { return; }
                if (el.classList.contains("shrimp-legal-check")) { return; }   // se avisa aparte
                if (!el.checkValidity()) { bad = el; }
            });
            return bad;
        }

        function validateStep(step) {
            var id = step.id;
            if (id === "shrimp_reg_step_account") {
                var p1 = document.getElementById("password"), p2 = document.getElementById("password_confirm");
                if (p1 && p2 && p2.value && p1.value !== p2.value) {
                    showAlert("Las contraseñas no coinciden."); p2.focus(); return false;
                }
            }
            if (id === "shrimp_reg_step_profile") {
                if (!checkedRoles().length) { showAlert("Elige al menos un perfil."); return false; }
                prefill();
            }
            if (id === "shrimp_reg_step_basic" && !personType()) {
                showAlert("Elige si la cuenta es de una persona natural o de una persona jurídica (empresa).");
                return false;
            }
            if (id === "shrimp_reg_step_basic") {
                [vatInput, repCed].forEach(function (inp) { if (inp && !inp.disabled) { checkId(inp, true); } });
            }
            var bad = visibleInvalid(step);
            if (bad) {
                // Un campo dentro de un bloque plegado (<details>) se despliega.
                var det = bad.closest("details");
                if (det) { det.open = true; }
                showAlert(bad.validity && bad.validity.customError && bad.validationMessage
                    ? bad.validationMessage : "Revisa los campos obligatorios de este paso.");
                bad.focus();
                return false;
            }
            for (var i = 0; i < validators.length; i++) {
                var msg = validators[i](step);
                if (msg) { showAlert(msg); return false; }
            }
            if (step.contains(certBox)) {
                var faltan = missingCerts();
                if (faltan.length) {
                    showAlert("Falta adjuntar: " + faltan.join(", ") + ".");
                    var ph = step.querySelector("#shrimpCertBody tr.is-missing .shrimp-cert-attach");
                    if (ph) { ph.focus(); }
                    return false;
                }
            }
            if (legalBox && step.contains(legalBox) && legalBox.shrimpLegal) {
                var pend = legalBox.shrimpLegal.pending();
                if (pend.length) {
                    showAlert("Falta aceptar: " + pend.map(function (p) { return p.label; }).join(", ") + ".");
                    legalBox.shrimpLegal.open(pend[0].item);
                    var t = pend[0].item.querySelector(".shrimp-legal-toggle");
                    if (t) { t.focus(); }
                    return false;
                }
            }
            clearAlert();
            return true;
        }

        function go(n) {
            var act = activeSteps();
            current = Math.max(0, Math.min(act.length - 1, n));
            var cur = act[current];
            steps.forEach(function (s) { s.classList.toggle("d-none", s !== cur); });
            marks.forEach(function (m, i) {
                var pos = act.indexOf(steps[i]);
                m.classList.toggle("is-current", steps[i] === cur);
                m.classList.toggle("is-done", pos !== -1 && pos < current);
                if (steps[i] === cur) { m.setAttribute("aria-current", "step"); } else { m.removeAttribute("aria-current"); }
            });
            renumber();
            prev.classList.toggle("invisible", current === 0);
            var count = document.getElementById("shrimpStepCount");
            if (count) { count.textContent = "Paso " + (current + 1) + " de " + act.length; }
            var last = current === act.length - 1;
            next.classList.toggle("d-none", last);
            submit.classList.toggle("d-none", !last);
            if (cur && certBox && cur.contains(certBox)) { renderCerts(); }
            window.scrollTo({top: form.getBoundingClientRect().top + window.scrollY - 120, behavior: "smooth"});
        }

        prev.addEventListener("click", function () { go(current - 1); });
        next.addEventListener("click", function () {
            if (validateStep(activeSteps()[current])) { go(current + 1); }
        });
        marks.forEach(function (m, i) {
            m.addEventListener("click", function () {
                var act = activeSteps(), target = act.indexOf(steps[i]);
                if (target === -1) { return; }
                if (target < current) { go(target); return; }
                for (var k = current; k < target; k++) { if (!validateStep(act[k])) { go(k); return; } }
                go(target);
            });
        });

        form.querySelectorAll(".shrimp-prof-check").forEach(function (c) { c.addEventListener("change", toggleRoles); });
        form.querySelectorAll("input[name='shrimp_person_type']").forEach(function (c) { c.addEventListener("change", togglePersonType); });

        form.addEventListener("submit", function (e) {
            var act = activeSteps();
            for (var k = 0; k < act.length; k++) {
                if (!validateStep(act[k])) { e.preventDefault(); go(k); return; }
            }
        });

        toggleRoles();
        ptShown = personType();
        togglePersonType();
        // Con un error del servidor se abre en el último paso (lo más probable
        // es que falte una casilla o un documento).
        go(document.getElementById("shrimpSignupError") ? activeSteps().length - 1 : 0);
        return true;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
