import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../tema.dart';
import 'comunes/menu_cuenta.dart';
import 'comunes/widgets.dart';
import 'pantalla_verificacion.dart';

/// Bandeja del técnico (lo suyo) o de la empresa verificadora (todo lo de la
/// empresa), con filtro por estado. Funciona sin internet.
class PantallaBandeja extends StatefulWidget {
  final String titulo;
  const PantallaBandeja({super.key, this.titulo = 'Mis verificaciones'});
  @override
  State<PantallaBandeja> createState() => _PantallaBandejaState();
}

class _PantallaBandejaState extends State<PantallaBandeja> {
  String _filtro = 'abiertas';

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final lista = sinc.bandeja.where((v) {
      return switch (_filtro) {
        'abiertas' => !v.cerrada,
        'campo' => v.enCampo,
        'cerradas' => v.cerrada,
        _ => true,
      };
    }).toList();
    final abiertas = sinc.bandeja.where((v) => !v.cerrada).length;
    final enCampo = sinc.bandeja.where((v) => v.enCampo).length;
    final sinTecnico = sinc.bandeja.where((v) => v.soyVerificadora && !v.cerrada && !v.tecnicoAsignado && !v.declinada).length;
    final porAceptar = sinc.bandeja.where(_porAceptar).length;

    return Scaffold(
      appBar: const BarraCuenta(),
      body: Column(
        children: [
          const FranjaPerfil(),
          BarraEstado(sinc: sinc),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
            child: Row(
              children: [
                Expanded(child: Text(widget.titulo, style: Theme.of(context).textTheme.titleLarge)),
                Text('$abiertas abiertas · $enCampo en campo', style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
          if (sinTecnico > 0 || porAceptar > 0)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
              child: Text(
                  [
                    if (porAceptar > 0) '$porAceptar por aceptar',
                    if (sinTecnico > 0) '$sinTecnico sin técnico asignado',
                  ].join(' · '),
                  style: TextStyle(fontSize: 13, color: context.colores.advertenciaTexto, fontWeight: FontWeight.w700)),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: SizedBox(
              width: double.infinity,
              child: SegmentedButton<String>(
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(value: 'abiertas', label: Text('Abiertas')),
                  ButtonSegment(value: 'campo', label: Text('En campo')),
                  ButtonSegment(value: 'cerradas', label: Text('Cerradas')),
                ],
                selected: {_filtro},
                onSelectionChanged: (s) => setState(() => _filtro = s.first),
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              color: context.colores.marcaTexto,
              onRefresh: sinc.sincronizar,
              child: lista.isEmpty
                  ? const VacioLista(texto: 'No hay verificaciones en esta lista.\nDesliza hacia abajo para sincronizar.')
                  : ListView.separated(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                      itemCount: lista.length,
                      separatorBuilder: (_, _) => const SizedBox(height: 10),
                      itemBuilder: (_, i) => _TarjetaVerificacion(v: lista[i]),
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

/// La orden espera que la empresa la acepte o la decline.
bool _porAceptar(Verificacion v) =>
    v.soyVerificadora && v.modo == 'platform' && v.respuestaOrden == 'pending' && const {'received', 'assigned'}.contains(v.estado);

class _TarjetaVerificacion extends StatelessWidget {
  final Verificacion v;
  const _TarjetaVerificacion({required this.v});

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final (_, colorEstado) = c.insignia(v.estado);
    final dm = DateFormat('dd/MM HH:mm');
    return TarjetaRegistro(
      titulo: v.referencia,
      subtitulo: v.producto.nombre,
      estado: v.estado,
      estadoTexto: v.estadoEtiqueta,
      onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVerificacion(id: v.id))),
      datos: [
        Dato(icono: Icons.water_outlined, texto: v.vendedor.nombre),
        Dato(icono: Icons.factory_outlined, texto: v.comprador.nombre),
        if (v.soyVerificadora)
          Dato(
            icono: Icons.engineering_outlined,
            texto: v.tecnico == null
                ? 'Sin técnico asignado'
                : v.tecnicosApoyo.isEmpty
                    ? v.tecnico!.nombre
                    : '${v.tecnico!.nombre} + ${v.tecnicosApoyo.length} de apoyo',
            color: v.tecnico == null && !v.cerrada && !v.declinada ? c.advertenciaTexto : null,
          ),
        if (v.soyApoyo) const Dato(icono: Icons.groups_outlined, texto: 'Vas de apoyo: cargas datos, no firmas'),
        if (v.esSegundaOpinion) const Dato(icono: Icons.balance_rounded, texto: 'Segunda opinión'),
        if (_porAceptar(v))
          Dato(
            icono: Icons.inbox_outlined,
            texto: v.plazoRespuesta == null ? 'Por aceptar' : 'Por aceptar hasta el ${dm.format(v.plazoRespuesta!)}',
            color: c.advertenciaTexto,
          ),
        if (v.declinada) Dato(icono: Icons.block_rounded, texto: 'Declinaste esta orden', color: c.peligroTexto),
        if (!v.cerrada && !v.declinada && v.plazoVencidoEtiqueta != null)
          Dato(icono: Icons.schedule_rounded, texto: v.plazoVencidoEtiqueta!, color: c.peligroTexto)
        else if (!v.cerrada && v.plazoVeredicto != null)
          Dato(icono: Icons.schedule_rounded, texto: 'Veredicto hasta el ${dm.format(v.plazoVeredicto!)}'),
        if (v.rechazoNoPresentado) const Dato(icono: Icons.visibility_off_outlined, texto: 'Producto no presentado'),
        if (v.estado == 'rejected' && v.estadoImpugnacion == 'open')
          Dato(
            icono: Icons.hourglass_bottom_rounded,
            texto: v.plazoImpugnacion == null
                ? 'El vendedor puede pedir segunda opinión'
                : 'Segunda opinión posible hasta el ${dm.format(v.plazoImpugnacion!)}',
          ),
        if (v.despachoEta != null)
          Dato(
            icono: Icons.local_shipping_outlined,
            texto: 'Camión: ${DateFormat('dd/MM').format(v.despachoEta!)} a las ${DateFormat('HH:mm').format(v.despachoEta!)}',
            color: colorEstado,
          ),
      ],
    );
  }
}
