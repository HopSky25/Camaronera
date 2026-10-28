import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

export const AVISO_NUEVO = "shrimp_correo/nuevo";
export const AVISO_ACTUALIZADO = "shrimp_correo/actualizado";

// Al conectarse, el bus entrega también los avisos de los últimos segundos (o
// todos los pendientes desde la última visita). Los correos que llegaron más de
// este margen antes de abrir la pestaña no se anuncian: ya están en la bandeja.
// El margen cubre además un reloj del equipo algo adelantado al del servidor.
export const MARGEN_AVISOS_VIEJOS = 2 * 60 * 1000;

/**
 * Notificación «Nuevo correo» cuando llega algo a un buzón del usuario, esté
 * donde esté dentro de Odoo. El servidor solo se la manda a quien ve ese buzón
 * (sus usuarios y los administradores del correo).
 *
 * Se muestra también con la bandeja abierta: la fila nueva aparece sola, pero
 * el aviso es lo que llama la atención, como en Outlook.
 */
export const correoAvisoService = {
    dependencies: ["action", "bus_service", "notification", "orm"],

    start(env, { action, bus_service, notification, orm }) {
        const inicio = Date.now();

        async function abrir(correoId) {
            // El servidor revisa el acceso y arma la acción con el formulario
            // de lectura; abrirlo así lo marca como leído.
            const accion = await orm.call("shrimp.correo", "action_abrir", [[correoId]], {
                context: {
                    shrimp_correo_marcar_leido: true,
                    shrimp_accion: "shrimp_correo_entrante.action_correo_entrada",
                },
            });
            await action.doAction(accion);
        }

        bus_service.subscribe(AVISO_NUEVO, (aviso) => {
            if (aviso.creado && aviso.creado < inicio - MARGEN_AVISOS_VIEJOS) {
                return;
            }
            const de = aviso.de || _t("(remitente desconocido)");
            const asunto = aviso.asunto || _t("(sin asunto)");
            const cerrar = notification.add(
                _t("Nuevo correo de %(de)s · %(asunto)s", { de, asunto }),
                {
                    title: aviso.buzon_nombre,
                    type: "info",
                    sticky: false,
                    buttons: [
                        {
                            name: _t("Abrir"),
                            icon: aviso.tiene_adjuntos ? "fa-paperclip" : "fa-envelope-o",
                            primary: true,
                            onClick: async () => {
                                cerrar();
                                await abrir(aviso.id);
                            },
                        },
                    ],
                }
            );
        });
    },
};

registry.category("services").add("shrimp_correo_aviso", correoAvisoService);
