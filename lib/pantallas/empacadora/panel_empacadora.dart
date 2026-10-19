import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/repositorio.dart';
import '../../datos/sincronizador.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/menu_cuenta.dart';
import '../comunes/pantalla_despachos.dart';
import '../comunes/pantalla_informes.dart';
import '../comunes/pantalla_transacciones.dart';
import '../comunes/widgets.dart';
import 'pantalla_catalogo.dart';
import 'pantalla_listas_precios.dart';
import 'pantalla_ranking.dart';
import 'pantalla_reservas.dart';

/// Panel de la empacadora: compras en curso, informes por aceptar, llegadas
/// previstas hoy y top 3 de proveedores.
class PanelEmpacadora extends StatelessWidget {
  const PanelEmpacadora({super.key});

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
              repo.todoOVacio('/transactions', params: {'role': 'buyer'}),
              repo.todoOVacio('/verifications', params: {'role': 'buyer'}),
              repo.todoOVacio('/dispatches'),
              // El ranking solo lo da la API a una cuenta empacadora; si falla
              // (403, sin señal y sin caché) el panel sigue con el resto.
              repo.detalle('/suppliers/ranking', params: {'order': 'puntaje'}).catchError((Object e) {
                if (e is ErrorApi && e.esNoAutorizado) throw e;
                return const ResultadoLista([{}]);
              }),
            ]),
            hijo: (c, datos, _, _, recargar) {
              final compras = datos[0].datos.where((t) => transaccionEnCurso(t['state'] as String?)).toList();
              final informes = datos[1].datos.where((v) => v['acceptance_state'] == 'waiting').toList();
              final hoy = datos[2].datos.where((d) => d['state'] != 'llegado' && esHoy(aFecha(d['eta']))).toList();
              final ranking = datos[3].datos.first;
              final filas = ((ranking['rows'] as List?) ?? []).whereType<Map>().toList();
              final top = filas.take(3).toList();

              return RefreshIndicator(
                color: context.colores.marcaTexto,
                onRefresh: recargar,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
                  children: [
                    const Rotulo('Resumen'),
                    RejillaResumen(tarjetas: [
                      TarjetaResumen(
                        icono: Icons.shopping_bag_outlined,
                        valor: '${compras.length}',
                        titulo: 'Compras en curso',
                        detalle: _resumenEstados(compras),
                        color: context.colores.marcaTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTransacciones(rol: 'buyer'))),
                      ),
                      TarjetaResumen(
                        icono: Icons.how_to_vote_outlined,
                        valor: '${informes.length}',
                        titulo: 'Informes por aceptar',
                        detalle: informes.isEmpty ? 'Nada pendiente' : 'Esperan tu decisión',
                        color: informes.isEmpty ? context.colores.exitoTexto : context.colores.advertenciaTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaInformes(rol: 'buyer'))),
                      ),
                      TarjetaResumen(
                        icono: Icons.local_shipping_outlined,
                        valor: '${hoy.length}',
                        titulo: 'Llegadas previstas hoy',
                        detalle: hoy.isEmpty ? 'Ningún camión hoy' : hoy.map((d) => hora(aFecha(d['eta'])!)).join(', '),
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaDespachos())),
                      ),
                      TarjetaResumen(
                        icono: Icons.emoji_events_outlined,
                        valor: '${ranking['suppliers'] ?? filas.length}',
                        titulo: 'Proveedores en el ranking',
                        detalle: '${ranking['lots'] ?? 0} lotes verificados',
                        color: context.colores.acentoTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaRanking())),
                      ),
                    ]),
                    if (top.isNotEmpty) ...[
                      const Rotulo('Top 3 proveedores'),
                      Tarjeta(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaRanking())),
                        child: Column(children: [
                          for (var i = 0; i < top.length; i++) ...[
                            if (i > 0) const Divider(height: 1),
                            Padding(
                              padding: const EdgeInsets.symmetric(vertical: 10),
                              child: Row(children: [
                                CircleAvatar(radius: 14, backgroundColor: context.colores.marcaSuave, child: Text('${i + 1}', style: TextStyle(fontWeight: FontWeight.w800, color: context.colores.marcaTexto, fontSize: 13))),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                    Text(nombreDe(top[i]['supplier']), style: TextStyle(fontWeight: FontWeight.w700, color: context.colores.titulo)),
                                    Text('Rendimiento ${porcentaje(top[i]['rendimiento'])} · clase A ${porcentaje(top[i]['clase_a'])} · ${entero(top[i]['lotes'])} lotes',
                                        style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave)),
                                  ]),
                                ),
                                Text(numero(top[i]['puntaje']), style: TextStyle(fontWeight: FontWeight.w800, color: context.colores.marcaTexto)),
                              ]),
                            ),
                          ],
                        ]),
                      ),
                    ],
                    const Rotulo('Listas'),
                    FilaMenu(icono: Icons.storefront_outlined, titulo: 'Catálogo de lotes', subtitulo: 'Lotes publicados y compra con verificación', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaCatalogo()))),
                    FilaMenu(icono: Icons.shopping_bag_outlined, titulo: 'Mis compras', subtitulo: 'Estado y trazabilidad de cada compra', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTransacciones(rol: 'buyer')))),
                    FilaMenu(icono: Icons.fact_check_outlined, titulo: 'Informes de verificación', subtitulo: 'Ver el resultado y aceptar o rechazar', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaInformes(rol: 'buyer')))),
                    FilaMenu(icono: Icons.local_shipping_outlined, titulo: 'Despachos', subtitulo: 'Citas de los camiones y llegadas', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaDespachos()))),
                    FilaMenu(icono: Icons.event_available_outlined, titulo: 'Reservas de cosecha', subtitulo: 'Compromisos y compra del lote reservado', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaReservas()))),
                    FilaMenu(icono: Icons.emoji_events_outlined, titulo: 'Ranking de proveedores', subtitulo: 'Por rendimiento verificado', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaRanking()))),
                    FilaMenu(icono: Icons.price_change_outlined, titulo: 'Listas de precios', subtitulo: 'Solo lectura', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaListasPrecios()))),
                    const SizedBox(height: 8),
                    const AvisoWeb(texto: 'Publicar listas de precios, contraofertar y las reservas nuevas se hacen desde el portal web.'),
                  ],
                ),
              );
            },
          ),
        ),
      ]),
    );
  }

  static String _resumenEstados(List<Map<String, dynamic>> compras) {
    if (compras.isEmpty) return 'Sin compras abiertas';
    final porVerificar = compras.where((t) => t['state'] == 'pending_verification').length;
    final porAceptar = compras.where((t) => t['state'] == 'pending_acceptance').length;
    final confirmadas = compras.where((t) => t['state'] == 'confirmed').length;
    return [
      if (porVerificar > 0) '$porVerificar por verificar',
      if (porAceptar > 0) '$porAceptar por aceptar',
      if (confirmadas > 0) '$confirmadas confirmadas',
    ].join(' · ');
  }
}
