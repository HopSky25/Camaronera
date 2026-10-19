/** @odoo-module **/
/* CamaronMarket — botón luna/sol del MODO OSCURO (cabecera de los tres sitios).

   El modo se aplica con data-bs-theme en <html>. El servidor lo pone si hay
   cookie (views/dark_mode_templates.xml) y, si no, el script en línea del
   <head> antes de pintar (preferencia guardada o la del sistema). Aquí solo:
     * clic en [data-shrimp-theme-toggle] -> cambia, guarda la elección en
       localStorage + cookie «shrimp_theme» (1 año) y actualiza los rótulos
       accesibles (aria-label / title / aria-pressed) de todos los botones;
     * mientras el visitante no elija, sigue los cambios del sistema;
     * sincroniza las pestañas abiertas (evento storage).
   Sin dependencias: delegación de eventos en document. En modo edición del
   sitio (data-shrimp-theme-lock) no hace nada. */

const KEY = "shrimp_theme";
const root = document.documentElement;

function locked() {
    return root.hasAttribute("data-shrimp-theme-lock");
}

function current() {
    return root.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
}

function stored() {
    const m = document.cookie.match(/(?:^|;\s*)shrimp_theme=(dark|light)/);
    if (m) {
        return m[1];
    }
    try {
        const v = window.localStorage.getItem(KEY);
        return v === "dark" || v === "light" ? v : null;
    } catch {
        return null;
    }
}

function syncButtons() {
    const dark = current() === "dark";
    const label = dark ? "Activar modo claro" : "Activar modo oscuro";
    for (const btn of document.querySelectorAll("[data-shrimp-theme-toggle]")) {
        btn.setAttribute("aria-label", label);
        btn.setAttribute("title", label);
        btn.setAttribute("aria-pressed", dark ? "true" : "false");
    }
}

function apply(theme) {
    root.setAttribute("data-bs-theme", theme);
    syncButtons();
}

function save(theme) {
    try {
        window.localStorage.setItem(KEY, theme);
    } catch {
        // navegación privada sin almacenamiento: queda la cookie
    }
    document.cookie = `${KEY}=${theme}; path=/; max-age=31536000; SameSite=Lax`;
}

function onClick(ev) {
    const btn = ev.target.closest && ev.target.closest("[data-shrimp-theme-toggle]");
    if (!btn || locked()) {
        return;
    }
    ev.preventDefault();
    const next = current() === "dark" ? "light" : "dark";
    save(next);
    apply(next);
}

if (!locked()) {
    if (!root.hasAttribute("data-bs-theme")) {
        // Sin el script del head (caché vieja...): se decide aquí.
        const media = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
        apply(stored() || (media && media.matches ? "dark" : "light"));
    }
    document.addEventListener("click", onClick);
    if (window.matchMedia) {
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const follow = (e) => {
            if (!stored()) {
                apply(e.matches ? "dark" : "light");
            }
        };
        if (media.addEventListener) {
            media.addEventListener("change", follow);
        }
    }
    window.addEventListener("storage", (e) => {
        if (e.key === KEY && (e.newValue === "dark" || e.newValue === "light")) {
            apply(e.newValue);
        }
    });
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", syncButtons);
    } else {
        syncButtons();
    }
}
