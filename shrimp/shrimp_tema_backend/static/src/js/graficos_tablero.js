/** @odoo-module **/
// Gráficos del tablero de diarios con la paleta de CamaronMarket en vez del violeta de Odoo.
import { patch } from "@web/core/utils/patch";
import { JournalDashboardGraphField } from "@web/views/fields/journal_dashboard_graph/journal_dashboard_graph_field";

const TEAL = "#14A0B0";
const NAVY = "#123E5C";
const TEAL_CLARO = "#A9DDE3";
const GRIS = "#E3EAED";

function rgba(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

patch(JournalDashboardGraphField.prototype, {
    getLineChartConfig() {
        const config = super.getLineChartConfig(...arguments);
        const ds = config.data.datasets[0];
        const muestra = this.data[0].is_sample_data;
        ds.borderColor = muestra ? rgba(TEAL, 0.15) : TEAL;
        ds.backgroundColor = muestra ? rgba(TEAL, 0.05) : rgba(TEAL, 0.15);
        ds.pointBackgroundColor = muestra ? rgba(TEAL, 0.15) : NAVY;
        ds.pointBorderColor = ds.pointBackgroundColor;
        return config;
    },
    getBarChartConfig() {
        const config = super.getBarChartConfig(...arguments);
        const valores = this.data[0].values;
        config.data.datasets[0].backgroundColor = valores.map((pt) =>
            pt.type === "past" ? NAVY : pt.type === "future" ? TEAL_CLARO : GRIS
        );
        return config;
    },
});
