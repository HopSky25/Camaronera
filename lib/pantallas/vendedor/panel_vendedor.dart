import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/menu_cuenta.dart';
import '../comunes/pantalla_despachos.dart';
import '../comunes/pantalla_informes.dart';
import '../comunes/pantalla_transacciones.dart';
import '../comunes/widgets.dart';
import '../empacadora/pantalla_catalogo.dart';
import '../../modelos/instalaciones.dart';
import 'pantalla_instalaciones.dart';
import 'pantalla_mis_certificados.dart';
import 'pantalla_mis_lotes.dart';
import 'pantalla_publicar_lote.dart';

/// Panel de quien vende (camaronera, semillero, laboratorio): lotes
/// publicados, ventas en curso, informes por aceptar y próximos despachos.
class PanelVendedor extends StatelessWidget {
  const PanelVendedor({super.key});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final perfil = context.watch<Sesion>().perfil ?? '';
    final esCamaronera = perfil == 'camaronera';
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    return Scaffold(
      appBar: const BarraCuenta(),
      body: Column(children: [
        const FranjaPerfil(),
        BarraEstado(sinc: sinc),
        Expanded(
          child: CargaRemota<List<ResultadoLista>>(
            cargar: (repo) => Future.wait([
              repo.todoOVacio('/products', params: {'state': 'published'}),
              repo.todoOVacio('/transactions', params: {'role': 'seller'}),
              repo.todoOVacio('/verifications', params: {'role': 'seller'}),
              repo.todoOVacio('/dispatches'),
            ]),
            hijo: (c, datos, _, _, recargar) {
              final lotes = datos[0].datos;
              final ventas = datos[1].datos.where((t) => transaccionEnCurso(t['state'] as String?)).toList();
              final informes = datos[2].datos.where((v) => v['acceptance_state'] == 'waiting').toList();
              final ahora = DateTime.now();
              final despachos = datos[3].datos.where((d) {
                if (d['state'] == 'llegado') return false;
                final eta = aFecha(d['eta']);
                return eta == null || eta.isAfter(ahora.subtract(const Duration(hours: 12)));
              }).toList();
              final libras = lotes.fold<double>(0, (s, p) => s + ((p['available_qty'] as num?)?.toDouble() ?? 0));
              final unidad = lotes.isNotEmpty ? unidadPlural(lotes.first['uom']) : (esCamaronera ? 'lb' : '');
              final proximo = despachos.map((d) => aFecha(d['eta'])).whereType<DateTime>().fold<DateTime?>(null, (a, b) => a == null || b.isBefore(a) ? b : a);

              return RefreshIndicator(
                color: context.colores.marcaTexto,
                onRefresh: recargar,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
                  children: [
                    const Rotulo('Resumen'),
                    RejillaResumen(tarjetas: [
                      TarjetaResumen(
                        icono: Icons.inventory_outlined,
                        valor: '${lotes.length}',
                        titulo: 'Lotes publicados',
                        detalle: '${numero(libras)} $unidad disponibles',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaMisLotes())),
                      ),
                      TarjetaResumen(
                        icono: Icons.sell_outlined,
                        valor: '${ventas.length}',
                        titulo: 'Ventas en curso',
                        detalle: _resumenEstados(ventas),
                        color: context.colores.marcaTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTransacciones(rol: 'seller'))),
                      ),
                      TarjetaResumen(
                        icono: Icons.how_to_vote_outlined,
                        valor: '${informes.length}',
                        titulo: 'Informes por aceptar',
                        detalle: informes.isEmpty ? 'Nada pendiente' : 'Esperan tu decisión',
                        color: informes.isEmpty ? context.colores.exitoTexto : context.colores.advertenciaTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaInformes(rol: 'seller'))),
                      ),
                      TarjetaResumen(
                        icono: Icons.local_shipping_outlined,
                        valor: '${despachos.length}',
                        titulo: 'Próximos despachos',
                        detalle: proximo == null
                            ? (despachos.isEmpty ? 'Sin despachos' : 'Hay citas sin fijar')
                            : 'Próximo: ${fechaCorta(proximo)} ${hora(proximo)}',
                        color: context.colores.acentoTexto,
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaDespachos())),
                      ),
                    ]),
                    const Rotulo('Listas'),
                    if (!soloLectura && puedePublicarLotes(perfil))
                      FilaMenu(
                        icono: Icons.add_box_outlined,
                        titulo: 'Publicar lote',
                        subtitulo: esCamaronera ? 'Camarón: se guarda en borrador y se publica con tu huella' : 'Nauplio o larva: se guarda en borrador y se publica con tu huella',
                        onTap: () async {
                          final creado = await Navigator.push<bool>(context, MaterialPageRoute(builder: (_) => const PantallaPublicarLote()));
                          if (creado == true) await recargar();
                        },
                      ),
                    FilaMenu(icono: Icons.inventory_outlined, titulo: 'Mis lotes', subtitulo: 'Editar, fotos, certificados, retirar y reactivar', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaMisLotes()))),
                    if (puedePublicarLotes(perfil)) ...[
                      FilaMenu(
                        icono: Icons.agriculture_outlined,
                        titulo: '${vocabularioDe(perfil).instalaciones} y ${vocabularioDe(perfil).piscinas.toLowerCase()}',
                        subtitulo: 'El origen de tus lotes: crear, editar y archivar',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaInstalaciones())),
                      ),
                      FilaMenu(
                        icono: Icons.badge_outlined,
                        titulo: 'Mis certificados',
                        subtitulo: 'Los de tu cuenta, con su revisión',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaMisCertificados())),
                      ),
                    ],
                    FilaMenu(icono: Icons.sell_outlined, titulo: 'Mis ventas', subtitulo: 'Estado y trazabilidad de cada venta', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaTransacciones(rol: 'seller')))),
                    FilaMenu(icono: Icons.fact_check_outlined, titulo: 'Informes de verificación', subtitulo: 'Ver el resultado y aceptar o rechazar', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaInformes(rol: 'seller')))),
                    FilaMenu(icono: Icons.local_shipping_outlined, titulo: 'Despacho', subtitulo: 'Programar la cosecha, la salida y el camión', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaDespachos()))),
                    // Compra directa (C5): la camaronera compra larva; el
                    // laboratorio, nauplio. El servidor dice si el perfil puede.
                    if (perfil == 'camaronera' && !soloLectura)
                      FilaMenu(icono: Icons.shopping_cart_outlined, titulo: 'Comprar larva', subtitulo: 'Zoea, mysis y postlarva: compra directa', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaCatalogo(tipoInicial: 'larva', titulo: 'Comprar larva')))),
                    if (perfil == 'laboratorio' && !soloLectura)
                      FilaMenu(icono: Icons.shopping_cart_outlined, titulo: 'Comprar nauplio', subtitulo: 'Nauplio de nauplieros y laboratorios: compra directa', onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaCatalogo(tipoInicial: 'nauplio', titulo: 'Comprar nauplio')))),
                  ],
                ),
              );
            },
          ),
        ),
      ]),
    );
  }

  static String _resumenEstados(List<Map<String, dynamic>> ventas) {
    if (ventas.isEmpty) return 'Sin ventas abiertas';
    final porVerificar = ventas.where((t) => t['state'] == 'pending_verification').length;
    final porAceptar = ventas.where((t) => t['state'] == 'pending_acceptance').length;
    final confirmadas = ventas.where((t) => t['state'] == 'confirmed').length;
    return [
      if (porVerificar > 0) '$porVerificar por verificar',
      if (porAceptar > 0) '$porAceptar por aceptar',
      if (confirmadas > 0) confirmadas == 1 ? '1 confirmada' : '$confirmadas confirmadas',
    ].join(' · ');
  }
}
