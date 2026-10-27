import { registry } from "@web/core/registry";
import { FileModel } from "@web/core/file_viewer/file_model";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import {
    Many2ManyBinaryField,
    many2ManyBinaryField,
} from "@web/views/fields/many2many_binary/many2many_binary_field";

/**
 * Adjuntos de un correo como «chips»: un clic abre la vista previa (imágenes,
 * PDF, texto, video) con el visor de archivos de Odoo; el resto se descarga.
 */
export class AdjuntosCorreoField extends Many2ManyBinaryField {
    static template = "shrimp_correo_entrante.AdjuntosCorreo";

    setup() {
        super.setup();
        this.visor = useFileViewer();
    }

    get archivos() {
        return this.files.map((f) => Object.assign(new FileModel(), {
            id: f.id,
            name: f.name,
            mimetype: f.mimetype,
        }));
    }

    icono(archivo) {
        const tipo = archivo.mimetype || "";
        if (tipo.startsWith("image/")) {
            return "fa-file-image-o";
        }
        if (tipo === "application/pdf") {
            return "fa-file-pdf-o";
        }
        if (tipo.includes("sheet") || tipo.includes("excel")) {
            return "fa-file-excel-o";
        }
        if (tipo.includes("word")) {
            return "fa-file-word-o";
        }
        if (tipo.includes("zip") || tipo.includes("compressed")) {
            return "fa-file-archive-o";
        }
        return "fa-file-o";
    }

    abrir(archivo, ev) {
        if (archivo.isViewable) {
            ev.preventDefault();
            const todos = this.archivos;
            this.visor.open(todos.find((a) => a.id === archivo.id), todos);
        }
    }
}

registry.category("fields").add("shrimp_adjuntos_correo", {
    ...many2ManyBinaryField,
    component: AdjuntosCorreoField,
});
