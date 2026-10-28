import { onMounted, onWillUnmount, status } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

import { AVISO_ACTUALIZADO, AVISO_NUEVO } from "./correo_aviso_service";

// Si llegan diez correos seguidos (o alguien marca cincuenta como leídos), la
// bandeja se recarga una sola vez.
export const ESPERA_RECARGA = 800;

/** Registros cargados de la lista, también si está agrupada. */
function registrosDe(lista) {
    if (lista.isGrouped) {
        return lista.groups.flatMap((grupo) => registrosDe(grupo.list));
    }
    return lista.records;
}

/**
 * Lista de correos en vivo: cuando llega un correo a un buzón del usuario o
 * alguien (otro usuario u otra pestaña) cambia lo leído o la carpeta de un
 * correo, se recargan las filas y los contadores del panel izquierdo sin
 * perder la página, la selección ni el desplazamiento.
 *
 * Mientras está abierta, la pestaña del navegador muestra «(N)» con los no
 * leídos de la bandeja de entrada.
 */
export class CorreoListController extends ListController {
    setup() {
        super.setup();
        this.busService = useService("bus_service");
        this.titleService = useService("title");
        this.seleccionPorRestaurar = null;
        this.recargarPronto = useDebounced(this.recargar, ESPERA_RECARGA);
        this.alAviso = () => this.recargarPronto();

        onMounted(() => {
            this.busService.subscribe(AVISO_NUEVO, this.alAviso);
            this.busService.subscribe(AVISO_ACTUALIZADO, this.alAviso);
            this.actualizarTitulo();
        });
        onWillUnmount(() => {
            this.busService.unsubscribe(AVISO_NUEVO, this.alAviso);
            this.busService.unsubscribe(AVISO_ACTUALIZADO, this.alAviso);
            this.titleService.setCounters({ shrimp_correo: 0 });
        });
    }

    get modelParams() {
        const params = super.modelParams;
        const alCargar = params.hooks.onRootLoaded;
        params.hooks.onRootLoaded = async (root) => {
            await alCargar?.(root);
            this.restaurarSeleccion(root);
        };
        return params;
    }

    async recargar() {
        if (status(this) !== "mounted") {
            return;
        }
        const root = this.model.root;
        if (root.editedRecord) {
            // Alguien está editando una fila: se reintenta más tarde.
            this.recargarPronto();
            return;
        }
        this.seleccionPorRestaurar = root.isDomainSelected
            ? "todo"
            : new Set(root.selection.map((registro) => registro.resId));
        try {
            await Promise.all([root.load(), this.recargarPanel(), this.actualizarTitulo()]);
        } finally {
            this.seleccionPorRestaurar = null;
        }
        if (status(this) === "mounted") {
            // Dibujo profundo: así se redibuja también el panel izquierdo (vive
            // dentro del Layout de la lista) con los contadores nuevos.
            this.render(true);
        }
    }

    /** La recarga reemplaza las filas: se vuelven a marcar las que estaban elegidas. */
    restaurarSeleccion(root) {
        const antes = this.seleccionPorRestaurar;
        this.seleccionPorRestaurar = null;
        if (!antes) {
            return;
        }
        if (antes === "todo") {
            root._selectDomain(true);
            return;
        }
        if (!antes.size) {
            return;
        }
        for (const registro of registrosDe(root)) {
            if (antes.has(registro.resId)) {
                registro._toggleSelection(true);
            }
        }
    }

    /**
     * Los contadores del panel («Bandeja de entrada (3)», «info@ (2)») vienen
     * en el nombre de cada valor: se piden de nuevo, sin la caché del
     * navegador. No se dispara el «update» del modelo de búsqueda: eso
     * recargaría la lista desde arriba y la devolvería a la primera página
     * sin la selección.
     */
    async recargarPanel() {
        const searchModel = this.env.searchModel;
        const categorias = searchModel.categories || [];
        if (!searchModel.display?.searchPanel || !categorias.length) {
            return;
        }
        const filterDomain = searchModel._getFilterDomain();
        const resultados = await Promise.all(
            categorias.map((categoria) =>
                this.orm.call(
                    searchModel.resModel,
                    "search_panel_select_range",
                    [categoria.fieldName],
                    {
                        category_domain: searchModel._getCategoryDomain(categoria.id),
                        context: searchModel.globalContext,
                        enable_counters: categoria.enableCounters,
                        expand: categoria.expand,
                        filter_domain: filterDomain,
                        hierarchize: categoria.hierarchize,
                        limit: categoria.limit,
                        search_domain: searchModel.searchDomain,
                    }
                )
            )
        );
        if (status(this) !== "mounted") {
            return;
        }
        categorias.forEach((categoria, i) =>
            searchModel._createCategoryTree(categoria.id, resultados[i])
        );
    }

    async actualizarTitulo() {
        const noLeidos = await this.orm.searchCount("shrimp.correo", [
            ["carpeta", "=", "entrada"],
            ["leido", "=", false],
        ]);
        if (status(this) === "mounted") {
            this.titleService.setCounters({ shrimp_correo: noLeidos });
        }
    }
}

export const correoListView = {
    ...listView,
    Controller: CorreoListController,
};

registry.category("views").add("shrimp_correo_list", correoListView);
