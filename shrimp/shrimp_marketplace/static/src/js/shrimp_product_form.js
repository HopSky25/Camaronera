/* ==========================================================================
   CamaronMarket — Publicar / editar producto (/marketplace/products/new y
   /marketplace/products/<ref>/edit). Plantilla: product_new_form.

   JS sin dependencias. Hace:
     · etapas por perfil, presentación/talla solo en camarón, tallas por
       presentación, piscinas por instalación (con su contexto);
     · fotos: arrastrar y soltar, miniaturas, portada, reordenar y quitar
       (photo_files + photo_order + remove_photo_<token>);
     · certificados en tarjetas con un modal (Bootstrap) para agregar/editar:
       al «Agregar», los datos pasan a inputs ocultos de la tarjeta y el
       archivo se mueve a ella con los nombres que espera el controlador;
     · vista previa en vivo de la tarjeta del marketplace, checklist, paso a
       paso con estado y validación en línea antes de enviar.
   Nada de innerHTML con datos: todo texto entra con textContent.
   ========================================================================== */
(function () {
    "use strict";

    var MAX_BYTES = 5 * 1024 * 1024;
    var IMG_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
    var CERT_TYPES = ["application/pdf", "image/jpeg", "image/png"];
    var STEP_TITLES = {1: "Información básica", 2: "Producción y calidad", 3: "Información comercial", 4: "Certificados"};
    var TAB_BY_TIPO = {nauplio: "Nauplio", larva: "Larva", camaron: "Camarón"};

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text != null) { n.textContent = text; }
        return n;
    }
    function icon(name) {
        var i = el("i", "fa " + name);
        i.setAttribute("aria-hidden", "true");
        return i;
    }
    function num(v) {
        var n = parseFloat(String(v == null ? "" : v).replace(",", "."));
        return isNaN(n) ? null : n;
    }
    function fmtNum(n, dec) {
        if (n == null) { return "—"; }
        return n.toLocaleString("en-US", {minimumFractionDigits: dec || 0, maximumFractionDigits: dec == null ? 2 : dec});
    }
    function fmtDate(iso) {
        if (!iso) { return ""; }
        var p = String(iso).slice(0, 10).split("-");
        return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : iso;
    }
    function parseISO(iso) {
        if (!iso) { return null; }
        var p = String(iso).slice(0, 10).split("-");
        if (p.length !== 3) { return null; }
        return Date.UTC(+p[0], +p[1] - 1, +p[2]);
    }
    function singular(name) {
        var s = String(name || "").trim().toLowerCase();
        if (/[^aeiou]es$/.test(s)) { return s.slice(0, -2); }
        if (/s$/.test(s)) { return s.slice(0, -1); }
        return s;
    }
    function show(node, on) { if (node) { node.hidden = !on; } }

    // ---------------------------------------------------------------- modal
    // Bootstrap 5 (global Modal en el frontend de Odoo) con plan B mínimo.
    function Dialog(node) {
        var M = window.Modal || (window.bootstrap && window.bootstrap.Modal);
        var opener = null;
        var inst = M && M.getOrCreateInstance ? M.getOrCreateInstance(node, {backdrop: true, keyboard: true, focus: true}) : null;
        var backdrop = null;
        node.addEventListener("hidden.bs.modal", function () {
            if (opener && opener.focus && document.contains(opener)) { opener.focus(); }
        });
        function fbHide() {
            node.classList.remove("show");
            node.style.display = "none";
            node.setAttribute("aria-hidden", "true");
            document.body.classList.remove("modal-open");
            if (backdrop) { backdrop.remove(); backdrop = null; }
            node.dispatchEvent(new Event("hidden.bs.modal"));
        }
        if (!inst) {
            node.addEventListener("keydown", function (e) { if (e.key === "Escape") { fbHide(); } });
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

    function init() {
        var form = document.getElementById("product_create_form");
        if (!form || !form.classList.contains("pf")) { return; }
        form.classList.add("pf-js");
        var $ = function (s, r) { return (r || form).querySelector(s); };
        var $$ = function (s, r) { return Array.prototype.slice.call((r || form).querySelectorAll(s)); };
        var ds = form.dataset;
        var cfg = {
            tipos: JSON.parse(ds.tipos || "{}"),
            uomRol: JSON.parse(ds.uomRol || "{}"),
            internal: ds.internal === "1",
            edit: ds.edit === "1",
            draft: ds.draft === "1",
            locked: ds.locked === "1",
            maxPhotos: parseInt(ds.maxPhotos || "20", 10),
            today: parseISO(ds.today) || Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()),
            verifRoles: (ds.verifRoles || "").split(",").filter(Boolean),
        };
        var seller = $("#pfSeller");
        var uomTouched = false;
        var initialStage = checked("stage_id");
        var initialTipo = initialStage ? initialStage.dataset.tipo : "";

        function checked(name) { return form.querySelector('input[name="' + name + '"]:checked'); }
        function role() {
            if (cfg.internal && !cfg.edit && seller) {
                var o = seller.options[seller.selectedIndex];
                return (o && o.dataset.role) || "";
            }
            return ds.role || "";
        }
        function stageInfo() {
            var s = checked("stage_id");
            return {node: s, code: s ? (s.dataset.code || "") : "", tipo: s ? s.dataset.tipo : "", label: s ? s.dataset.label : ""};
        }
        function tipoActual() {
            var t = stageInfo().tipo;
            if (t) { return t; }
            var r = role();
            return r === "camaronera" ? "camaron" : (r ? "larva" : "");
        }

        // -------------------------------------------------- perfil → etapas
        function applyRole() {
            var r = role();
            var tipos = cfg.tipos[r] || null;
            $$(".pf-cgrp").forEach(function (g) {
                var tipo = g.dataset.tipo;
                var legacy = cfg.edit && initialTipo === tipo;
                var off = !!(tipos && tipos.indexOf(tipo) < 0 && !legacy);
                g.classList.toggle("off", off);
                $$('input[name="stage_id"]', g).forEach(function (i) {
                    if (off && i.checked) { i.checked = false; }
                    i.disabled = off || cfg.locked;
                });
            });
            var lbl = $("#pfRoleLbl");
            if (lbl && cfg.internal && !cfg.edit) {
                var o = seller && seller.options[seller.selectedIndex];
                lbl.textContent = r ? ("Perfil del vendedor: " + r.charAt(0).toUpperCase() + r.slice(1)) : "Elige primero el vendedor";
                if (o && !r) { lbl.textContent = "Elige primero el vendedor"; }
            }
            // Unidad por defecto del eslabón (solo en alta y si no la tocó).
            if (cfg.internal && !cfg.edit && !uomTouched && cfg.uomRol[r]) {
                var u = form.querySelector('input[name="uom_id"][value="' + cfg.uomRol[r] + '"]');
                if (u) { u.checked = true; }
            }
            // Avisos/insignias que dependen del perfil (shrimp_verification).
            $$("[data-pf-role-show]").forEach(function (n) {
                show(n, n.dataset.pfRoleShow.split(",").indexOf(r) >= 0);
            });
            $$("[data-pf-role-hide]").forEach(function (n) {
                show(n, !!r && n.dataset.pfRoleHide.split(",").indexOf(r) < 0);
            });
            // Catálogo de certificados del perfil.
            $$("#pfCeCert option").forEach(function (o) {
                if (!o.value) { return; }
                var ok = !r || o.dataset.role === "all" || o.dataset.role === r;
                o.hidden = !ok;
                o.disabled = !ok;
            });
            var sl = $("#pfPrevSeller");
            if (sl && cfg.internal && !cfg.edit && seller) {
                var so = seller.options[seller.selectedIndex];
                sl.textContent = (so && so.dataset.name) || "Vendedor";
            }
        }

        // -------------------------------------- presentación y talla (camarón)
        function applyStage() {
            var st = stageInfo();
            var tipo = tipoActual();
            var camaron = st.tipo === "camaron";
            var pres = $("#pfPresBlk");
            if (pres) {
                pres.hidden = !camaron;
                if (!camaron && !cfg.locked) {
                    $$('input[name="presentation"], input[name="size_grade_id"]').forEach(function (i) { i.checked = false; });
                }
            }
            $$("[data-show-code]").forEach(function (n) {
                show(n, n.dataset.showCode === st.code);
            });
            var engorde = st.code === "ENGORDE";
            $$('#pfClReq [data-check="presentation"], #pfClReq [data-check="size_grade"]').forEach(function (li) { show(li, engorde); });
            $$('#pfClRec [data-check="survgen"], #pfClRec [data-check="pcr"]').forEach(function (li) { show(li, tipo !== "camaron"); });
            $$('#pfClRec [data-check="origin"]').forEach(function (li) { show(li, tipo === "camaron"); });
            applySizes();
        }
        function applySizes() {
            var p = checked("presentation");
            var pv = p ? p.value : "";
            var any = false;
            $$("#pfSizes .pf-chip").forEach(function (c) {
                var ok = !!pv && c.dataset.presentation === pv;
                c.hidden = !ok;
                var i = c.querySelector("input");
                if (!ok && i.checked && !cfg.locked) { i.checked = false; }
                if (ok) { any = true; }
            });
            show($("#pfSizesEmpty"), !any);
        }

        // ------------------------------------------- piscina por instalación
        var pond = $("#originPond");
        function applyPonds() {
            if (!pond) { return; }
            var f = checked("origin_facility_ref");
            var fv = f ? f.value : "";
            var ok = false;
            $$("option", pond).forEach(function (o) {
                if (!o.value) { return; }
                var vis = !fv || o.dataset.facility === fv;
                o.hidden = !vis;
                o.disabled = !vis;
                if (vis && o.selected) { ok = true; }
            });
            if (!ok) { pond.value = ""; }
            pondCtx();
        }
        function pondCtx() {
            var box = $("#pfPondCtx");
            if (!box || !pond) { return; }
            var o = pond.options[pond.selectedIndex];
            box.textContent = "";
            if (!o || !o.value) { box.hidden = true; return; }
            [["Tipo", o.dataset.type], [o.dataset.sizeK, o.dataset.sizeV], ["Ubicación", o.dataset.loc]].forEach(function (kv) {
                if (!kv[1]) { return; }
                var s = el("span", null, kv[0] + " ");
                s.appendChild(el("b", null, kv[1]));
                box.appendChild(s);
            });
            box.hidden = false;
        }

        // ================================================================ FOTOS
        var thumbs = $("#pfThumbs");
        var addTile = $("#pfThumbAdd");
        var picker = $("#pfPhotoPicker");
        var realFiles = $("#pfPhotoFiles");
        var orderInp = $("#pfPhotoOrder");
        var photoErr = $("#pfPhotoErr");
        var newPhotos = [];
        var photoSeq = 0;
        var dragged = null;

        function photoThumbs() { return $$(".pf-th:not(.pf-th-add)", thumbs); }
        function decorateThumb(th) {
            if (th.dataset.decorated) { return; }
            th.dataset.decorated = "1";
            var grip = el("span", "pf-grip");
            grip.setAttribute("aria-hidden", "true");
            grip.appendChild(icon("fa-arrows"));
            var x = el("button", "pf-th-x");
            x.type = "button";
            x.setAttribute("aria-label", "Quitar foto");
            x.title = "Quitar";
            x.appendChild(icon("fa-times"));
            var cover = el("span", "pf-th-cover");
            cover.appendChild(icon("fa-star"));
            cover.appendChild(document.createTextNode(" Portada"));
            var mk = el("button", "pf-th-mk", "Hacer portada");
            mk.type = "button";
            th.appendChild(grip);
            th.appendChild(x);
            th.appendChild(cover);
            th.appendChild(mk);
        }
        function photoMsg(txt) {
            if (!photoErr) { return; }
            photoErr.textContent = "";
            if (txt) { photoErr.appendChild(icon("fa-exclamation-circle")); photoErr.appendChild(document.createTextNode(" " + txt)); }
            photoErr.hidden = !txt;
        }
        function syncFiles() {
            if (!realFiles || typeof DataTransfer === "undefined") { return; }
            var dt = new DataTransfer();
            newPhotos.forEach(function (o) { dt.items.add(o.file); });
            realFiles.files = dt.files;
        }
        function syncPhotos() {
            var toks = [];
            var list = photoThumbs();
            list.forEach(function (th, i) {
                if (th.dataset.kind === "e") {
                    toks.push("e:" + th.dataset.token);
                } else {
                    var idx = -1;
                    newPhotos.forEach(function (o, k) { if (String(o.key) === th.dataset.key) { idx = k; } });
                    toks.push("n:" + idx);
                }
                th.classList.toggle("is-cover", i === 0);
                th.setAttribute("aria-label", (i === 0 ? "Portada. " : "") + "Foto " + (i + 1) + " de " + list.length +
                    ". Flechas izquierda y derecha para mover, Suprimir para quitar.");
            });
            if (orderInp) { orderInp.value = toks.join(","); }
            var cnt = $("#pfPhotoCount");
            if (cnt) { cnt.textContent = list.length + " de " + cfg.maxPhotos; }
            if (addTile) { addTile.hidden = list.length >= cfg.maxPhotos; }
            refresh();
        }
        function addFiles(files) {
            var errs = [];
            Array.prototype.forEach.call(files || [], function (f) {
                if (IMG_TYPES.indexOf(f.type) < 0) { errs.push("«" + f.name + "» no es una imagen JPG, PNG, WEBP o GIF."); return; }
                if (f.size > MAX_BYTES) { errs.push("«" + f.name + "» pesa más de 5 MB."); return; }
                if (photoThumbs().length >= cfg.maxPhotos) { errs.push("Máximo " + cfg.maxPhotos + " fotos."); return; }
                var key = String(++photoSeq);
                var url = URL.createObjectURL(f);
                newPhotos.push({file: f, url: url, key: key});
                var th = el("div", "pf-th");
                th.setAttribute("role", "listitem");
                th.tabIndex = 0;
                th.draggable = true;
                th.dataset.kind = "n";
                th.dataset.key = key;
                var img = el("img");
                img.src = url;
                img.alt = "";
                th.appendChild(img);
                decorateThumb(th);
                thumbs.insertBefore(th, addTile);
            });
            photoMsg(errs.length ? errs.filter(function (v, i, a) { return a.indexOf(v) === i; }).join(" ") : "");
            syncFiles();
            syncPhotos();
        }
        function removeThumb(th) {
            var next = th.nextElementSibling || th.previousElementSibling;
            if (th.dataset.kind === "e") {
                var cb = form.querySelector('#pfPhotoRemovals input[data-token="' + th.dataset.token + '"]');
                if (cb) { cb.checked = true; }
            } else {
                newPhotos = newPhotos.filter(function (o) {
                    if (String(o.key) === th.dataset.key) { URL.revokeObjectURL(o.url); return false; }
                    return true;
                });
                syncFiles();
            }
            th.remove();
            syncPhotos();
            if (next && next.focus) { next.focus(); }
        }
        if (thumbs) {
            photoThumbs().forEach(decorateThumb);
            if (picker) {
                $("#pfPhotoPick").addEventListener("click", function () { picker.click(); });
                if (addTile) { addTile.addEventListener("click", function () { picker.click(); }); }
                picker.addEventListener("change", function () { addFiles(picker.files); picker.value = ""; });
            }
            thumbs.addEventListener("click", function (e) {
                var th = e.target.closest(".pf-th");
                if (!th || th === addTile) { return; }
                if (e.target.closest(".pf-th-x")) { removeThumb(th); return; }
                if (e.target.closest(".pf-th-mk")) {
                    thumbs.insertBefore(th, thumbs.firstElementChild);
                    syncPhotos();
                    th.focus();
                }
            });
            thumbs.addEventListener("keydown", function (e) {
                var th = e.target.closest(".pf-th");
                if (!th || th === addTile || e.target !== th) { return; }
                if (e.key === "ArrowLeft" && th.previousElementSibling) {
                    thumbs.insertBefore(th, th.previousElementSibling);
                } else if (e.key === "ArrowRight" && th.nextElementSibling && th.nextElementSibling !== addTile) {
                    thumbs.insertBefore(th.nextElementSibling, th);
                } else if (e.key === "Delete" || e.key === "Backspace") {
                    e.preventDefault();
                    removeThumb(th);
                    return;
                } else {
                    return;
                }
                e.preventDefault();
                syncPhotos();
                th.focus();
            });
            thumbs.addEventListener("dragstart", function (e) {
                var th = e.target.closest(".pf-th");
                if (!th || th === addTile) { return; }
                dragged = th;
                th.classList.add("is-drag");
                e.dataTransfer.effectAllowed = "move";
                try { e.dataTransfer.setData("text/plain", "pf-photo"); } catch (err) { /* IE */ }
            });
            thumbs.addEventListener("dragend", function () {
                if (dragged) { dragged.classList.remove("is-drag"); }
                dragged = null;
                syncPhotos();
            });
            thumbs.addEventListener("dragover", function (e) {
                if (!dragged) { return; }
                e.preventDefault();
                var over = e.target.closest(".pf-th");
                if (!over || over === dragged) { return; }
                if (over === addTile) { thumbs.insertBefore(dragged, addTile); return; }
                var r = over.getBoundingClientRect();
                var after = (e.clientX - r.left) > r.width / 2;
                thumbs.insertBefore(dragged, after ? over.nextElementSibling : over);
            });
            var hasFiles = function (e) {
                return !dragged && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0;
            };
            [$("#pfDrop"), thumbs].forEach(function (zone) {
                if (!zone) { return; }
                zone.addEventListener("dragover", function (e) { if (hasFiles(e)) { e.preventDefault(); zone.classList.add("is-over"); } });
                zone.addEventListener("dragleave", function () { zone.classList.remove("is-over"); });
                zone.addEventListener("drop", function (e) {
                    zone.classList.remove("is-over");
                    if (!hasFiles(e)) { if (dragged) { e.preventDefault(); } return; }
                    e.preventDefault();
                    addFiles(e.dataTransfer.files);
                });
            });
            syncPhotos();
        }

        // ========================================================= CERTIFICADOS
        var certList = $("#pfCertList");
        var certModal = $("#pfCertModal") ? Dialog($("#pfCertModal")) : null;
        var delModal = $("#pfCertDelModal") ? Dialog($("#pfCertDelModal")) : null;
        var ce = {
            cert: $("#pfCeCert"), issuer: $("#pfCeIssuer"), number: $("#pfCeNumber"),
            issue: $("#pfCeIssue"), expiry: $("#pfCeExpiry"), drop: $("#pfCeDrop"),
            slot: $("#pfCeFileSlot"), fname: $("#pfCeFileName"), save: $("#pfCeSave"),
        };
        var ceMode = "new";
        var ceCard = null;
        var ceFile = null;
        var newCertIdx = 5000;
        var pendingDel = null;

        function validity(expiry) {
            var t = parseISO(expiry);
            if (!t) { return {cls: "ok", txt: "Vigente", state: "ok"}; }
            var days = Math.round((t - cfg.today) / 86400000);
            if (days < 0) { return {cls: "bad", txt: "Vencido", state: "exp"}; }
            if (days <= 30) { return {cls: "warn", txt: "Por vencer · " + days + (days === 1 ? " día" : " días"), state: "soon"}; }
            return {cls: "ok", txt: "Vigente", state: "ok"};
        }
        function activeCards() { return $$(".pf-cert", certList).filter(function (c) { return !c.classList.contains("is-removed"); }); }

        function renderCard(card) {
            var d = card.dataset;
            var kind = d.kind;
            // Conserva inputs ocultos/archivos; reconstruye lo visible.
            var keep = $$("input", card);
            card.textContent = "";
            var v = validity(d.expiry);
            card.classList.toggle("c-exp", v.state === "exp");
            card.classList.toggle("c-soon", v.state === "soon");
            var ico = el("div", "pf-cert-ico");
            ico.setAttribute("aria-hidden", "true");
            ico.appendChild(icon(v.state === "exp" ? "fa-exclamation-triangle" : (/^PCR/i.test(d.code || "") ? "fa-shield" : "fa-file-text-o")));
            var body = el("div", "pf-cert-body");
            var t = el("div", "pf-cert-t");
            t.appendChild(el("span", "pf-cert-name", d.name || "Certificado"));
            if (kind === "perfil" || kind === "usuario") {
                t.appendChild(el("span", "pf-tag pf-t-user", "De tu perfil"));
            } else {
                t.appendChild(el("span", "pf-tag pf-t-prod", "Del producto"));
            }
            if (kind === "nuevo") {
                t.appendChild(el("span", "pf-tag pf-t-new", "Nuevo · se guarda con el producto"));
            } else if (kind === "producto" && d.status === "pending") {
                t.appendChild(el("span", "pf-tag pf-t-rev", "En revisión"));
            } else if (kind === "producto" && d.status === "rejected") {
                t.appendChild(el("span", "pf-tag pf-t-rej", "Rechazado"));
            }
            t.appendChild(el("span", "pf-chip-st pf-chip-xs pf-st-" + v.cls, v.txt));
            body.appendChild(t);
            var meta = [];
            if (d.issuer) { meta.push(d.issuer); }
            if (v.state === "exp") {
                meta.push("venció " + fmtDate(d.expiry));
                meta.push("sube uno nuevo o quítalo");
            } else {
                if (d.number) { meta.push("N° " + d.number); }
                if (d.issue) { meta.push("emitido " + fmtDate(d.issue)); }
                if (d.expiry) { meta.push("vence " + fmtDate(d.expiry)); }
            }
            if (kind === "nuevo" && d.filename) { meta.push(d.filename); }
            body.appendChild(el("div", "pf-cert-m", meta.join(" · ")));
            var acts = el("div", "pf-cert-a");
            var nm = d.name || "certificado";
            function btn(cls, ic, label) {
                var b = el("button", "pf-ib " + cls);
                b.type = "button";
                b.title = label;
                b.setAttribute("aria-label", label + " " + nm);
                b.appendChild(icon(ic));
                acts.appendChild(b);
                return b;
            }
            if (d.url) { btn("pf-cert-view", "fa-eye", "Ver"); }
            if (kind === "producto" || kind === "nuevo") {
                var eb = btn("pf-cert-edit", "fa-pencil", "Editar");
                eb.setAttribute("aria-haspopup", "dialog");
            }
            btn("pf-cert-del del", "fa-trash", "Quitar");
            var hidden = el("span", "pf-cert-hidden");
            keep.forEach(function (i) { hidden.appendChild(i); });
            card.appendChild(ico);
            card.appendChild(body);
            card.appendChild(acts);
            card.appendChild(hidden);
        }

        function ceError(field, msg) {
            var map = {cert: "#pfCeCertErr", issue: "#pfCeIssueErr", expiry: "#pfCeExpiryErr", file: "#pfCeFileErr"};
            var box = $(map[field]);
            if (!box) { return; }
            var span = box.querySelector("span");
            if (span) { span.textContent = msg || ""; }
            box.hidden = !msg;
            var f = form.querySelector('[data-mfield="' + field + '"]');
            if (f) { f.classList.toggle("pf-err", !!msg); }
            var inp = {cert: ce.cert, issue: ce.issue, expiry: ce.expiry, file: ce.drop}[field];
            if (inp) { inp.setAttribute("aria-invalid", msg ? "true" : "false"); }
        }
        function clearCeErrors() { ["cert", "issue", "expiry", "file"].forEach(function (f) { ceError(f, ""); }); }
        function syncIssuer() {
            var o = ce.cert.options[ce.cert.selectedIndex];
            ce.issuer.value = (o && o.value) ? (o.dataset.issuer || "") : "";
        }
        function newFileInput() {
            ce.slot.textContent = "";
            ceFile = el("input", "pf-vh");
            ceFile.type = "file";
            ceFile.accept = "application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png";
            ceFile.tabIndex = -1;
            ceFile.setAttribute("aria-hidden", "true");
            ceFile.addEventListener("change", function () { fileChosen(); });
            ce.slot.appendChild(ceFile);
            setFileLabel(null);
        }
        function setFileLabel(f) {
            ce.fname.textContent = "";
            if (f) {
                ce.fname.appendChild(el("b", null, f.name));
                ce.fname.appendChild(document.createTextNode(" · " + fmtNum(f.size / 1024 / 1024, 2) + " MB · "));
                ce.fname.appendChild(el("u", null, "cambiar"));
            } else {
                ce.fname.appendChild(document.createTextNode("Arrastra el PDF aquí o "));
                ce.fname.appendChild(el("b", null, "elige un archivo"));
            }
            ce.drop.classList.toggle("has-file", !!f);
        }
        function fileProblem(f) {
            if (!f) { return ""; }
            var okType = CERT_TYPES.indexOf(f.type) >= 0 || /\.(pdf|jpe?g|png)$/i.test(f.name || "");
            if (!okType) { return "Solo PDF, JPG o PNG."; }
            if (f.size > MAX_BYTES) { return "El archivo pesa más de 5 MB."; }
            return "";
        }
        function fileChosen() {
            var f = ceFile && ceFile.files && ceFile.files[0];
            setFileLabel(f || null);
            ceError("file", fileProblem(f));
        }
        function openCert(mode, card, from) {
            if (!certModal) { return; }
            ceMode = mode;
            ceCard = card || null;
            clearCeErrors();
            newFileInput();
            var d = card ? card.dataset : {};
            var title = $("#pfCertModalT span");
            var saveTxt = ce.save.querySelector("span");
            title.textContent = mode === "new" ? "Agregar certificado" : "Editar certificado";
            saveTxt.textContent = mode === "new" ? "Agregar" : "Guardar cambios";
            ce.cert.value = d.cert || "";
            ce.cert.disabled = !!(card && card.dataset.kind === "producto");
            syncIssuer();
            if (card && !ce.cert.value) { ce.issuer.value = d.issuer || ""; }
            ce.number.value = d.number || "";
            ce.issue.value = (d.issue || "").slice(0, 10);
            ce.expiry.value = (d.expiry || "").slice(0, 10);
            show($("#pfCeFileHint"), mode !== "new");
            show($("#pfCeFileReq"), mode === "new");
            certModal.show(from);
        }
        function saveCert() {
            clearCeErrors();
            var f = ceFile && ceFile.files && ceFile.files[0];
            var errs = [];
            var o = ce.cert.options[ce.cert.selectedIndex];
            if (!ce.cert.disabled && !ce.cert.value) { ceError("cert", "Elige el certificado del catálogo."); errs.push(ce.cert); }
            if (ce.issue.value && ce.expiry.value && ce.expiry.value < ce.issue.value) {
                ceError("expiry", "El vencimiento no puede ser anterior a la emisión."); errs.push(ce.expiry);
            }
            if (ce.issue.value && parseISO(ce.issue.value) > cfg.today) {
                ceError("issue", "La fecha de emisión no puede ser futura."); errs.push(ce.issue);
            }
            var fp = fileProblem(f);
            if (fp) { ceError("file", fp); errs.push(ce.drop); }
            else if (ceMode === "new" && !f) { ceError("file", "Adjunta el archivo del certificado."); errs.push(ce.drop); }
            if (errs.length) { errs[0].focus(); return; }

            var card = ceCard;
            if (ceMode === "new") {
                var idx = newCertIdx++;
                card = el("div", "pf-cert");
                card.dataset.kind = "nuevo";
                card.dataset.idx = String(idx);
                [["id", ""], ["number", "pf-ci-number"], ["issue_date", "pf-ci-issue"], ["expiry_date", "pf-ci-expiry"]].forEach(function (p) {
                    var h = el("input", p[1] || "pf-ci-cert");
                    h.type = "hidden";
                    h.name = "prod_cert_" + idx + "_" + p[0];
                    card.appendChild(h);
                });
                card.dataset.fname = "prod_cert_" + idx + "_file";
                certList.appendChild(card);
            }
            var d = card.dataset;
            if (!ce.cert.disabled) {
                d.cert = ce.cert.value;
                d.name = o ? o.textContent.trim() : d.name;
                d.issuer = o ? (o.dataset.issuer || "") : d.issuer;
                d.code = o ? (o.dataset.code || "") : d.code;
                var hc = card.querySelector(".pf-ci-cert");
                if (hc) { hc.value = ce.cert.value; }
            }
            d.number = ce.number.value.trim();
            d.issue = ce.issue.value;
            d.expiry = ce.expiry.value;
            [["pf-ci-number", d.number], ["pf-ci-issue", d.issue], ["pf-ci-expiry", d.expiry]].forEach(function (p) {
                var h = card.querySelector("." + p[0]);
                if (h) { h.value = p[1]; }
            });
            if (f) {
                var prev = card.querySelector(".pf-ci-file");
                if (prev) { prev.remove(); }
                if (d.blob) { URL.revokeObjectURL(d.blob); }
                ceFile.name = d.fname;
                ceFile.className = "pf-ci-file d-none";
                card.appendChild(ceFile);
                ceFile = null;
                d.blob = URL.createObjectURL(f);
                d.url = d.blob;
                d.filename = f.name;
            }
            if (d.kind === "producto") { d.status = "pending"; }  // un cambio vuelve a revisión
            renderCard(card);
            certModal.hide();
            refresh();
        }
        function viewCert(card) {
            var url = card.dataset.url;
            var modal = document.getElementById("certModal");
            var frame = document.getElementById("certFrame");
            var dl = document.getElementById("certDownload");
            if (!url || !modal || !frame) { if (url) { window.open(url, "_blank", "noopener"); } return; }
            frame.src = url;
            if (dl) {
                dl.href = url.indexOf("blob:") === 0 ? url : url + (url.indexOf("?") >= 0 ? "&" : "?") + "download=1";
                if (url.indexOf("blob:") === 0) { dl.setAttribute("download", card.dataset.filename || "certificado"); } else { dl.removeAttribute("download"); }
            }
            modal.classList.add("open");
        }
        if (certList) {
            $$(".pf-cert", certList).forEach(renderCard);
            $("#pfCertAdd").addEventListener("click", function (e) { openCert("new", null, e.currentTarget); });
            certList.addEventListener("click", function (e) {
                var card = e.target.closest(".pf-cert");
                if (!card) { return; }
                if (e.target.closest(".pf-cert-view")) { viewCert(card); return; }
                if (e.target.closest(".pf-cert-edit")) { openCert("edit", card, e.target.closest("button")); return; }
                if (e.target.closest(".pf-cert-del")) {
                    pendingDel = card;
                    $("#pfCertDelTxt").textContent = "¿Quitar «" + (card.dataset.name || "certificado") + "» de este producto? Se aplica al guardar.";
                    if (delModal) { delModal.show(e.target.closest("button")); }
                }
            });
            if (ce.cert) { ce.cert.addEventListener("change", function () { syncIssuer(); ceError("cert", ""); }); }
            ce.drop.addEventListener("click", function () { if (ceFile) { ceFile.click(); } });
            ce.drop.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (ceFile) { ceFile.click(); } }
            });
            ce.drop.addEventListener("dragover", function (e) { e.preventDefault(); ce.drop.classList.add("is-over"); });
            ce.drop.addEventListener("dragleave", function () { ce.drop.classList.remove("is-over"); });
            ce.drop.addEventListener("drop", function (e) {
                e.preventDefault();
                ce.drop.classList.remove("is-over");
                var files = e.dataTransfer && e.dataTransfer.files;
                if (!files || !files.length || !ceFile || typeof DataTransfer === "undefined") { return; }
                var dt = new DataTransfer();
                dt.items.add(files[0]);
                ceFile.files = dt.files;
                fileChosen();
            });
            ce.save.addEventListener("click", saveCert);
            // El modal vive dentro del <form>: Enter no debe enviar el producto.
            certModal.node.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); saveCert(); }
            });
            certModal.node.addEventListener("shown.bs.modal", function () {
                (ce.cert.disabled ? ce.number : ce.cert).focus();
            });
            $$("[data-pf-close]").forEach(function (b) {
                b.addEventListener("click", function () {
                    var m = b.closest(".modal");
                    if (m === certModal.node) { certModal.hide(); } else if (delModal && m === delModal.node) { delModal.hide(); pendingDel = null; }
                });
            });
            $("#pfCertDelOk").addEventListener("click", function () {
                var card = pendingDel;
                pendingDel = null;
                if (card) {
                    var flag = card.querySelector(".pf-cert-remove");
                    if (flag) {
                        flag.checked = true;
                        card.classList.add("is-removed");
                        card.hidden = true;
                    } else {
                        if (card.dataset.blob) { URL.revokeObjectURL(card.dataset.blob); }
                        card.remove();
                    }
                }
                delModal.hide();
                var add = $("#pfCertAdd");
                if (add) { setTimeout(function () { add.focus(); }, 0); }
                refresh();
            });
        }

        // ===================================================== VALIDACIÓN
        function fieldError(key, on) {
            var msg = form.querySelector('[data-err-for="' + key + '"]');
            show(msg, on);
            var f = msg && (msg.closest(".pf-f") || msg.closest(".pf-blk"));
            if (f) { f.classList.toggle("pf-err", on); }
        }
        function checks() {
            var st = stageInfo();
            var engorde = st.code === "ENGORDE";
            var qty = num($("#pfQty") && $("#pfQty").value);
            var priceInp = $("#pfPrice");
            var priceV = priceInp ? priceInp.value.trim() : "";
            var price = num(priceV);
            var locked = cfg.locked;
            var req = {
                seller: !(cfg.internal && !cfg.edit) || !!(seller && seller.value),
                name: !!($("#pfName") && $("#pfName").value.trim()),
                stage: !!st.node,
                qty: locked || (qty != null && qty > 0),
                price: locked || (priceV !== "" && price != null && price >= 0),
                presentation: !engorde || locked || !!checked("presentation"),
                size_grade: !engorde || locked || !!checked("size_grade_id"),
            };
            var tipo = tipoActual();
            var cards = activeCards();
            var expired = cards.filter(function (c) { return validity(c.dataset.expiry).state === "exp"; }).length;
            var pcr = cards.some(function (c) { return /^PCR/i.test(c.dataset.code || "") && validity(c.dataset.expiry).state !== "exp"; });
            var fac = checked("origin_facility_ref");
            var allocs = $$('input[name="origin_allocation_ref"]:checked').length;
            var rec = {
                photos: photoThumbs().length > 0,
                locdate: !!($("#pfLocation") && $("#pfLocation").value) && !!($("#pfDelivery") && $("#pfDelivery").value),
                survgen: tipo !== "camaron" && !!num($("#pfSurv") && $("#pfSurv").value) && !!($("#pfGenetics") && $("#pfGenetics").value),
                origin: tipo === "camaron" && !!(pond && pond.value) && (allocs > 0 || !$("#pfAllocBlk")),
                pcr: pcr,
                expired: expired === 0,
            };
            var optional = [
                num($("#pfSurv") && $("#pfSurv").value), num($("#pfSize") && $("#pfSize").value),
                $("#pfLocation") && $("#pfLocation").value, $("#pfDelivery") && $("#pfDelivery").value,
                fac && fac.value, pond && pond.value, $("#pfHealth") && $("#pfHealth").value.trim(),
            ].filter(Boolean).length;
            return {req: req, rec: rec, engorde: engorde, expired: expired, cards: cards.length,
                    optional: optional, tipo: tipo, photos: photoThumbs().length, allocs: allocs};
        }
        var REQ_LABEL = {seller: "vendedor", name: "nombre", stage: "etapa", qty: "cantidad", price: "precio",
                         presentation: "presentación", size_grade: "talla"};
        var STEP_REQ = {1: ["seller", "name", "stage"], 3: ["qty", "price", "presentation", "size_grade"]};

        function setChip(node, cls, txt, ic) {
            if (!node) { return; }
            node.className = "pf-chip-st pf-st-" + cls;
            node.textContent = "";
            if (ic) { node.appendChild(icon(ic)); node.appendChild(document.createTextNode(" ")); }
            node.appendChild(document.createTextNode(txt));
        }
        var curStep = 1;
        function refresh() {
            if (refresh.raf) { return; }
            refresh.raf = requestAnimationFrame(function () { refresh.raf = null; doRefresh(); });
        }
        function doRefresh() {
            var c = checks();
            var pendReq = Object.keys(c.req).filter(function (k) { return !c.req[k]; });
            var totalReq = Object.keys(c.req).filter(function (k) {
                if ((k === "presentation" || k === "size_grade") && !c.engorde) { return false; }
                if (k === "seller" && !(cfg.internal && !cfg.edit)) { return false; }
                return true;
            });
            var okReq = totalReq.filter(function (k) { return c.req[k]; }).length;

            // Checklist
            $$("#pfClReq li").forEach(function (li) {
                var ok = c.req[li.dataset.check];
                li.className = ok ? "ok" : "pend";
                setCi(li, ok ? "fa-check" : "fa-exclamation");
            });
            $$("#pfClRec li").forEach(function (li) {
                var k = li.dataset.check;
                var ok = c.rec[k];
                if (k === "expired") { show(li, c.expired > 0); }
                li.classList.remove("ok", "rec");
                li.classList.add(ok ? "ok" : "rec");
                setCi(li, ok ? "fa-check" : null);
                if (k === "photos") { li.querySelector("a").textContent = "Fotos" + (c.photos ? " (" + c.photos + ")" : ""); }
                if (k === "expired") { li.querySelector("a").textContent = c.expired > 1 ? "Renovar " + c.expired + " certificados vencidos" : "Renovar certificado vencido"; }
            });
            var bar = $("#pfClBar");
            if (bar) { bar.style.width = (totalReq.length ? Math.round(okReq * 100 / totalReq.length) : 100) + "%"; bar.classList.toggle("is-full", okReq === totalReq.length); }
            var cnt = $("#pfClCount");
            if (cnt) { cnt.textContent = okReq + " de " + totalReq.length; }

            // Paso a paso
            var stepState = {};
            [1, 3].forEach(function (s) {
                var pend = STEP_REQ[s].filter(function (k) { return totalReq.indexOf(k) >= 0 && !c.req[k]; });
                stepState[s] = pend.length ? {cls: "warn", sub: "Falta " + pend.map(function (k) { return REQ_LABEL[k]; }).join(", "), chip: ["warn", pend.length + " pendiente" + (pend.length > 1 ? "s" : ""), "fa-exclamation-triangle"]}
                    : {cls: "done", sub: "Completo", chip: ["ok", "Completo", "fa-check"]};
            });
            stepState[2] = {cls: c.optional >= 3 ? "done" : "", sub: "Opcional · " + c.optional + "/7", chip: ["opt", "Opcional", null]};
            if (c.expired) {
                stepState[4] = {cls: "warn", sub: c.expired + " vencido" + (c.expired > 1 ? "s" : ""), chip: ["bad", c.expired + " vencido" + (c.expired > 1 ? "s" : ""), "fa-exclamation-triangle"]};
            } else if (c.cards) {
                stepState[4] = {cls: "done", sub: c.cards + " adjunto" + (c.cards > 1 ? "s" : ""), chip: ["info", c.cards + " adjunto" + (c.cards > 1 ? "s" : ""), null]};
            } else {
                stepState[4] = {cls: "", sub: "Opcional", chip: ["opt", "Opcional", null]};
            }
            [1, 2, 3, 4].forEach(function (s) {
                var st = stepState[s];
                var a = form.querySelector('.pf-step[data-step="' + s + '"]');
                if (a) {
                    a.classList.remove("done", "warn");
                    if (st.cls) { a.classList.add(st.cls); }
                    a.classList.toggle("cur", s === curStep);
                    if (s === curStep) { a.setAttribute("aria-current", "step"); } else { a.removeAttribute("aria-current"); }
                    a.querySelector(".pf-step-s").textContent = st.sub;
                }
                var sec = form.querySelector('#pfSec' + s + ' .pf-sec-h .pf-step-n');
                if (sec) { sec.className = "pf-step-n" + (st.cls ? " " + st.cls : "") + (s === curStep ? " cur" : ""); }
                setChip(form.querySelector('[data-sec-status="' + s + '"]'), st.chip[0], st.chip[1], st.chip[2]);
            });
            var pb = $("#pfBar");
            if (pb) { pb.style.width = (totalReq.length ? Math.round(okReq * 100 / totalReq.length) : 100) + "%"; }
            var lbl = $("#pfStepLabel");
            if (lbl) { lbl.textContent = curStep + " de 4 · " + STEP_TITLES[curStep]; }
            var pendTxt = pendReq.length ? (pendReq.length + " pendiente" + (pendReq.length > 1 ? "s" : "")) : "Listo para publicar";
            var sp = $("#pfStepPend");
            if (sp) { sp.textContent = pendTxt; sp.classList.toggle("is-ok", !pendReq.length); }

            // Acciones
            var ready = !pendReq.length;
            $$(".pf-btn-publish").forEach(function (b) { b.disabled = !ready; b.setAttribute("aria-disabled", ready ? "false" : "true"); });
            var note = $("#pfActNote");
            if (note) {
                var verif = cfg.verifRoles.indexOf(role()) >= 0;
                if (!cfg.draft) {
                    note.textContent = "Los cambios se ven en el marketplace al guardar.";
                } else if (ready) {
                    note.textContent = "Al publicar, el lote queda visible en el catálogo" + (verif ? " con verificación obligatoria." : ".");
                } else {
                    note.textContent = "Completa " + pendReq.map(function (k) { return REQ_LABEL[k]; }).join(", ") + " para publicar. Puedes guardar el borrador cuando quieras.";
                }
            }
            var mb = $("#pfMbarSt");
            if (mb) {
                mb.textContent = "";
                var b = el("b", ready ? "is-ok" : "", ready ? "Listo" : (pendReq.length + " pendiente" + (pendReq.length > 1 ? "s" : "")));
                mb.appendChild(b);
                mb.appendChild(document.createTextNode(cfg.draft ? "para publicar" : "para guardar"));
            }
            var ac = $("#pfAllocCount");
            if (ac) { ac.textContent = c.allocs ? c.allocs + " marcada" + (c.allocs > 1 ? "s" : "") : "Automáticas"; }
            show($("#pfCertEmpty"), !c.cards);
            preview(c);
        }
        function setCi(li, ic) {
            var ci = li.querySelector(".pf-ci");
            ci.textContent = "";
            if (ic) { ci.appendChild(icon(ic)); }
        }

        // ======================================================= VISTA PREVIA
        function uomName() {
            var u = checked("uom_id");
            return u ? (u.dataset.label || "") : "";
        }
        function preview(c) {
            var st = stageInfo();
            var tipo = c.tipo;
            var name = ($("#pfName") && $("#pfName").value.trim()) || "Nombre del producto";
            $("#pfPrevName").textContent = name;
            var first = photoThumbs()[0];
            var img = $("#pfPrevImg");
            var src = first ? first.querySelector("img").src : "";
            if (src) { img.src = src; }
            show(img, !!src);
            show($("#pfPrevNoImg"), !src);
            var badge = function (id, txt, ic) {
                var n = $(id);
                if (!n) { return; }
                if (ic !== undefined) {
                    n.textContent = "";
                    if (txt) { n.appendChild(icon(ic + " me-1")); n.appendChild(document.createTextNode(txt)); }
                } else if (txt) { n.textContent = txt; }
                show(n, !!txt);
            };
            badge("#pfPrevStage", st.label || "");
            var sp = checked("species_id");
            badge("#pfPrevSpecies", sp ? sp.dataset.label : "");
            var surv = num($("#pfSurv") && $("#pfSurv").value);
            badge("#pfPrevSurv", tipo !== "camaron" && surv ? "Superv. " + Math.round(surv) + " %" : "", "fa-heartbeat");
            var gen = $("#pfGenetics");
            var genTxt = gen && gen.value ? gen.options[gen.selectedIndex].textContent.trim() : "";
            badge("#pfPrevGen", tipo !== "camaron" ? genTxt : "", "fa-code-fork");
            show($("#pfPrevPcr"), c.rec.pcr);
            show($("#pfPrevVer"), cfg.verifRoles.indexOf(role()) >= 0);
            var pres = checked("presentation");
            var size = checked("size_grade_id");
            var presBox = $("#pfPrevPres");
            if (presBox) {
                var t = [];
                if (tipo === "camaron" && pres) { t.push(pres.dataset.label); }
                if (tipo === "camaron" && size) { t.push("Talla " + size.dataset.label); }
                presBox.querySelector("span").textContent = t.join(" · ");
                show(presBox, !!t.length);
            }
            var qtyInp = $("#pfQty");
            var qty = num(qtyInp && qtyInp.value);
            // En edición la tarjeta muestra lo DISPONIBLE (lo inicial menos lo
            // vendido o reservado), como el marketplace.
            var disp = cfg.edit && ds.available != null ? num(ds.available) : qty;
            $("#pfPrevQty").textContent = disp != null && (disp || cfg.edit) ? fmtNum(disp) : "—";
            var un = uomName();
            $("#pfPrevUom").textContent = un.toLowerCase();
            $$('[data-uom-label="lower"]').forEach(function (n) { n.textContent = un.toLowerCase(); });
            $$('[data-uom-label="singular"]').forEach(function (n) { n.textContent = singular(un) || "unidad"; });
            $("#pfPrevLoc").textContent = ($("#pfLocation") && $("#pfLocation").value) || "—";
            var dd = $("#pfDelivery") && $("#pfDelivery").value;
            var dateBox = $("#pfPrevDate");
            dateBox.querySelector("span").textContent = fmtDate(dd);
            show(dateBox, !!dd);
            var priceInp = $("#pfPrice");
            var price = num(priceInp && priceInp.value);
            var pr = $("#pfPrevPrice");
            pr.textContent = "";
            pr.classList.toggle("is-empty", price == null);
            pr.appendChild(document.createTextNode("$"));
            if (price == null) { pr.appendChild(el("span", "pf-skel")); } else { pr.appendChild(document.createTextNode(fmtNum(price, 2))); }
            var u = el("span", "s-unit", " / " + (singular(un) || "unidad"));
            pr.appendChild(u);
            var tot = $("#pfTotal");
            if (tot) {
                tot.textContent = (qty && price != null) ? ("$" + fmtNum(qty * price, 2) + " · " + fmtNum(qty) + " " + un.toLowerCase()) : "—";
            }
            var tab = $("#pfPrevTab");
            if (tab) { tab.textContent = tipo ? "pestaña " + TAB_BY_TIPO[tipo] : "pestaña según la etapa"; }
            // Resumen plegable (móvil)
            $("#pfMprevName").textContent = name;
            $("#pfMprevSub").textContent = "Vista previa · " + (price == null ? "$ —" : "$" + fmtNum(price, 2)) + " / " + (singular(un) || "unidad");
            var th = $("#pfMprevTh");
            if (th) {
                th.textContent = "";
                if (src) { var im = el("img"); im.src = src; im.alt = ""; th.appendChild(im); } else { th.appendChild(icon("fa-image")); }
            }
        }
        // La tarjeta vive en el panel de escritorio o dentro del plegable móvil.
        var mq = window.matchMedia ? window.matchMedia("(max-width: 991.98px)") : null;
        function placePreview() {
            var card = $("#pfPrevCard");
            var note = card && card.nextElementSibling;
            var slot = mq && mq.matches ? $("#pfMprevSlot") : $("#pfPrevSlot");
            if (card && slot && card.parentNode !== slot) {
                slot.appendChild(card);
                if (note && note.classList.contains("pf-prev-note")) { slot.appendChild(note); }
            }
        }
        if (mq) {
            placePreview();
            if (mq.addEventListener) { mq.addEventListener("change", placePreview); } else if (mq.addListener) { mq.addListener(placePreview); }
        }

        // ================================================= PASO ACTUAL (scroll)
        if ("IntersectionObserver" in window) {
            var vis = {};
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (en) { vis[en.target.dataset.step] = en.isIntersecting ? en.intersectionRatio : 0; });
                var best = null;
                [1, 2, 3, 4].forEach(function (s) { if (vis[s] > 0 && best === null) { best = s; } });
                if (best && best !== curStep) { curStep = best; refresh(); }
            }, {rootMargin: "-140px 0px -45% 0px", threshold: [0, 0.01, 0.25]});
            $$(".pf-sec").forEach(function (s) { io.observe(s); });
        }

        // ============================================================ EVENTOS
        form.addEventListener("input", function (e) { clearErrorFor(e.target); refresh(); });
        form.addEventListener("change", function (e) {
            var n = e.target.name;
            if (e.target === seller) { applyRole(); applyStage(); fieldError("seller", false); }
            if (n === "stage_id") { applyStage(); fieldError("stage", false); }
            if (n === "presentation") { applySizes(); fieldError("presentation", false); }
            if (n === "size_grade_id") { fieldError("size_grade", false); }
            if (n === "uom_id") { uomTouched = true; }
            if (n === "origin_facility_ref") { applyPonds(); }
            if (e.target === pond) { pondCtx(); }
            refresh();
        });
        // El combo de provincia escribe el hidden sin evento.
        var combo = $("#pfLocCombo");
        if (combo) { combo.addEventListener("click", function () { setTimeout(refresh, 0); }); }
        function clearErrorFor(t) {
            if (t.id === "pfName") { fieldError("name", false); }
            if (t.id === "pfQty") { fieldError("qty", false); }
            if (t.id === "pfPrice") { fieldError("price", false); }
        }

        form.addEventListener("submit", function (e) {
            var publish = e.submitter && e.submitter.name === "publish";
            var c = checks();
            var keys = ["seller", "name", "stage", "qty", "presentation", "size_grade"];
            if (publish) { keys.push("price"); }
            var bad = keys.filter(function (k) { return !c.req[k]; });
            Object.keys(REQ_LABEL).forEach(function (k) { fieldError(k, bad.indexOf(k) >= 0); });
            if (bad.length) {
                e.preventDefault();
                var target = {seller: "#pfSeller", name: "#pfName", stage: '#pfStageBlk input[name="stage_id"]:not([disabled])',
                              qty: "#pfQty", price: "#pfPrice", presentation: 'input[name="presentation"]', size_grade: '#pfSizes input'}[bad[0]];
                var t = target && form.querySelector(target);
                if (t) {
                    (t.closest(".pf-f") || t.closest(".pf-blk") || t).scrollIntoView({behavior: "smooth", block: "center"});
                    setTimeout(function () { try { t.focus({preventScroll: true}); } catch (err) { t.focus(); } }, 250);
                }
                return;
            }
            if (form.dataset.sending) { e.preventDefault(); return; }
            form.dataset.sending = "1";
            syncFiles();
            $$('button[type="submit"]').forEach(function (b) { b.setAttribute("aria-busy", b === e.submitter ? "true" : "false"); });
            if (e.submitter) { e.submitter.classList.add("is-busy"); }
        });

        // Cabecera del sitio fija (Odoo 19): el paso a paso y la columna
        // derecha se pegan DEBAJO de ella, no detrás.
        function stickyTop() {
            var h = document.querySelector("header#top");
            var top = 0;
            if (h) {
                var pos = window.getComputedStyle(h).position;
                if (pos === "fixed" || pos === "sticky") { top = Math.max(0, Math.round(h.getBoundingClientRect().bottom)); }
            }
            form.style.setProperty("--pf-top", top + "px");
        }
        var stRaf = null;
        window.addEventListener("scroll", function () {
            if (stRaf) { return; }
            stRaf = requestAnimationFrame(function () { stRaf = null; stickyTop(); });
        }, {passive: true});
        window.addEventListener("resize", stickyTop);
        stickyTop();

        applyRole();
        applyStage();
        applyPonds();
        refresh();
        var errBox = document.getElementById("pfServerError");
        if (errBox) { errBox.setAttribute("tabindex", "-1"); errBox.focus(); }
    }

    if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
})();
