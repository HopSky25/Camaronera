import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sincronizador.dart';
import '../../tema.dart';
import '../comunes/lista_remota.dart';
import '../comunes/menu_cuenta.dart';
import '../comunes/widgets.dart';
import 'pantalla_ordenes.dart';
import 'pantalla_solicitudes.dart';
import 'pantalla_tarifas.dart';

/// Panel del maquilador: solicitudes de empaque abiertas, órdenes en curso,
/// actas por firmar y tarifas.
class PanelMaquilador extends StatelessWidget {
  const PanelMaquilador({super.key});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    return Scaffold(
      appBar: const BarraCuenta(),
      body: Column(children: [
        const FranjaPerfil(),
        BarraEstado(sinc: sinc),
        Expanded(
          child: CargaRemota<List<ResultadoLista>>(
            cargar: (repo) => Future.wait([
              repo.todoOVacio('/copack/requests', params: {'state': 'published'}),
              repo.todoOVacio('/copack/orders'),
              repo.todoOVacio('/copack/tariffs'),
            ]),
            hijo: (c, datos, _, _, recargar) {
              final solicitudes = datos[0].datos;
              final sinOferta = solicitudes.where((s) => ((s['offers'] as List?) ?? []).isEmpty).length;
              final ordenes = datos[1].datos.where((o) => o['state'] != 'closed' && o['state'] != 'cancelled').toList();
              final porRecibir = ordenes.where((o) => o['state'] == 'confirmed').length;
              final porEmpacar = ordenes.where((o) => o['state'] == 'received').length;
              final actas = ordenes.where((o) => o['acceptance_state'] == 'open' || o['acceptance_state'] == 'disputed').length;
              final tarifas = datos[2].datos.where((t) => t['my_role'] == 'copacker').toList();
              final vigentes = tarifas.where((t) => t['is_current'] == true).length;

              return RefreshIndicator(
                color: context.colores.marcaTexto,
                onRefresh: recargar,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
                  children: [
                    const Rotulo('Resumen'),
                    RejillaResumen(tarjetas: [
                      TarjetaResumen(
                        icono: Icons.campaign_outlined,
                        valor: '${solicitudes.length}',
                        titulo: 'Solicitudes abiertas',
                        detalle: sinOferta == 0 ? 'Todas con tu oferta' : '$sinOferta sin ofertar',
                        color: sinOferta > 0 ? context.colores.advertenciaTexto : context.colores.acentoTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaSolicitudes())),
                      ),
                      TarjetaResumen(
                        icono: Icons.inventory_2_outlined,
                        valor: '${ordenes.length}',
                        titulo: 'Órdenes en curso',
                        detalle: [if (porRecibir > 0) '$porRecibir por recibir', if (porEmpacar > 0) '$porEmpacar por empacar'].join(' · '),
                        color: context.colores.marcaTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaOrdenes())),
                      ),
                      TarjetaResumen(
                        icono: Icons.draw_outlined,
                        valor: '$actas',
                        titulo: 'Actas por firmar',
                        detalle: actas == 0 ? 'Nada pendiente' : 'Revisa el cuadre',
                        color: actas > 0 ? context.colores.advertenciaTexto : context.colores.exitoTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaOrdenes(filtroInicial: 'actas'))),
                      ),
                      TarjetaResumen(
                        icono: Icons.price_change_outlined,
                        valor: '$vigentes',
                        titulo: 'Tarifas vigentes',
                        detalle: '${tarifas.length} en total',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTarifas())),
                      ),
                    ]),
                    const Rotulo('Listas'),
                    FilaMenu(icono: Icons.campaign_outlined, titulo: 'Solicitudes de empaque', subtitulo: 'Ver y ofertar', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaSolicitudes()))),
                    FilaMenu(icono: Icons.inventory_2_outlined, titulo: 'Órdenes', subtitulo: 'Recepción, empaque y acta', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaOrdenes()))),
                    FilaMenu(icono: Icons.price_change_outlined, titulo: 'Tarifas', subtitulo: 'Solo lectura', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTarifas()))),
                    const SizedBox(height: 8),
                    const AvisoWeb(texto: 'Crear o publicar tarifas, reabrir actas y el empaque propio se hacen desde el portal web.'),
                  ],
                ),
              );
            },
          ),
        ),
      ]),
    );
  }
}
