/* Asistente de alta: paso «Instalaciones» (shrimp_marketplace 19.0.1.22.4).
 *
 * Dos tablas, como los certificados y el equipo de campo (mismo Dialog de
 * window.ShrimpSignupUI: foco atrapado, Esc, foco de vuelta; en el móvil el
 * modal ocupa la pantalla y las tablas se ven como tarjetas):
 *   «Instalaciones»       → modal #shrimpFacModal (tipo, nombre, código,
 *                            provincia, cantón, dirección, coordenadas).
 *   «Piscinas y tanques»  → modal #shrimpPondModal (instalación, nombre,
 *                            código, tipo según la instalación, capacidad por
 *                            dimensiones o volumen directo).
 * Cada fila tiene «Editar» (el mismo modal, con sus datos) y «Eliminar» (con
 * confirmación en la fila; la instalación avisa que se van sus piscinas).
 *
 * Tipos de instalación (data-kinds, del servidor): solo los de los perfiles
 * marcados en el paso «Perfil» (camaronera → Finca, laboratorio →
 * Laboratorio, naupliero → Centro de producción); si hay uno solo, ya viene
 * elegido. Lo que se envía son campos ocultos en #shrimpFacData:
 *   fac_<i>_type/_name/_code/_state_id/_city/_address/_lat/_lng
 *   pond_<i>_<j>_name/_code/_type/_mode/_length/_width/_depth/_volume
 * (i = posición de la instalación en la tabla). «Lo haré después» no envía
 * nada. Con un error del servidor se reconstruye desde data-prefill.
 */
(function () {
    "use strict";

    var LIMITS = {length: 100000, width: 100000, depth: 50, volume: 10000000};
    var AREA_TYPES = ["earth", "geomembrane"];

    function boot() {
        var box = document.getElementById("shrimpFacilities");
        var facNode = document.getElementById("shrimpFacModal");
        var pondNode = document.getElementById("shrimpPondModal");
        var UI = window.ShrimpSignupUI;
        if (!box || !facNode || !pondNode || !UI || box.dataset.ready) { return; }
        box.dataset.ready = "1";
        var el = UI.el, hidden = UI.hidden;

        var form = document.getElementById("shrimpRegisterForm");
        var later = document.getElementById("shrimpFacLater");
        var data = document.getElementById("shrimpFacData");
        var live = document.getElementById("facLive");
        var MAX_FAC = parseInt(box.getAttribute("data-max-fac"), 10) || 20;
        var MAX_POND = parseInt(box.getAttribute("data-max-pond"), 10) || 60;
        var T = {
            facBody: document.getElementById("facBody"), facWrap: document.getElementById("facWrap"),
            facEmpty: document.getElementById("facEmpty"), facAdd: document.getElementById("facAdd"),
            facHint: document.getElementById("facAddHint"), facCount: document.getElementById("facCount"),
            pondBody: document.getElementById("pondBody"), pondWrap: document.getElementById("pondWrap"),
            pondEmpty: document.getElementById("pondEmpty"), pondAdd: document.getElementById("pondAdd"),
            pondHint: document.getElementById("pondAddHint"), pondCount: document.getElementById("pondCount"),
            pondNeed: document.getElementById("pondNeed"),
        };

        var kinds = [];
        try { kinds = JSON.parse(box.getAttribute("data-kinds") || "[]") || []; } catch (e) { kinds = []; }
        function kindOf(code) { return kinds.filter(function (k) { return k.code === code; })[0] || null; }
        function pondTypeLabel(code) {
            var all = [];
            kinds.forEach(function (k) { all = all.concat(k.pondTypes); });
            var t = all.filter(function (x) { return x.code === code; })[0];
            return t ? t.label : code;
        }
        function pondTypesOf(fac) { var k = fac && kindOf(fac.type); return k ? k.pondTypes : []; }
        function pondAllowed(fac, type) { return pondTypesOf(fac).some(function (t) { return t.code === type; }); }

        // Los modales van fuera del <form> (como los de certificados y equipo):
        // Enter en sus campos no envía el alta y la validación de pasos no los ve.
        [facNode, pondNode].forEach(function (n) {
            if (form && form.parentNode) { form.parentNode.insertBefore(n, form.nextSibling); }
            else { document.body.appendChild(n); }
        });
        var facDlg = UI.Dialog(facNode), pondDlg = UI.Dialog(pondNode);

        var facs = [];    // {type, name, code, state_id, stateName, city, address, lat, lng}
        var ponds = [];   // {fac, name, code, type, mode, length, width, depth, volume}

        function checkedRoles() {
            var boxes = form ? form.querySelectorAll(".shrimp-prof-check") : [];
            if (!boxes.length) { return kinds.map(function (k) { return k.profile; }); }
            return Array.prototype.slice.call(form.querySelectorAll(".shrimp-prof-check:checked"))
                .map(function (c) { return c.value; });
        }
        var roles = checkedRoles();
        function allowedKinds() { return kinds.filter(function (k) { return roles.indexOf(k.profile) !== -1; }); }
        function kindAllowed(code) { return allowedKinds().some(function (k) { return k.code === code; }); }
        function isOff() { return !!(later && later.checked) || !allowedKinds().length; }

        function say(msg) { live.textContent = ""; window.setTimeout(function () { live.textContent = msg; }, 30); }

        /* ------------------------------------------------ números */
        function str(v) { return v == null || v === false ? "" : String(v); }
        function parseNum(v) {
            v = str(v).trim().replace(",", ".");
            if (!v) { return 0; }
            if (!/^-?(\d+\.?\d*|\.\d+)$/.test(v)) { return NaN; }
            return parseFloat(v);
        }
        function fmt(n, dec) {
            return n.toLocaleString("es-EC", {maximumFractionDigits: dec == null ? 2 : dec});
        }
        function geometry(p) {
            if (p.mode === "volume") { return {area: 0, volume: parseNum(p.volume) || 0}; }
            var l = parseNum(p.length) || 0, w = parseNum(p.width) || 0, d = parseNum(p.depth) || 0;
            return {area: l * w, volume: l * w * d};
        }
        function haText(m2) { return fmt(m2 / 10000, m2 < 100 ? 4 : 2) + " ha"; }
        function measure(p) {
            var g = geometry(p), out = [];
            var ha = g.area > 0 ? haText(g.area) : "";
            var m3 = g.volume > 0 ? fmt(g.volume, 1) + " m³" : "";
            if (AREA_TYPES.indexOf(p.type) !== -1) {
                out.push(ha || m3);
            } else if (p.type === "tank" || p.type === "raceway") {
                out.push(m3 || (g.area > 0 ? fmt(g.area, 1) + " m²" : ""));
            } else {
                out.push(ha, m3);
            }
            return out.filter(Boolean).join(" · ") || "—";
        }

        /* ------------------------------------------------ tablas */
        function cell(label, text, cls) {
            var td = el("td", cls || "", text);
            td.setAttribute("data-label", label);
            return td;
        }
        function pondsOf(f) { return ponds.filter(function (p) { return p.fac === f; }); }
        function location(f) { return [f.city, f.stateName].filter(Boolean).join(", ") || "—"; }

        function actions(td, kind, entry) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts");
            var ed = el("button", "shrimp-cert-btn", "Editar");
            ed.type = "button";
            ed.setAttribute("aria-label", "Editar " + entry.name);
            ed.addEventListener("click", function () { (kind === "fac" ? openFac : openPond)(entry, ed); });
            var rm = el("button", "shrimp-cert-btn is-del", "Eliminar");
            rm.type = "button";
            rm.setAttribute("aria-label", "Eliminar " + entry.name);
            rm.addEventListener("click", function () { confirmDel(td, kind, entry); });
            acts.appendChild(ed);
            acts.appendChild(rm);
            td.appendChild(acts);
        }

        /* Confirmación pequeña dentro de la misma fila. */
        function confirmDel(td, kind, entry) {
            td.innerHTML = "";
            var acts = el("div", "shrimp-tech-acts is-confirm");
            acts.setAttribute("role", "group");
            acts.setAttribute("aria-label", "Confirmar eliminación");
            var n = kind === "fac" ? pondsOf(entry).length : 0;
            var extra = n === 1 ? "su piscina/tanque" : "sus " + n + " piscinas/tanques";
            acts.appendChild(el("span", "shrimp-tech-q", n
                ? "¿Eliminar? También se eliminará" + (n === 1 ? " " : "n ") + extra + "."
                : "¿Eliminar?"));
            var yes = el("button", "shrimp-cert-btn is-del is-solid", "Sí, eliminar");
            yes.type = "button";
            var no = el("button", "shrimp-cert-btn", "No");
            no.type = "button";
            yes.addEventListener("click", function () {
                var list = kind === "fac" ? facs : ponds;
                var k = list.indexOf(entry);
                if (k === -1) { return; }
                list.splice(k, 1);
                if (kind === "fac") { ponds = ponds.filter(function (p) { return p.fac !== entry; }); }
                render();
                say("Se eliminó " + entry.name + (n ? " y " + extra : "") + ".");
                var body = kind === "fac" ? T.facBody : T.pondBody;
                var btns = body.querySelectorAll(".shrimp-tech-acts .shrimp-cert-btn.is-del");
                var target = btns[Math.min(k, btns.length - 1)];
                var add = kind === "fac" ? T.facAdd : T.pondAdd;
                (target || (add.disabled ? T.facAdd : add)).focus();
            });
            function cancel() {
                actions(td, kind, entry);
                var b = td.querySelector(".is-del");
                if (b) { b.focus(); }
            }
            no.addEventListener("click", cancel);
            acts.addEventListener("keydown", function (e) {
                if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
            });
            acts.appendChild(yes);
            acts.appendChild(no);
            td.appendChild(acts);
            no.focus();
        }

        function nameCell(entry, chip) {
            // Un solo hijo: en el móvil la celda es una rejilla «etiqueta | valor».
            var td = cell("Nombre", null, "shrimp-tech-name");
            var v = el("span", "shrimp-fac-nm");
            v.appendChild(document.createTextNode(entry.name));
            if (entry.code) { v.appendChild(el("span", "shrimp-fac-code", entry.code)); }
            if (chip) { v.appendChild(document.createTextNode(" ")); v.appendChild(chip); }
            td.appendChild(v);
            return td;
        }

        /* Lo que se envía: nada con «Lo haré después» o sin perfil con instalaciones. */
        function renderData() {
            data.innerHTML = "";
            if (isOff()) { return; }
            var seq = {};
            facs.forEach(function (f, i) {
                var pre = "fac_" + i + "_";
                data.appendChild(hidden(pre + "type", f.type));
                ["name", "code", "state_id", "city", "address", "lat", "lng"].forEach(function (k) {
                    data.appendChild(hidden(pre + k, str(f[k])));
                });
            });
            ponds.forEach(function (p) {
                var i = facs.indexOf(p.fac);
                if (i === -1) { return; }
                var j = seq[i] = (seq[i] == null ? 0 : seq[i] + 1);
                var pre = "pond_" + i + "_" + j + "_";
                data.appendChild(hidden(pre + "name", p.name));
                data.appendChild(hidden(pre + "code", p.code));
                data.appendChild(hidden(pre + "type", p.type));
                data.appendChild(hidden(pre + "mode", p.mode));
                if (p.mode === "volume") {
                    data.appendChild(hidden(pre + "volume", str(p.volume)));
                } else {
                    ["length", "width", "depth"].forEach(function (k) { data.appendChild(hidden(pre + k, str(p[k]))); });
                }
            });
        }

        function render() {
            T.facBody.innerHTML = "";
            facs.forEach(function (f) {
                var k = kindOf(f.type), ok = kindAllowed(f.type);
                var tr = el("tr", ok ? "" : "is-missing");
                tr.appendChild(nameCell(f, ok ? null : el("span", "shrimp-chip is-req", "No corresponde a tus perfiles")));
                tr.appendChild(cell("Tipo", k ? k.label : f.type, "shrimp-fac-type"));
                tr.appendChild(cell("Ubicación", location(f), "shrimp-tech-mail"));
                tr.appendChild(cell("Piscinas", String(pondsOf(f).length), "shrimp-fac-num"));
                var td = cell("Acciones", null, "shrimp-tech-acts-c");
                actions(td, "fac", f);
                tr.appendChild(td);
                T.facBody.appendChild(tr);
            });
            T.pondBody.innerHTML = "";
            ponds.forEach(function (p) {
                var tr = el("tr");
                tr.appendChild(nameCell(p));
                tr.appendChild(cell("Instalación", p.fac.name));
                tr.appendChild(cell("Tipo", pondTypeLabel(p.type), "shrimp-fac-type"));
                tr.appendChild(cell("Medidas", measure(p), "shrimp-fac-meas"));
                var td = cell("Acciones", null, "shrimp-tech-acts-c");
                actions(td, "pond", p);
                tr.appendChild(td);
                T.pondBody.appendChild(tr);
            });
            var nf = facs.length, np = ponds.length, off = !!(later && later.checked);
            T.facWrap.hidden = !nf;
            T.facEmpty.hidden = !!nf;
            T.facCount.textContent = nf ? "· " + nf : "";
            T.facAdd.disabled = off || nf >= MAX_FAC;
            T.facHint.textContent = nf >= MAX_FAC ? "máximo " + MAX_FAC : "opcional";
            T.pondWrap.hidden = !np;
            T.pondNeed.hidden = !!nf;
            T.pondEmpty.hidden = !!np || !nf;
            T.pondCount.textContent = np ? "· " + np : "";
            T.pondAdd.disabled = off || !nf || np >= MAX_POND;
            if (nf) { T.pondAdd.removeAttribute("aria-describedby"); } else { T.pondAdd.setAttribute("aria-describedby", "pondNeed"); }
            T.pondHint.textContent = np >= MAX_POND ? "máximo " + MAX_POND : "opcional";
            box.classList.toggle("is-later", off);
            renderData();
        }

        /* ------------------------------------------------ modales */
        function err(inp, msg, box2) {
            box2 = box2 || document.getElementById(inp.id + "Err");
            if (box2) { box2.textContent = (msg || "").trim(); box2.hidden = !(msg || "").trim(); }
            inp.classList.toggle("is-invalid", !!msg);
            if (msg) { inp.setAttribute("aria-invalid", "true"); } else { inp.removeAttribute("aria-invalid"); }
        }
        function option(sel, value, label, disabled) {
            var o = el("option", "", label);
            o.value = value;
            if (disabled) { o.disabled = true; }
            sel.appendChild(o);
            return o;
        }
        function clean(v) { return (v || "").trim().replace(/\s+/g, " "); }
        var focusAfter = null;

        /* ---- instalación ---- */
        var F = {
            title: facNode.querySelector("#shrimpFmTitle span"),
            type: document.getElementById("shrimpFmType"), name: document.getElementById("shrimpFmName"),
            code: document.getElementById("shrimpFmCode"), state: document.getElementById("shrimpFmState"),
            city: document.getElementById("shrimpFmCity"), address: document.getElementById("shrimpFmAddress"),
            lat: document.getElementById("shrimpFmLat"), lng: document.getElementById("shrimpFmLng"),
            save: document.getElementById("shrimpFmSave"),
        };
        var facEditing = null;

        function openFac(entry, from) {
            facEditing = entry || null;
            F.title.textContent = entry ? "Editar instalación" : "Agregar instalación";
            F.type.innerHTML = "";
            var allowed = allowedKinds();
            if (allowed.length > 1) { option(F.type, "", "Elige…"); }
            allowed.forEach(function (k) { option(F.type, k.code, k.label); });
            if (entry && !kindAllowed(entry.type)) {
                var k0 = kindOf(entry.type);
                option(F.type, entry.type, (k0 ? k0.label : entry.type) + " (no corresponde a tus perfiles)");
            }
            F.type.value = entry ? entry.type : (allowed.length === 1 ? allowed[0].code : "");
            F.name.value = entry ? entry.name : "";
            F.code.value = entry ? entry.code : "";
            F.state.value = entry ? str(entry.state_id) : "";
            F.city.value = entry ? entry.city : "";
            F.address.value = entry ? entry.address : "";
            F.lat.value = entry ? str(entry.lat) : "";
            F.lng.value = entry ? str(entry.lng) : "";
            [F.type, F.name, F.code, F.lat, F.lng].forEach(function (i) { err(i, ""); });
            focusAfter = null;
            facDlg.show(from);
        }

        function validateFac() {
            var first = null;
            function bad(inp, msg) { err(inp, msg); if (msg && !first) { first = inp; } }
            var name = clean(F.name.value), code = clean(F.code.value), type = F.type.value;
            var tmsg = "";
            if (!type) { tmsg = "Elige el tipo de instalación."; }
            else if (!kindAllowed(type)) { tmsg = "Ese tipo no corresponde a los perfiles que elegiste."; }
            else if (facEditing) {
                var choque = pondsOf(facEditing).filter(function (p) { return !pondAllowed({type: type}, p.type); });
                if (choque.length) {
                    tmsg = "Sus piscinas/tanques (" + choque.map(function (p) { return p.name; }).join(", ") +
                        ") no son de un tipo que admita esta instalación: cámbialos o elimínalos primero.";
                }
            }
            bad(F.type, tmsg);
            bad(F.name, name ? "" : "Escribe el nombre de la instalación.");
            bad(F.code, code && facs.some(function (f) {
                return f !== facEditing && f.code && f.code.toLowerCase() === code.toLowerCase();
            }) ? "Ya agregaste una instalación con ese código." : "");
            var lat = parseNum(F.lat.value), lng = parseNum(F.lng.value);
            bad(F.lat, isNaN(lat) || lat < -90 || lat > 90 ? "La latitud va de -90 a 90 (ej. -3.2581)." : "");
            bad(F.lng, isNaN(lng) || lng < -180 || lng > 180 ? "La longitud va de -180 a 180 (ej. -79.9554)." : "");
            if (first) { first.focus(); return null; }
            var st = F.state.options[F.state.selectedIndex];
            return {
                type: type, name: name, code: code, state_id: F.state.value,
                stateName: F.state.value && st ? st.textContent.trim() : "",
                city: clean(F.city.value), address: clean(F.address.value),
                lat: F.lat.value.trim() ? String(lat) : "", lng: F.lng.value.trim() ? String(lng) : "",
            };
        }

        function saveFac() {
            var v = validateFac();
            if (!v) { return; }
            var isNew = !facEditing, entry = facEditing || {};
            Object.keys(v).forEach(function (k) { entry[k] = v[k]; });
            if (isNew) { facs.push(entry); }
            render();
            var row = T.facBody.children[facs.indexOf(entry)];
            focusAfter = (isNew && !T.facAdd.disabled) ? T.facAdd : (row && row.querySelector(".shrimp-cert-btn"));
            facDlg.hide();
            say((isNew ? "Se agregó " : "Se actualizó ") + entry.name + ".");
        }

        /* ---- piscina o tanque ---- */
        var P = {
            title: pondNode.querySelector("#shrimpPmTitle span"),
            fac: document.getElementById("shrimpPmFac"), name: document.getElementById("shrimpPmName"),
            code: document.getElementById("shrimpPmCode"), type: document.getElementById("shrimpPmType"),
            dim: document.getElementById("shrimpPmModeDim"), vol: document.getElementById("shrimpPmModeVol"),
            length: document.getElementById("shrimpPmLength"), width: document.getElementById("shrimpPmWidth"),
            depth: document.getElementById("shrimpPmDepth"), volume: document.getElementById("shrimpPmVolume"),
            dimErr: document.getElementById("shrimpPmDimErr"), calc: document.getElementById("shrimpPmCalc"),
            save: document.getElementById("shrimpPmSave"),
        };
        var pondEditing = null;

        function pmFac() { return P.fac.value === "" ? null : (facs[parseInt(P.fac.value, 10)] || null); }
        function pmMode() { return P.vol.checked ? "volume" : "dimensions"; }
        function fillPondTypes(keep) {
            var f = pmFac(), types = pondTypesOf(f);
            P.type.innerHTML = "";
            if (!f) {
                option(P.type, "", "Elige primero la instalación");
                P.type.disabled = true;
                return;
            }
            P.type.disabled = false;
            types.forEach(function (t) { option(P.type, t.code, t.label); });
            P.type.value = types.some(function (t) { return t.code === keep; }) ? keep : (types[0] ? types[0].code : "");
        }
        function calc() {
            var g = geometry({mode: pmMode(), length: P.length.value, width: P.width.value,
                              depth: P.depth.value, volume: P.volume.value});
            var out = [];
            if (g.area > 0) { out.push("Área ≈ " + haText(g.area) + " (" + fmt(g.area, 1) + " m²)"); }
            if (g.volume > 0) { out.push("Volumen ≈ " + fmt(g.volume, 1) + " m³"); }
            P.calc.textContent = out.join(" · ");
            P.calc.hidden = !out.length;
        }
        function syncMode() {
            var vol = pmMode() === "volume";
            pondNode.querySelectorAll(".shrimp-pm-dim").forEach(function (x) { x.hidden = vol; });
            pondNode.querySelectorAll(".shrimp-pm-vol").forEach(function (x) { x.hidden = !vol; });
            pondNode.querySelectorAll(".shrimp-seg-opt").forEach(function (x) {
                x.classList.toggle("is-on", x.querySelector("input").checked);
            });
            calc();
        }
        function dimErrors(msg) {
            P.dimErr.textContent = msg || "";
            P.dimErr.hidden = !msg;
        }

        function openPond(entry, from) {
            pondEditing = entry || null;
            P.title.textContent = entry ? "Editar piscina o tanque" : "Agregar piscina o tanque";
            P.fac.innerHTML = "";
            if (facs.length !== 1) { option(P.fac, "", "Elige…"); }
            facs.forEach(function (f, i) {
                var k = kindOf(f.type);
                option(P.fac, String(i), f.name + (f.code ? " (" + f.code + ")" : "") + (k ? " · " + k.label : ""),
                       !kindAllowed(f.type));
            });
            var fi = entry ? facs.indexOf(entry.fac) : (facs.length === 1 ? 0 : -1);
            P.fac.value = fi === -1 ? "" : String(fi);
            fillPondTypes(entry ? entry.type : "");
            P.name.value = entry ? entry.name : "";
            P.code.value = entry ? entry.code : "";
            (entry && entry.mode === "volume" ? P.vol : P.dim).checked = true;
            ["length", "width", "depth", "volume"].forEach(function (k) { P[k].value = entry ? str(entry[k]) : ""; });
            [P.fac, P.name, P.code, P.volume, P.length, P.width, P.depth].forEach(function (i) { err(i, ""); });
            dimErrors("");
            syncMode();
            focusAfter = null;
            pondDlg.show(from);
        }

        function validatePond() {
            var first = null;
            function bad(inp, msg) { err(inp, msg); if (msg && !first) { first = inp; } }
            var f = pmFac();
            bad(P.fac, !f ? "Elige la instalación." : (!kindAllowed(f.type) ? "Esa instalación no corresponde a tus perfiles." : ""));
            var name = clean(P.name.value), code = clean(P.code.value);
            bad(P.name, name ? "" : "Escribe el nombre de la piscina o tanque.");
            bad(P.code, code && ponds.some(function (p) {
                return p !== pondEditing && p.code && p.code.toLowerCase() === code.toLowerCase();
            }) ? "Ya agregaste una piscina o tanque con ese código." : "");
            var mode = pmMode();
            var r = {fac: f, name: name, code: code, type: P.type.value, mode: mode,
                     length: "", width: "", depth: "", volume: ""};
            if (mode === "volume") {
                var v = parseNum(P.volume.value);
                bad(P.volume, isNaN(v) || v < 0 || v > LIMITS.volume ? "Escribe un volumen válido (de 0 a 10.000.000 m³)." : "");
                r.volume = P.volume.value.trim() ? String(v) : "";
            } else {
                var msg = "";
                ["length", "width", "depth"].forEach(function (k) {
                    var n = parseNum(P[k].value);
                    var wrong = isNaN(n) || n < 0 || n > LIMITS[k];
                    P[k].classList.toggle("is-invalid", wrong);
                    if (wrong) { P[k].setAttribute("aria-invalid", "true"); } else { P[k].removeAttribute("aria-invalid"); }
                    if (wrong && !msg) {
                        msg = k === "depth" ? "La profundidad va de 0 a 50 m." : "El largo y el ancho van de 0 a 100.000 m.";
                        if (!first) { first = P[k]; }
                    }
                    r[k] = P[k].value.trim() && !wrong ? String(n) : "";
                });
                dimErrors(msg);
            }
            if (first) { first.focus(); return null; }
            return r;
        }

        function savePond() {
            var v = validatePond();
            if (!v) { return; }
            var isNew = !pondEditing, entry = pondEditing || {};
            Object.keys(v).forEach(function (k) { entry[k] = v[k]; });
            if (isNew) { ponds.push(entry); }
            render();
            var row = T.pondBody.children[ponds.indexOf(entry)];
            focusAfter = (isNew && !T.pondAdd.disabled) ? T.pondAdd : (row && row.querySelector(".shrimp-cert-btn"));
            pondDlg.hide();
            say((isNew ? "Se agregó " : "Se actualizó ") + entry.name + ".");
        }

        /* ---- eventos de los modales ---- */
        [[facNode, facDlg, saveFac], [pondNode, pondDlg, savePond]].forEach(function (m) {
            var node = m[0], dlg = m[1], save = m[2];
            node.querySelectorAll("[data-cm-close]").forEach(function (b) {
                b.addEventListener("click", function () { dlg.hide(); });
            });
            // Enter en un campo = Guardar (el modal no está dentro del <form>).
            node.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); }
            });
            node.addEventListener("shown.bs.modal", function () {
                var target = node === facNode ? (F.type.value ? F.name : F.type) : (P.fac.value ? P.name : P.fac);
                target.focus();
            });
            node.addEventListener("hidden.bs.modal", function () {
                // Después del Dialog (que devuelve el foco al botón que abrió).
                if (focusAfter && document.contains(focusAfter)) { focusAfter.focus(); }
                focusAfter = null;
            });
        });
        F.save.addEventListener("click", saveFac);
        P.save.addEventListener("click", savePond);
        P.fac.addEventListener("change", function () { fillPondTypes(P.type.value); });
        [P.dim, P.vol].forEach(function (r) { r.addEventListener("change", syncMode); });
        ["length", "width", "depth", "volume"].forEach(function (k) { P[k].addEventListener("input", calc); });
        [F.type, F.name, F.code, F.lat, F.lng, P.fac, P.name, P.code, P.volume].forEach(function (i) {
            i.addEventListener(i.tagName === "SELECT" ? "change" : "input", function () {
                if (i.classList.contains("is-invalid")) { err(i, ""); }
            });
        });

        T.facAdd.addEventListener("click", function () { if (!T.facAdd.disabled) { openFac(null, T.facAdd); } });
        T.pondAdd.addEventListener("click", function () { if (!T.pondAdd.disabled) { openPond(null, T.pondAdd); } });
        if (later) { later.addEventListener("change", render); }
        if (form) {
            form.addEventListener("shrimp:roles", function (e) {
                roles = (e.detail && e.detail.roles) || checkedRoles();
                render();
            });
        }

        /* Antes de salir del paso: instalaciones de un perfil desmarcado. */
        function pending(stepEl) {
            if (stepEl && !stepEl.contains(box)) { return null; }
            if (isOff()) { return null; }
            var ajenas = facs.filter(function (f) { return !kindAllowed(f.type); });
            if (!ajenas.length) { return null; }
            return "Estas instalaciones no corresponden a los perfiles que elegiste: " +
                ajenas.map(function (f) { return f.name; }).join(", ") +
                ". Cámbiales el tipo con «Editar», elimínalas o vuelve a marcar su perfil.";
        }
        if (form && form.shrimpWizard) { form.shrimpWizard.validators.push(pending); }

        /* Lo enviado, devuelto por el servidor tras un error. */
        var prefill = [];
        try { prefill = JSON.parse(box.getAttribute("data-prefill") || "[]") || []; } catch (e) { prefill = []; }
        prefill.slice(0, MAX_FAC).forEach(function (r) {
            if (!r || !kindOf(r.type)) { return; }
            var opt = r.state_id ? F.state.querySelector("option[value='" + parseInt(r.state_id, 10) + "']") : null;
            var f = {
                type: r.type, name: r.name || "", code: r.code || "",
                state_id: opt ? str(r.state_id) : "", stateName: opt ? opt.textContent.trim() : "",
                city: r.city || "", address: r.address || "",
                lat: r.lat ? String(r.lat) : "", lng: r.lng ? String(r.lng) : "",
            };
            facs.push(f);
            (r.ponds || []).forEach(function (p) {
                if (ponds.length >= MAX_POND) { return; }
                ponds.push({
                    fac: f, name: p.name || "", code: p.code || "",
                    type: pondAllowed(f, p.type) ? p.type : (pondTypesOf(f)[0] || {}).code,
                    mode: p.mode === "volume" ? "volume" : "dimensions",
                    length: p.length ? String(p.length) : "", width: p.width ? String(p.width) : "",
                    depth: p.depth ? String(p.depth) : "", volume: p.volume ? String(p.volume) : "",
                });
            });
        });
        render();
    }

    // Con defer este script corre antes de DOMContentLoaded y después de
    // signup_wizard.js, que arranca en DOMContentLoaded: se espera a ese
    // evento para que form.shrimpWizard ya exista (boot es idempotente).
    if (document.readyState === "complete") {
        boot();
    } else {
        document.addEventListener("DOMContentLoaded", boot);
        window.addEventListener("load", boot);
    }
})();
