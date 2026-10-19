import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../datos/sesion.dart';
import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/formato.dart';
import 'comunes/widgets.dart';
import 'pantalla_asignar_apoyo.dart';
import 'pantalla_asignar_tecnico.dart';
import 'pantalla_fotos.dart';
import 'pantalla_informe.dart';
import 'pantalla_veredicto.dart';
import 'pantalla_veredicto_no_presentado.dart';

/// Detalle de una verificación y las acciones del técnico, en el orden del
/// trabajo real: aceptar la orden → asignar (y sumar apoyo) → iniciar campo
/// → llegada del camión → informe y fotos → veredicto. El técnico de apoyo
/// hace todo lo de campo, pero no firma el veredicto.
class PantallaVerificacion extends StatelessWidget {
  final String id;
  const PantallaVerificacion({super.key, required this.id});

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final v = sinc.bandeja.where((e) => e.id == id).firstOrNull;
    if (v == null) {
      return Scaffold(appBar: AppBar(), body: const Center(child: Text('Esta verificación ya no está en tu bandeja.')));
    }
    final c = context.colores;
    final fmt = DateFormat('dd/MM/yyyy HH:mm');
    final pesos = (v.crudo['weights'] ?? {}) as Map;
    final clasif = (v.crudo['classification'] ?? {}) as Map;
    final larva = v.crudo['larvae'] is Map ? v.crudo['larvae'] as Map : const {};
    final hayInforme = _hay(pesos['sent_lb']) ||
        _hay(pesos['plant_lb']) ||
        ((clasif['lines'] as List?)?.isNotEmpty ?? false) ||
        _hay(larva['qty_verified']);
    final plataforma = v.modo == 'platform';
    final ordenAbierta = plataforma && const {'received', 'assigned'}.contains(v.estado);
    final porAceptar = ordenAbierta && v.respuestaOrden == 'pending';
    final apoyo = v.tecnicosApoyo;
    final avisos = v.crudo['warnings'] is List ? (v.crudo['warnings'] as List).whereType<String>().toList() : const <String>[];

    return Scaffold(
      appBar: AppBar(title: Text(v.referencia)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          Row(children: [
            Expanded(child: Text(v.producto.nombre, style: Theme.of(context).textTheme.titleMedium)),
            const SizedBox(width: 10),
            Insignia(estado: v.estado, texto: v.estadoEtiqueta),
          ]),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            if (v.soyResponsable) const Insignia(estado: '', texto: 'Eres el responsable', tipo: TipoPildora.hecho),
            if (v.soyApoyo) const Insignia(estado: '', texto: 'Vas de apoyo', tipo: TipoPildora.info),
            if (v.esSegundaOpinion) const Insignia(estado: '', texto: 'Segunda opinión', tipo: TipoPildora.info),
            if (porAceptar) const Insignia(estado: '', texto: 'Por aceptar', tipo: TipoPildora.advertencia),
            if (v.declinada) const Insignia(estado: '', texto: 'Declinada', tipo: TipoPildora.peligro),
          ]),
          if (v.esSegundaOpinion) ...[
            const SizedBox(height: 10),
            const CajaAviso(
              tipo: TipoAviso.info,
              icono: Icons.balance_rounded,
              texto: 'Segunda opinión: el vendedor impugnó el rechazo de otra verificadora. Verifica de forma independiente; '
                  'el honorario lo paga quien la pidió.',
            ),
          ],
          const Rotulo('Lote'),
          SeccionDatos(filas: [
            ('Camaronera', v.vendedor.nombre),
            ('Compra de', v.comprador.nombre),
            if (v.instalacion != null) ('Finca', v.instalacion!),
            if (v.piscina != null) ('Piscina', v.piscina!),
            if (v.loteCodigo != null) ('Código de lote', v.loteCodigo!),
            if (v.soyVerificadora || v.soyApoyo) ('Responsable', v.tecnico?.nombre ?? 'Sin asignar'),
            if (apoyo.isNotEmpty) ('De apoyo', apoyo.map((t) => t.nombre).join(', ')),
          ]),
          if (plataforma && !v.cerrada) ..._plazos(context, v),
          if (v.despachoId != null) ...[
            const Rotulo('Camión'),
            SeccionDatos(filas: [
              ('Llegada prevista', v.despachoEta == null ? 'Sin programar' : fmt.format(v.despachoEta!)),
            ]),
          ],
          if (v.estado == 'rejected') ..._rechazo(context, v),
          const Rotulo('Informe en el servidor'),
          if (!hayInforme)
            Tarjeta(
              child: Text('Todavía no se ha cargado ningún dato de campo.', style: TextStyle(color: c.tintaSuave)),
            )
          else if (v.esLarva)
            SeccionDatos(filas: [
              ('Cantidad verificada', _hay(larva['qty_verified']) ? NumberFormat('#,##0').format(larva['qty_verified']) : '—'),
              ('Supervivencia', _pct(larva['survival_rate'])),
              ('Tamaño promedio', _hay(larva['avg_size_mg']) ? '${NumberFormat('#,##0.##').format(larva['avg_size_mg'])} mg' : '—'),
              ('Estado sanitario', _sanidad(larva['health_status'])),
              ('Fotos', '${v.crudo['photo_count'] ?? 0}'),
            ])
          else
            SeccionDatos(filas: [
              ('Libras enviadas', _lb(pesos['sent_lb'])),
              ('Libras en planta', _lb(pesos['plant_lb'])),
              ('Basura', _lb(pesos['trash_lb'])),
              ('Rendimiento', _pct(clasif['yield_pct'])),
              ('Clase A', _pct(clasif['yield_class_a_pct'])),
              ('Clasificación', '${(clasif['lines'] as List?)?.length ?? 0} líneas'),
              ('Conteos', '${(v.crudo['counts'] as List?)?.length ?? 0}'),
              ('Fotos', '${v.crudo['photo_count'] ?? 0}'),
            ]),
          if (avisos.isNotEmpty && !v.cerrada) ...[
            const SizedBox(height: 10),
            CajaAviso(
              tipo: TipoAviso.advertencia,
              icono: Icons.error_outline,
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('Revisa antes de dictaminar:', style: TextStyle(fontWeight: FontWeight.w700)),
                for (final a in avisos) Text('• $a'),
              ]),
            ),
          ],
          const Rotulo('Acciones'),
          if (soloLectura) ...[
            // Modo supervisor: ni responder, ni asignar, ni campo, ni informe, ni fotos, ni veredicto.
            const AvisoSoloLectura(),
            if (v.cerrada) const SizedBox(height: 10),
          ] else if (v.declinada) ...[
            Tarjeta(
              child: Text(
                  'Declinaste esta orden${v.motivoDeclinada == null ? '' : ' (motivo: ${v.motivoDeclinada})'}. '
                  'El comprador elegirá otra verificadora o cancelará la compra.',
                  style: TextStyle(color: c.tintaSuave)),
            ),
          ] else ...[
            if (v.soyVerificadora && ordenAbierta) ..._respuesta(context, sinc, v, porAceptar),
            if (v.soyVerificadora && !v.cerrada) ...[
              OutlinedButton.icon(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaAsignarTecnico(id: v.id))),
                icon: const Icon(Icons.engineering_outlined),
                label: Text(v.tecnicoAsignado ? 'Cambiar técnico responsable' : 'Asignar técnico'),
              ),
              const SizedBox(height: 10),
              if (v.tecnicoAsignado) ...[
                OutlinedButton.icon(
                  onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaAsignarApoyo(id: v.id))),
                  icon: const Icon(Icons.groups_outlined),
                  label: Text(apoyo.isEmpty ? 'Sumar técnicos de apoyo' : 'Equipo de campo (${apoyo.length} de apoyo)'),
                ),
                const SizedBox(height: 10),
              ],
            ],
            if (v.esperaTecnico && !v.soyVerificadora)
              Tarjeta(
                child: Text('Todavía no tiene técnico asignado. Cuando la verificadora lo asigne podrás iniciar el trabajo de campo.',
                    style: TextStyle(color: c.tintaSuave)),
              ),
            if (v.esperaTecnico && v.soyVerificadora)
              Tarjeta(
                child: Text('Asigna un técnico para poder iniciar el trabajo de campo.', style: TextStyle(color: c.tintaSuave)),
              ),
            if (v.esperaTecnico) const SizedBox(height: 10),
            if (v.puedeIniciarCampo) ...[
              BotonAccion(
                onPressed: () async {
                  try {
                    await sinc.iniciarCampo(v);
                  } on ErrorSeguridad catch (e) {
                    if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
                    return;
                  }
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Trabajo de campo iniciado')));
                  }
                },
                icono: Icons.play_arrow_rounded,
                texto: 'Iniciar trabajo de campo',
                textoOcupado: 'Iniciando…',
              ),
              const SizedBox(height: 10),
            ],
            if (v.despachoId != null && !v.cerrada) ...[
              BotonAccion(
                relleno: false,
                onPressed: () => _registrarLlegada(context, sinc, v),
                icono: Icons.local_shipping_outlined,
                texto: 'Registrar llegada del camión',
              ),
              const SizedBox(height: 10),
            ],
            if (!v.cerrada) ...[
              FilledButton.icon(
                style: FilledButton.styleFrom(backgroundColor: v.enCampo ? c.acento : c.marca),
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaInforme(id: v.id))),
                icon: const Icon(Icons.edit_note_rounded),
                label: const Text('Informe de campo'),
              ),
              const SizedBox(height: 10),
            ],
            if (v.enCampo || v.estado == 'done') ...[
              OutlinedButton.icon(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaFotos(id: v.id))),
                icon: const Icon(Icons.photo_camera_outlined),
                label: const Text('Fotos de campo'),
              ),
              const SizedBox(height: 10),
              if (v.puedoDictaminar)
                FilledButton.icon(
                  style: EstilosBoton.relleno(c.exito),
                  onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaVeredicto(id: v.id))),
                  icon: const Icon(Icons.gavel_rounded),
                  label: const Text('Dar veredicto'),
                )
              else
                AvisoApoyo(v: v),
              const SizedBox(height: 10),
            ] else if (!v.cerrada && v.estado == 'assigned') ...[
              Text('Las fotos y el veredicto se habilitan al iniciar el trabajo de campo.',
                  style: TextStyle(fontSize: 13, color: c.tintaSuave)),
              const SizedBox(height: 10),
            ],
            if (admiteNoPresentado(v))
              OutlinedButton.icon(
                style: EstilosBoton.peligro(context),
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaNoPresentado(id: v.id))),
                icon: const Icon(Icons.visibility_off_outlined),
                label: const Text('El producto no se presentó'),
              ),
          ],
          if (v.cerrada)
            Tarjeta(
              child: Text('Esta verificación está cerrada. Puedes consultarla, pero ya no se edita.', style: TextStyle(color: c.tintaSuave)),
            ),
        ],
      ),
    );
  }

  /// Plazos de la verificadora, como el resumen de la orden del portal.
  static List<Widget> _plazos(BuildContext context, Verificacion v) {
    final filas = <(String, String)>[
      if (v.respuestaOrden == 'pending' && v.plazoRespuesta != null && const {'received', 'assigned'}.contains(v.estado))
        ('Aceptar o declinar hasta', fechaHoraDe(v.plazoRespuesta!)),
      if (v.estado == 'received' && v.plazoAsignar != null) ('Asignar técnico hasta', fechaHoraDe(v.plazoAsignar!)),
      if (v.plazoVeredicto != null) ('Veredicto hasta', fechaHoraDe(v.plazoVeredicto!)),
    ];
    final vencido = v.plazoVencidoEtiqueta;
    if (filas.isEmpty && vencido == null) return const [];
    return [
      const Rotulo('Plazos'),
      if (vencido != null) ...[
        CajaAviso(
          tipo: TipoAviso.peligro,
          icono: Icons.schedule_rounded,
          texto: '$vencido. El comprador puede cambiar de verificadora o cancelar sin costo.',
        ),
        if (filas.isNotEmpty) const SizedBox(height: 10),
      ],
      if (filas.isNotEmpty) SeccionDatos(filas: filas),
    ];
  }

  /// Cómo quedó el rechazo: por «producto no presentado» (firme) o por el
  /// informe (con o sin segunda opinión).
  static List<Widget> _rechazo(BuildContext context, Verificacion v) {
    final String texto;
    if (v.rechazoNoPresentado) {
      final llegada = v.llegadaNoPresentado;
      texto = 'Rechazada: producto no presentado${llegada == null ? '' : ' (llegada a la planta: ${fechaHoraDe(llegada)})'}. '
          'El honorario lo paga el vendedor y suma un strike. No admite segunda opinión.';
    } else {
      final estado = v.impugnacionEtiqueta;
      final plazo = v.plazoImpugnacion;
      texto = switch (v.estadoImpugnacion) {
        'open' => 'Rechazada por el informe. El vendedor puede pedir una segunda opinión'
            '${plazo == null ? '' : ' hasta el ${fechaHoraDe(plazo)}'}; si no la pide, la compra se cancela.',
        'contested' => 'Rechazada por el informe. El vendedor pidió una segunda opinión: otra verificadora vuelve a verificar.',
        _ => 'Rechazada por el informe${estado == null ? '' : ' · $estado'}.',
      };
    }
    return [
      const Rotulo('Rechazo'),
      CajaAviso(tipo: TipoAviso.peligro, icono: Icons.cancel_outlined, texto: texto),
    ];
  }

  /// Aceptar o declinar la orden (administrador de la verificadora).
  static List<Widget> _respuesta(BuildContext context, Sincronizador sinc, Verificacion v, bool porAceptar) {
    if (!porAceptar && !v.puedoDeclinar) return const [];
    final plazo = v.plazoRespuesta;
    return [
      if (porAceptar)
        CajaAviso(
          tipo: TipoAviso.advertencia,
          icono: Icons.inbox_outlined,
          texto: '¿Aceptas la orden? Puedes aceptarla o declinarla${plazo == null ? '' : ' hasta el ${fechaHoraDe(plazo)}'}. '
              'Asignar el técnico también la acepta. Si no respondes a tiempo, cuenta como declinada y el comprador elige otra verificadora.',
        ),
      const SizedBox(height: 10),
      if (porAceptar && v.puedoResponder) ...[
        BotonAccion(
          onPressed: () => _responder(context, sinc, v, 'accept'),
          icono: Icons.check_rounded,
          texto: 'Aceptar la orden',
          textoOcupado: 'Enviando…',
        ),
        const SizedBox(height: 10),
      ],
      if (v.puedoDeclinar) ...[
        BotonAccion(
          relleno: false,
          estilo: EstilosBoton.peligro(context),
          onPressed: () => _responder(context, sinc, v, 'decline'),
          icono: Icons.block_rounded,
          texto: 'Declinar la orden',
        ),
        const SizedBox(height: 10),
      ],
    ];
  }

  static Future<void> _responder(BuildContext context, Sincronizador sinc, Verificacion v, String decision) async {
    String? motivo;
    if (decision == 'decline') {
      motivo = await showDialog<String>(context: context, builder: (_) => const _DialogoDeclinar());
      if (motivo == null || !context.mounted) return;
    }
    final r = await sinc.responderOrden(v, decision, motivo: motivo);
    if (!context.mounted) return;
    avisarResultado(context, r, hecho: decision == 'accept' ? 'Orden aceptada' : 'Orden declinada');
  }

  static bool _hay(dynamic v) => v != null && v is num && v > 0;
  static String _sanidad(dynamic v) => switch (v) {
        'excellent' => 'Excelente',
        'good' => 'Bueno',
        'acceptable' => 'Aceptable',
        'rejected' => 'Rechazado',
        _ => '—',
      };
  static String _lb(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.##').format(v)} lb';
  static String _pct(dynamic v) => !_hay(v) ? '—' : '${NumberFormat('#,##0.#').format(v)} %';

  Future<void> _registrarLlegada(BuildContext context, Sincronizador sinc, Verificacion v) async {
    final ahora = DateTime.now();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Llegada del camión'),
        content: Text('Se registrará la llegada ahora, ${DateFormat('dd/MM/yyyy').format(ahora)} a las ${DateFormat('HH:mm').format(ahora)}.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Registrar')),
        ],
      ),
    );
    if (ok == true) {
      final bool registrada;
      try {
        registrada = await sinc.registrarLlegadaCamion(v, ahora);
      } on ErrorSeguridad catch (e) {
        if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
        return;
      }
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(registrada ? 'Llegada registrada' : 'Esta verificación no tiene despacho asociado.')));
      }
    }
  }
}

/// Pide el motivo para declinar la orden (obligatorio: lo ve el comprador).
class _DialogoDeclinar extends StatefulWidget {
  const _DialogoDeclinar();
  @override
  State<_DialogoDeclinar> createState() => _DialogoDeclinarState();
}

class _DialogoDeclinarState extends State<_DialogoDeclinar> {
  final _motivo = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _motivo.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Declinar la orden'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('El comprador elegirá otra verificadora o cancelará sin costo. Explica el motivo: el comprador lo verá.'),
          const SizedBox(height: 12),
          TextField(
            controller: _motivo,
            maxLines: 3,
            maxLength: 2000,
            autofocus: true,
            decoration: InputDecoration(
              labelText: 'Motivo (obligatorio)',
              hintText: 'Ej.: sin capacidad en esa fecha, conflicto de interés…',
              errorText: _error,
            ),
            onChanged: (_) {
              if (_error != null) setState(() => _error = null);
            },
          ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Volver')),
          FilledButton(
            style: EstilosBoton.peligroRelleno(context),
            onPressed: () {
              final t = _motivo.text.trim();
              if (t.isEmpty) {
                setState(() => _error = 'Escribe el motivo');
                return;
              }
              Navigator.pop(context, t);
            },
            child: const Text('Declinar'),
          ),
        ],
      );
}
