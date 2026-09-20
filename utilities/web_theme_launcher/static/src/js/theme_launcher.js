/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { user } from "@web/core/user";

const DEFAULT_PRIMARY = "#123e5c";

// Applies the configured primary color and the user's dark-mode preference to
// the whole backend. Runs once as soon as the module is loaded so the theme is
// in place before the first paint the user notices.
function applyTheme() {
  // Runs at import time, i.e. from <head> before <body> exists, so operate on
  // documentElement (always present) instead of document.body.
  const root = document.documentElement;
  const color = session.wtl_primary_color || DEFAULT_PRIMARY;
  root.style.setProperty("--wtl-primary", color);
  root.classList.toggle("wtl-dark", !!session.wtl_dark_mode);
}
applyTheme();

// En el backend, la plantilla web.layout usa el favicon por defecto de Odoo
// (x_icon solo lo define el sitio web). Apuntamos el favicon a la ruta
// /favicon.ico del módulo de sitio web, que redirige al favicon del sitio
// actual; así la pestaña del back-office muestra el mismo icono corporativo
// que el sitio web, sin depender de ids fijos.
function applyFavicon() {
  const href = "/favicon.ico";
  let link = document.querySelector("link[rel~='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "shortcut icon";
    link.type = "image/x-icon";
    document.head.appendChild(link);
  }
  link.href = href;
}
applyFavicon();

class ThemeLauncherSystray extends Component {
  static template = "web_theme_launcher.SystrayLauncher";
  static props = {};

  setup() {
    this.menu = useService("menu");
    this.orm = useService("orm");
    this.state = useState({
      open: false,
      query: "",
      dark: !!session.wtl_dark_mode,
    });
    this.searchInput = useRef("searchInput");

    this._onKeyDown = (ev) => {
      if (ev.ctrlKey && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        this.toggle();
        return;
      }
      if (this.state.open && ev.key === "Escape") {
        this.close();
      }
    };

    onMounted(() => window.addEventListener("keydown", this._onKeyDown));
    onWillUnmount(() => window.removeEventListener("keydown", this._onKeyDown));
  }

  get apps() {
    return this.menu.getApps();
  }

  getIconUrl(app) {
    if (app.webIconData) {
      const data = app.webIconData;
      return data.startsWith("data:") ? data : "data:image/png;base64," + data;
    }
    if (typeof app.webIcon === "string" && app.webIcon.includes(",")) {
      const [mod, path] = app.webIcon.split(",");
      return "/" + mod + "/" + path;
    }
    return false;
  }

  get filteredApps() {
    const q = (this.state.query || "").toLowerCase().trim();
    if (!q) return this.apps;
    return this.apps.filter((a) => (a.name || "").toLowerCase().includes(q));
  }

  toggle() {
    this.state.open = !this.state.open;
    if (this.state.open) {
      this.state.query = "";
      setTimeout(() => this.searchInput.el && this.searchInput.el.focus(), 0);
    }
  }

  close() {
    this.state.open = false;
  }

  onOverlayClick() {
    this.close();
  }

  openApp(app) {
    this.close();
    this.menu.selectMenu(app);
  }

  // Persist the preference on the user and apply it instantly, without a reload.
  async toggleDark() {
    const value = !this.state.dark;
    this.state.dark = value;
    session.wtl_dark_mode = value;
    document.documentElement.classList.toggle("wtl-dark", value);
    await this.orm.write("res.users", [user.userId], { wtl_dark_mode: value });
  }
}

registry.category("systray").add("web_theme_launcher.systray", {
  Component: ThemeLauncherSystray,
  sequence: 1,
  isDisplayed: () => true,
});

// El editor del sitio web usa una bandeja de sistema propia
// (registry "website_systray") y oculta los items del systray estándar,
// por lo que hay que registrar el lanzador también ahí para que el icono
// no desaparezca al entrar a Sitio web.
registry.category("website_systray").add(
  "web_theme_launcher.systray",
  {
    Component: ThemeLauncherSystray,
    isDisplayed: () => true,
  },
  { sequence: 1 }
);
