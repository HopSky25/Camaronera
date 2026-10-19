import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';

import '../../datos/api_cliente.dart';
import '../../datos/lotes_vendedor.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/widgets.dart';
import 'pantalla_fotos_lote.dart';
import 'pantalla_mis_certificados.dart';

/// Un archivo elegido para el certificado: nombre y bytes.
typedef ArchivoElegido = ({String nombre, Uint8List bytes});

/// Cómo se elige el PDF del certificado (por defecto file_picker). Se
/// cambia en las pruebas.
typedef ElegirPdf = Future<ArchivoElegido?> Function();

Future<ArchivoElegido?> elegirPdfConFilePicker() async {
  final r = await FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions: const ['pdf'], withData: true);
  final f = r?.files.firstOrNull;
  final b = f?.bytes;
  return f == null || b == null ? null : (nombre: f.name, bytes: b);
}

/// Botón del ancho de su texto (el tema los hace de todo el ancho).
final _enLinea = OutlinedButton.styleFrom(minimumSize: const Size(0, 48));

TipoPildora _pildora(String? estado) => switch (estado) {
      'approved' => TipoPildora.ok,
      'rejected' => TipoPildora.peligro,
      _ => TipoPildora.advertencia,
    };

String _detalleCertificado(Map<String, dynamic> c) => [
      if (c['issuer'] is String) c['issuer'] as String,
      if (c['number'] is String) 'N.º ${c['number']}',
      c['expiry_date'] is String ? 'vence ${fecha(c['expiry_date'])}' : 'sin vencimiento',
    ].join(' · ');

/// Certificados de un lote propio: los del lote (con su revisión), ligar uno
/// vigente de «mis certificados» y subir uno propio del lote con su PDF o
/// foto. Como el formulario del portal. No se firman.
class PantallaCertificadosLote extends StatefulWidget {
  final String productoId;
  final String nombre;
  final ElegirImagenes elegirImagen;
  final ComprimirImagen comprimir;
  final ElegirPdf elegirPdf;
  const PantallaCertificadosLote({
    super.key,
    required this.productoId,
    this.nombre = '',
    this.elegirImagen = elegirConImagePicker,
    this.comprimir = comprimirEnSegundoPlano,
    this.elegirPdf = elegirPdfConFilePicker,
  });
  @override
  State<PantallaCertificadosLote> createState() => _PantallaCertificadosLoteState();
}

class _PantallaCertificadosLoteState extends State<PantallaCertificadosLote> {
  Map<String, dynamic>? _datos;
  String? _error;
  bool _ocupado = false;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    setState(() => _error = null);
    try {
      final d = await context.read<Sincronizador>().certificadosLote(widget.productoId);
      if (mounted) setState(() => _datos = d);
    } catch (e) {
      if (mounted) setState(() => _error = e is ErrorApi ? mensajeLote(e) : mensajeDeError(e));
    }
  }

  List<Map<String, dynamic>> _lista(String clave) {
    final l = _datos?[clave];
    return l is List ? [for (final x in l.whereType<Map>()) Map<String, dynamic>.from(x)] : [];
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(texto),
        backgroundColor: error ? context.colores.peligro : null,
        duration: Duration(seconds: error ? 6 : 3),
      ));
  }

  Future<void> _hacer(Future<Map<String, dynamic>> Function(Sincronizador s) accion, String hecho) async {
    if (_ocupado) return;
    setState(() => _ocupado = true);
    try {
      final d = await accion(context.read<Sincronizador>());
      if (mounted) setState(() => _datos = d);
      _aviso(hecho);
    } on SinConexion {
      _aviso('Sin internet: no se guardó el cambio. Inténtalo cuando tengas señal.', error: true);
    } on ErrorApi catch (e) {
      _aviso(mensajeLote(e), error: true);
    } catch (e) {
      _aviso(mensajeDeError(e), error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _quitar(Map<String, dynamic> linea) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Quitar el certificado'),
        content: Text(linea['source'] == 'account'
            ? '«${nombreDe(linea['certificate'])}» deja de mostrarse en este lote. Sigue en tus certificados de la cuenta.'
            : '«${nombreDe(linea['certificate'])}» deja de mostrarse en este lote.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Quitar')),
        ],
      ),
    );
    if (ok != true) return;
    await _hacer((s) => s.quitarCertificadoLote(widget.productoId, '${linea['id']}'), 'Certificado quitado del lote.');
  }

  Future<void> _subir() async {
    final subido = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => PantallaSubirCertificado(
          catalogo: _lista('catalog'),
          guardar: (s, c, idem) => s.subirCertificadoLote(widget.productoId, c, idempotencia: idem),
          elegirImagen: widget.elegirImagen,
          comprimir: widget.comprimir,
          elegirPdf: widget.elegirPdf,
        ),
      ),
    );
    if (subido != null && mounted) {
      setState(() => _datos = subido);
      _aviso('Certificado subido: queda en revisión hasta que la plataforma lo apruebe.');
    }
  }

  /// Corregir número, fechas o archivo de uno propio del lote.
  Future<void> _editar(Map<String, dynamic> linea) async {
    final tipo = _lista('catalog').where((t) => t['id'] == idDe(linea['certificate'])).firstOrNull;
    final r = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => PantallaSubirCertificado(
          catalogo: _lista('catalog'),
          inicial: CertificadoNuevo.desde(linea, tipo: tipo),
          guardar: (s, c, _) => s.editarCertificadoLote(widget.productoId, '${linea['id']}', c),
          elegirImagen: widget.elegirImagen,
          comprimir: widget.comprimir,
          elegirPdf: widget.elegirPdf,
        ),
      ),
    );
    if (r != null && mounted) {
      setState(() => _datos = r);
      _aviso('Cambios guardados: el certificado vuelve a revisión.');
    }
  }

  Future<void> _abrirMisCertificados() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaMisCertificados()));
    if (mounted) await _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final c = context.colores;
    final lineas = _lista('data');
    final disponibles = _lista('available');
    return Scaffold(
      appBar: AppBar(title: const Text('Certificados del lote')),
      body: _datos == null && _error == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              color: c.marcaTexto,
              onRefresh: _cargar,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                children: [
                  if (widget.nombre.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(widget.nombre, style: Theme.of(context).textTheme.titleLarge),
                    ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    CajaError(texto: _error!, reintentar: _cargar),
                  ],
                  if (soloLectura) ...[const SizedBox(height: 12), const AvisoSoloLectura()],
                  const Rotulo('En este lote'),
                  if (lineas.isEmpty)
                    Text('Este lote todavía no tiene certificados.', style: TextStyle(color: c.tintaSuave))
                  else
                    for (final l in lineas)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Tarjeta(
                          padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
                          child: Row(children: [
                            Expanded(
                              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                Text(nombreDe(l['certificate']), style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo)),
                                const SizedBox(height: 4),
                                Text(_detalleCertificado(l), style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                                Text(l['source'] == 'account' ? 'De mis certificados' : 'Propio del lote',
                                    style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                                const SizedBox(height: 6),
                                Insignia(estado: '${l['status']}', texto: estadoCertificado(l['status'] as String?), tipo: _pildora(l['status'] as String?)),
                              ]),
                            ),
                            if (!soloLectura && l['source'] != 'account')
                              IconButton(
                                tooltip: 'Corregir el certificado',
                                onPressed: _ocupado ? null : () => _editar(l),
                                icon: Icon(Icons.edit_outlined, color: c.tintaSuave),
                              ),
                            if (!soloLectura)
                              IconButton(
                                tooltip: 'Quitar del lote',
                                onPressed: _ocupado ? null : () => _quitar(l),
                                icon: Icon(Icons.delete_outline, color: c.peligroTexto),
                              ),
                          ]),
                        ),
                      ),
                  if (!soloLectura) ...[
                    const Rotulo('De mis certificados'),
                    if (disponibles.isEmpty)
                      Text('No tienes otros certificados vigentes en tu cuenta para agregar. '
                          'Los que subes a tu cuenta sirven después para todos tus lotes, cuando la plataforma los aprueba.',
                          style: TextStyle(color: c.tintaSuave))
                    else
                      for (final d in disponibles)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: Tarjeta(
                            padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
                            child: Row(children: [
                              Expanded(
                                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                  Text(nombreDe(d['certificate']), style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo)),
                                  const SizedBox(height: 4),
                                  Text(_detalleCertificado(d), style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                                ]),
                              ),
                              const SizedBox(width: 8),
                              OutlinedButton(
                                style: _enLinea,
                                onPressed: _ocupado
                                    ? null
                                    : () => _hacer(
                                        (s) => s.ligarCertificadoLote(widget.productoId, '${d['id']}', idempotencia: const Uuid().v4()),
                                        'Certificado agregado al lote.'),
                                child: const Text('Agregar'),
                              ),
                            ]),
                          ),
                        ),
                    const SizedBox(height: 4),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: _ocupado ? null : _abrirMisCertificados,
                        icon: const Icon(Icons.badge_outlined),
                        label: const Text('Mis certificados de la cuenta'),
                      ),
                    ),
                    const Rotulo('Propio del lote'),
                    Text('Sube un PCR u otro certificado solo de este lote, en PDF o foto. '
                        'La plataforma lo revisa antes de mostrarlo a los compradores.',
                        style: TextStyle(color: c.tintaSuave)),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: _ocupado || _datos == null ? null : _subir,
                        icon: const Icon(Icons.upload_file_outlined),
                        label: const Text('Subir certificado'),
                      ),
                    ),
                  ],
                ],
              ),
            ),
    );
  }
}

/// Guarda el certificado del formulario (subir, corregir o renovar) y
/// devuelve la respuesta del servidor.
typedef GuardarCertificado = Future<Map<String, dynamic>> Function(Sincronizador s, CertificadoNuevo c, String idempotencia);

/// «Subir certificado» (de un lote o de la cuenta): tipo del catálogo,
/// número, fechas y el archivo (PDF, o foto de la cámara o la galería). Con
/// [inicial] corrige o renueva uno ya guardado: el tipo no cambia y el
/// archivo solo va si se elige otro. Devuelve la respuesta del servidor si
/// se guardó.
class PantallaSubirCertificado extends StatefulWidget {
  final List<Map<String, dynamic>> catalogo;
  final GuardarCertificado guardar;
  final CertificadoNuevo? inicial;
  final ElegirImagenes elegirImagen;
  final ComprimirImagen comprimir;
  final ElegirPdf elegirPdf;
  const PantallaSubirCertificado({
    super.key,
    required this.catalogo,
    required this.guardar,
    this.inicial,
    this.elegirImagen = elegirConImagePicker,
    this.comprimir = comprimirEnSegundoPlano,
    this.elegirPdf = elegirPdfConFilePicker,
  });
  @override
  State<PantallaSubirCertificado> createState() => _PantallaSubirCertificadoState();
}

class _PantallaSubirCertificadoState extends State<PantallaSubirCertificado> {
  final _form = GlobalKey<FormState>();
  late final CertificadoNuevo _cert = widget.inicial ?? CertificadoNuevo();
  late final _numero = TextEditingController(text: _cert.numero);
  bool get _editando => _cert.editando;
  final _errServidor = <String, String>{};
  bool _ocupado = false;
  String? _error;
  String? _idem;
  String? _ultimo;

  @override
  void dispose() {
    _numero.dispose();
    super.dispose();
  }

  String? _err(String campo) => _cert.errores()[campo] ?? _errServidor[campo];

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));
  }

  void _irAlPrimerError(Set<FormFieldState<Object?>> campos) {
    double alto(FormFieldState<Object?> c) {
      final r = c.context.findRenderObject();
      return r is RenderBox && r.attached ? r.localToGlobal(Offset.zero).dy : double.infinity;
    }

    final primero = campos.reduce((a, b) => alto(a) <= alto(b) ? a : b);
    if (!primero.mounted) return;
    Scrollable.ensureVisible(primero.context, duration: const Duration(milliseconds: 350), alignment: 0.2);
  }

  Future<void> _elegirArchivo(String origen) async {
    try {
      ArchivoElegido? a;
      if (origen == 'pdf') {
        a = await widget.elegirPdf();
      } else {
        final fotos = await widget.elegirImagen(origen == 'camara' ? ImageSource.camera : ImageSource.gallery, 1);
        if (fotos.isNotEmpty) {
          a = (nombre: 'certificado_${DateTime.now().millisecondsSinceEpoch}.jpg', bytes: await widget.comprimir(fotos.first));
        }
      }
      if (a == null || !mounted) return;
      final elegido = a;
      setState(() {
        _cert
          ..nombreArchivo = elegido.nombre
          ..archivo = elegido.bytes;
        _errServidor.remove('archivo');
      });
      _form.currentState?.validate();
    } on FormatException {
      _aviso('No se pudo leer esa imagen.', error: true);
    } catch (_) {
      _aviso('No se pudo abrir el archivo. Revisa el permiso e inténtalo otra vez.', error: true);
    }
  }

  Future<void> _elegirFecha(bool vence) async {
    final hoy = DateTime.now();
    final actual = vence ? _cert.vence : _cert.emision;
    final f = await showDatePicker(
      context: context,
      initialDate: actual ?? hoy,
      firstDate: DateTime(hoy.year - 10),
      lastDate: DateTime(hoy.year + 15),
    );
    if (f == null || !mounted) return;
    setState(() {
      if (vence) {
        _cert.vence = f;
      } else {
        _cert.emision = f;
      }
      _errServidor.remove(vence ? 'vence' : 'emision');
    });
  }

  Future<void> _guardar() async {
    if (_ocupado) return;
    final form = _form.currentState;
    if (form == null) return;
    final conError = form.validateGranularly();
    if (conError.isNotEmpty) {
      FocusManager.instance.primaryFocus?.unfocus();
      _aviso('Revisa los campos marcados en rojo.', error: true);
      _irAlPrimerError(conError);
      return;
    }
    final huella = '${idDe(_cert.tipo)}|${_cert.numero}|${_cert.emision}|${_cert.vence}|${_cert.nombreArchivo}|${identityHashCode(_cert.archivo)}';
    if (huella != _ultimo) {
      _ultimo = huella;
      _idem = const Uuid().v4();
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      final r = await widget.guardar(context.read<Sincronizador>(), _cert, _idem!);
      if (mounted) Navigator.pop(context, r);
    } on SinConexion {
      if (mounted) setState(() => _error = 'Sin internet: el certificado no se guardó. Vuelve a intentarlo cuando tengas señal.');
    } on ErrorApi catch (e) {
      if (!mounted) return;
      final campos = <String, String>{};
      final lista = e.datos['errors'];
      if (lista is List) {
        for (final item in lista.whereType<Map>()) {
          final campo = campoCertificadoDeApi[item['field']];
          if (campo != null && item['message'] is String) campos.putIfAbsent(campo, () => item['message'] as String);
        }
      }
      setState(() {
        _errServidor
          ..clear()
          ..addAll(campos);
        _error = mensajeLote(e);
      });
      if (campos.isNotEmpty) {
        final marcados = form.validateGranularly();
        if (marcados.isNotEmpty) _irAlPrimerError(marcados);
      }
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final archivo = _cert.archivo;
    return Scaffold(
      appBar: AppBar(title: Text(_editando ? 'Corregir certificado' : 'Subir certificado')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            Tarjeta(
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                if (_editando)
                  InputDecorator(
                    decoration: const InputDecoration(labelText: 'Certificado', helperText: 'El tipo y el emisor no cambian.'),
                    child: Text(nombreDe(_cert.tipo)),
                  )
                else
                DropdownButtonFormField<String>(
                  initialValue: idDe(_cert.tipo),
                  isExpanded: true,
                  validator: (_) => _err('tipo'),
                  decoration: InputDecoration(
                    labelText: 'Certificado *',
                    helperText: widget.catalogo.isEmpty ? 'No hay certificados en el catálogo para tu perfil.' : null,
                  ),
                  items: [
                    for (final t in widget.catalogo)
                      DropdownMenuItem(value: t['id'] as String, child: Text(nombreDe(t), overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setState(() {
                    _cert.tipo = widget.catalogo.where((t) => t['id'] == v).firstOrNull;
                    _errServidor.remove('tipo');
                  }),
                ),
                const SizedBox(height: 10),
                TextFormField(
                  controller: _numero,
                  maxLength: 64,
                  validator: (_) => _err('numero'),
                  onChanged: (v) => setState(() {
                    _cert.numero = v;
                    _errServidor.remove('numero');
                  }),
                  decoration: const InputDecoration(labelText: 'Número del certificado (opcional)'),
                ),
                const SizedBox(height: 10),
                _CampoFecha(
                  etiqueta: 'Fecha de emisión (opcional)',
                  valor: _cert.emision,
                  error: () => _err('emision'),
                  alPulsar: () => _elegirFecha(false),
                  alQuitar: () => setState(() => _cert.emision = null),
                ),
                const SizedBox(height: 10),
                _CampoFecha(
                  etiqueta: _cert.exigeVencimiento ? 'Fecha de vencimiento *' : 'Fecha de vencimiento (opcional)',
                  valor: _cert.vence,
                  error: () => _err('vence'),
                  alPulsar: () => _elegirFecha(true),
                  alQuitar: () => setState(() => _cert.vence = null),
                ),
              ]),
            ),
            const SizedBox(height: 12),
            FormField<Object>(
              validator: (_) => _err('archivo'),
              builder: (estado) => Tarjeta(
                colorBorde: estado.hasError ? c.peligroTexto : null,
                child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Text(_editando ? 'Archivo' : 'Archivo *', style: TextStyle(fontWeight: FontWeight.w700, color: c.titulo)),
                  const SizedBox(height: 4),
                  Text(
                    archivo == null
                        ? (_editando
                            ? 'Se conserva el archivo actual. Elige otro solo si quieres reemplazarlo (PDF, JPG o PNG de hasta 5 MB).'
                            : 'PDF, JPG o PNG de hasta 5 MB. Si es una foto, que se lea bien.')
                        : '${_cert.nombreArchivo} · ${numero(archivo.length / 1024)} KB',
                    style: TextStyle(fontSize: 13, color: c.tintaSuave),
                  ),
                  if (estado.hasError) ...[
                    const SizedBox(height: 6),
                    Text(estado.errorText!, style: TextStyle(fontSize: 12.5, color: c.peligroTexto)),
                  ],
                  const SizedBox(height: 10),
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    OutlinedButton.icon(
                      style: _enLinea,
                      onPressed: _ocupado ? null : () => _elegirArchivo('pdf'),
                      icon: const Icon(Icons.picture_as_pdf_outlined),
                      label: const Text('PDF'),
                    ),
                    OutlinedButton.icon(
                      style: _enLinea,
                      onPressed: _ocupado ? null : () => _elegirArchivo('camara'),
                      icon: const Icon(Icons.photo_camera_outlined),
                      label: const Text('Tomar foto'),
                    ),
                    OutlinedButton.icon(
                      style: _enLinea,
                      onPressed: _ocupado ? null : () => _elegirArchivo('galeria'),
                      icon: const Icon(Icons.photo_library_outlined),
                      label: const Text('Galería'),
                    ),
                  ]),
                ]),
              ),
            ),
            const SizedBox(height: 12),
            const CajaAviso(
              tipo: TipoAviso.info,
              icono: Icons.fact_check_outlined,
              texto: 'El certificado queda en revisión: la plataforma lo aprueba antes de que cuente para tus lotes y los compradores.',
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              CajaError(texto: _error!),
            ],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _ocupado ? null : _guardar,
                icon: _ocupado
                    ? SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: c.tintaSuave))
                    : const Icon(Icons.upload_outlined),
                label: Text(_ocupado ? 'Guardando…' : (_editando ? 'Guardar cambios' : 'Subir')),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Fecha opcional como campo del formulario (con su error).
class _CampoFecha extends StatelessWidget {
  final String etiqueta;
  final DateTime? valor;
  final String? Function() error;
  final VoidCallback alPulsar;
  final VoidCallback alQuitar;
  const _CampoFecha({required this.etiqueta, required this.valor, required this.error, required this.alPulsar, required this.alQuitar});

  @override
  Widget build(BuildContext context) => FormField<DateTime>(
        validator: (_) => error(),
        builder: (estado) => InkWell(
          onTap: alPulsar,
          borderRadius: BorderRadius.circular(12),
          child: InputDecorator(
            decoration: InputDecoration(
              labelText: etiqueta,
              errorText: estado.errorText,
              errorMaxLines: 3,
              suffixIcon: valor == null
                  ? const Icon(Icons.event_outlined)
                  : IconButton(tooltip: 'Quitar la fecha', icon: const Icon(Icons.close), onPressed: alQuitar),
            ),
            child: Text(valor == null ? 'Sin fecha' : fechaCorta(valor!)),
          ),
        ),
      );
}
