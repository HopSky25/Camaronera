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
    final sinTecnico = sinc.bandeja.where((v) => v.soyVerificadora && !v.cerrada && !v.tecnicoAsignado).length;

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
          if (sinTecnico > 0)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
              child: Text('$sinTecnico sin técnico asignado', style: TextStyle(fontSize: 13, color: context.colores.advertenciaTexto, fontWeight: FontWeight.w700)),
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

class _TarjetaVerificacion extends StatelessWidget {
  final Verificacion v;
  const _TarjetaVerificacion({required this.v});

  @override
  Widget build(BuildContext context) {
    final (_, colorEstado) = context.colores.insignia(v.estado);
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
            texto: v.tecnico?.nombre ?? 'Sin técnico asignado',
            color: v.tecnico == null && !v.cerrada ? context.colores.advertenciaTexto : null,
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
