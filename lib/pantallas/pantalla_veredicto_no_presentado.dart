import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:intl/intl.dart';
import 'package:path/path.dart' as p;
import 'package:provider/provider.dart';

import '../datos/base_local.dart';
import '../datos/sincronizador.dart';
import '../modelos/foto_campo.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/widgets.dart';

/// El rechazo por «Producto no presentado» se puede firmar sin trabajo de
/// campo (desde «Asignada»), como en el portal. Solo lo firma quien firma
/// el veredicto (responsable o empresa), nunca el técnico de apoyo.
bool admiteNoPresentado(Verificacion v) =>
    v.modo == 'platform' && v.puedoDictaminar && const {'assigned', 'in_field', 'done'}.contains(v.estado);

/// Rechazo por «Producto no presentado»: el técnico llegó a la planta y el
/// producto no estaba. Exige la hora de llegada, qué encontró y al menos una
/// foto (si la verificación todavía no tiene). El honorario lo paga el
/// vendedor, que recibe un strike; la compra se cancela y no hay segunda
/// opinión.
class PantallaNoPresentado extends StatefulWidget {
  final String id;
  const PantallaNoPresentado({super.key, required this.id});
  @override
  State<PantallaNoPresentado> createState() => _PantallaNoPresentadoState();
}

class _PantallaNoPresentadoState extends State<PantallaNoPresentado> {
  final _form = GlobalKey<FormState>();
  final _notas = TextEditingController();
  DateTime _llegada = DateTime.now();
  final List<String> _fotos = [];
  int _fotosPrevias = 0;
  bool _tomando = false;

  @override
  void initState() {
    super.initState();
    _contarFotosPrevias();
  }

  @override
  void dispose() {
    _notas.dispose();
    super.dispose();
  }

  Verificacion? get _v => context.read<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;

  /// Fotos que la verificación ya tiene en el servidor (o que salen antes
  /// que este rechazo en la cola): con ellas la foto nueva es opcional.
  Future<void> _contarFotosPrevias() async {
    final v = _v;
    if (v == null) return;
    final enServidor = v.crudo['photo_count'] is num ? (v.crudo['photo_count'] as num).toInt() : 0;
    var locales = 0;
    // Las fotos sueltas solo las admite el servidor con la verificación en
    // campo o con el informe listo.
    if (const {'in_field', 'done'}.contains(v.estado)) {
      try {
        final fotos = await context.read<Sincronizador>().fotosDe(v.id);
        locales = fotos.where((f) => f.estado == EstadoFoto.pendiente).length;
      } catch (_) {
        // Sin base local: cuentan solo las del servidor.
      }
    }
    if (mounted) setState(() => _fotosPrevias = enServidor + locales);
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));
  }

  Future<void> _elegirHora() async {
    final ahora = DateTime.now();
    final dia = await showDatePicker(
      context: context,
      initialDate: _llegada,
      firstDate: ahora.subtract(const Duration(days: 30)),
      lastDate: ahora,
      helpText: 'Día de llegada a la planta',
    );
    if (dia == null || !mounted) return;
    final hora = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_llegada),
      helpText: 'Hora de llegada a la planta',
    );
    if (hora == null || !mounted) return;
    setState(() => _llegada = DateTime(dia.year, dia.month, dia.day, hora.hour, hora.minute));
  }

  Future<void> _tomarFoto(FormFieldState<List<String>> campo) async {
    if (_tomando) return;
    if (_fotos.length >= maximoFotosEvidencia) {
      _aviso('Puedes adjuntar hasta $maximoFotosEvidencia fotos.');
      return;
    }
    setState(() => _tomando = true);
    try {
      final foto = await ImagePicker().pickImage(source: ImageSource.camera, maxWidth: 2400, maxHeight: 2400, imageQuality: 90);
      if (foto == null || !mounted) return;
      // Se guarda en la carpeta de la app: no se pierde si se cierra.
      final carpeta = Directory(p.join((await BaseLocal.carpetaFotos()).path, widget.id));
      await carpeta.create(recursive: true);
      final destino = p.join(carpeta.path, 'evidencia_${DateTime.now().millisecondsSinceEpoch}.jpg');
      await File(foto.path).copy(destino);
      if (!mounted) return;
      setState(() => _fotos.add(destino));
      campo.didChange(List.of(_fotos));
    } catch (_) {
      _aviso('No se pudo tomar la foto. Revisa el permiso de la cámara e inténtalo otra vez.', error: true);
    } finally {
      if (mounted) setState(() => _tomando = false);
    }
  }

  /// Lleva la pantalla al primer campo con error (el de más arriba).
  void _irAlPrimerError(Set<FormFieldState<Object?>> campos) {
    double alto(FormFieldState<Object?> c) {
      final r = c.context.findRenderObject();
      return r is RenderBox && r.attached ? r.localToGlobal(Offset.zero).dy : double.infinity;
    }

    final primero = campos.reduce((a, b) => alto(a) <= alto(b) ? a : b);
    if (!primero.mounted) return;
    Scrollable.ensureVisible(primero.context, duration: const Duration(milliseconds: 350), alignment: 0.2);
  }

  Future<void> _firmar() async {
    final form = _form.currentState;
    final v = _v;
    if (form == null) return;
    if (v == null) {
      _aviso('Esta verificación ya no está en tu bandeja.');
      return;
    }
    final conError = form.validateGranularly();
    if (conError.isNotEmpty) {
      FocusManager.instance.primaryFocus?.unfocus();
      _aviso('Revisa los campos marcados en rojo.', error: true);
      _irAlPrimerError(conError);
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Rechazar: producto no presentado'),
        content: const Text('El honorario lo paga el vendedor, que además recibe un strike. La compra se cancela y este '
            'rechazo no admite segunda opinión. No se puede deshacer desde la app.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligro(c), onPressed: () => Navigator.pop(c, true), child: const Text('Firmar el rechazo')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await context.read<Sincronizador>().rechazarNoPresentado(v, llegada: _llegada, notas: _notas.text, fotos: List.of(_fotos));
    } on ErrorSeguridad catch (e) {
      _aviso(e.mensaje, error: true);
      return;
    } on FormatException catch (e) {
      _aviso('No se pudo preparar una foto: ${e.message} Bórrala y toma otra.', error: true);
      return;
    } catch (_) {
      _aviso('No se pudo guardar el rechazo. Inténtalo otra vez.', error: true);
      return;
    }
    if (!mounted) return;
    _aviso('Rechazo guardado; se enviará con internet');
    Navigator.popUntil(context, (r) => r.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final v = context.watch<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Producto no presentado')),
        body: const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.'),
      );
    }
    final c = context.colores;
    final fmt = DateFormat('dd/MM/yyyy HH:mm');
    return Scaffold(
      appBar: AppBar(title: const Text('Producto no presentado')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        // Column (no ListView): todos los campos existen siempre y se puede
        // llevar la vista al primero con error.
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            if (!admiteNoPresentado(v)) ...[
              const CajaAviso(
                tipo: TipoAviso.advertencia,
                icono: Icons.lock_outline,
                texto: 'Este rechazo lo firma el técnico responsable o la empresa, con la verificación asignada o en campo.',
              ),
              const SizedBox(height: 12),
            ],
            const CajaAviso(
              tipo: TipoAviso.peligro,
              icono: Icons.visibility_off_outlined,
              texto: 'Úsalo solo si llegaste a la planta y el producto no estaba. Es el único veredicto sin trabajo de campo. '
                  'El honorario lo paga el vendedor y recibe un strike; la compra se cancela, sin segunda opinión.',
            ),
            const Rotulo('Hora de llegada a la planta'),
            FormField<DateTime>(
              initialValue: _llegada,
              validator: (_) => _llegada.isAfter(DateTime.now().add(const Duration(minutes: 5)))
                  ? 'La hora de llegada no puede ser futura'
                  : null,
              builder: (campo) => Tarjeta(
                onTap: () async {
                  await _elegirHora();
                  campo.didChange(_llegada);
                },
                colorBorde: campo.hasError ? c.peligro : null,
                child: Row(children: [
                  Icon(Icons.schedule_rounded, color: c.marcaTexto),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(fmt.format(_llegada), style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo, fontSize: 16)),
                      Text(campo.errorText ?? 'Toca para cambiar el día o la hora', style: TextStyle(fontSize: 13, color: campo.hasError ? c.peligroTexto : c.tintaSuave)),
                    ]),
                  ),
                  Icon(Icons.edit_calendar_outlined, color: c.tintaSuave),
                ]),
              ),
            ),
            const Rotulo('Fotos de evidencia'),
            FormField<List<String>>(
              initialValue: const [],
              validator: (_) => _fotos.isEmpty && _fotosPrevias == 0 ? 'Toma al menos una foto (portón, báscula, planta vacía…)' : null,
              builder: (campo) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                if (_fotosPrevias > 0) ...[
                  Text(
                      _fotosPrevias == 1
                          ? 'La verificación ya tiene 1 foto: las nuevas son opcionales.'
                          : 'La verificación ya tiene $_fotosPrevias fotos: las nuevas son opcionales.',
                      style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                  const SizedBox(height: 8),
                ],
                if (_fotos.isNotEmpty) ...[
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    for (final f in _fotos)
                      Stack(children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(Radios.campo),
                          child: Image.file(File(f), width: 96, height: 96, fit: BoxFit.cover,
                              errorBuilder: (_, _, _) => Container(width: 96, height: 96, color: c.fondoAlt)),
                        ),
                        Positioned(
                          right: 0,
                          top: 0,
                          child: IconButton.filledTonal(
                            tooltip: 'Quitar foto',
                            visualDensity: VisualDensity.compact,
                            onPressed: () {
                              setState(() => _fotos.remove(f));
                              campo.didChange(List.of(_fotos));
                            },
                            icon: const Icon(Icons.close, size: 18),
                          ),
                        ),
                      ]),
                  ]),
                  const SizedBox(height: 10),
                ],
                OutlinedButton.icon(
                  style: campo.hasError ? OutlinedButton.styleFrom(side: BorderSide(color: c.peligro)) : null,
                  onPressed: _tomando || _fotos.length >= maximoFotosEvidencia ? null : () => _tomarFoto(campo),
                  icon: const Icon(Icons.photo_camera_outlined),
                  label: Text(_tomando ? 'Abriendo la cámara…' : 'Tomar foto (${_fotos.length}/$maximoFotosEvidencia)'),
                ),
                if (campo.hasError) ...[
                  const SizedBox(height: 6),
                  Text(campo.errorText!, style: TextStyle(fontSize: 12.5, color: c.peligroTexto)),
                ],
              ]),
            ),
            const Rotulo('Qué encontraste'),
            TextFormField(
              controller: _notas,
              maxLines: 4,
              maxLength: 4000,
              decoration: const InputDecoration(
                labelText: 'Explicación (obligatoria)',
                hintText: 'Ej.: llegué a la hora acordada y el camión no estaba; esperé 2 horas…',
              ),
              validator: (t) => (t ?? '').trim().isEmpty ? 'Explica qué encontraste al llegar' : null,
            ),
            const SizedBox(height: 20),
            BotonAccion(
              estilo: EstilosBoton.peligroRelleno(context),
              onPressed: admiteNoPresentado(v) ? _firmar : null,
              icono: Icons.gavel_rounded,
              texto: 'Firmar el rechazo',
              textoOcupado: 'Preparando las fotos…',
            ),
          ]),
        ),
      ),
    );
  }
}
