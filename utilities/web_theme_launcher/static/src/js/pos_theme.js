/** @odoo-module **/

// El Punto de Venta se sirve en un bundle aparte (point_of_sale.assets_prod),
// por lo que ni el CSS ni el JS del backend se cargan aquí. Este archivo se
// añade a ese bundle para aplicar la preferencia de modo oscuro del usuario
// (y el color de tema) también dentro del POS, y para agregar un interruptor
// de modo oscuro en el menú superior derecho del POS.
import { useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";

const DEFAULT_PRIMARY = "#123e5c";

function applyPosTheme(dark) {
  const root = document.documentElement;
  const color = session.wtl_primary_color || DEFAULT_PRIMARY;
  root.style.setProperty("--wtl-primary", color);
  root.classList.toggle("wtl-dark", !!dark);
}

// Aplica el tema en cuanto se carga el bundle (antes del primer render notorio).
applyPosTheme(session.wtl_dark_mode);

patch(Navbar.prototype, {
  setup() {
    super.setup();
    // Estado reactivo para que el interruptor del menú refleje el modo actual.
    this.wtlDark = useState({ on: !!session.wtl_dark_mode });
  },

  // Alterna el modo oscuro y lo persiste en el usuario (igual que el botón del
  // systray del backend), sin recargar.
  async toggleDarkMode() {
    const value = !this.wtlDark.on;
    this.wtlDark.on = value;
    session.wtl_dark_mode = value;
    applyPosTheme(value);
    try {
      await this.env.services.orm.write("res.users", [session.uid], {
        wtl_dark_mode: value,
      });
    } catch {
      // Si falla la escritura (p. ej. sin conexión) el cambio visual se
      // mantiene en la sesión actual del POS.
    }
  },
});
