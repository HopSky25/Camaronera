import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';

import '../../datos/api_cliente.dart';
import '../../datos/lotes_vendedor.dart';
import '../../datos/repositorio.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/cuerpos.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/lote_nuevo.dart';
import '../../modelos/operacion_pendiente.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/lista_remota.dart';
import '../comunes/widgets.dart';
import 'pantalla_certificados_lote.dart';
import 'pantalla_fotos_lote.dart';

/// Catálogos del formulario (`/public/catalogs/...`, `/facilities`,
/// `/ponds`). Pasan por el [Repositorio]: sin señal se usa la última copia.
Future<List<ResultadoLista>> cargarCatalogosLote(Repositorio repo) => Future.wait([
      repo.todo('/public/catalogs/stages'),
      repo.todo('/public/catalogs/species'),
      repo.todo('/public/catalogs/genetics-lines'),
      repo.todo('/public/catalogs/size-grades'),
      repo.todo('/public/catalogs/uoms'),
      repo.todoOVacio('/facilities'),
      repo.todoOVacio('/ponds'),
    ]);

/// «Publicar lote», paso 1: la ficha del lote, con los campos que tocan
/// según el perfil (naupliero y laboratorio: nauplio y larva; camaronera:
/// camarón). «Guardar borrador» lo crea en borrador (`POST /products`, sin
/// firma) y abre el resumen para publicarlo con la huella. Devuelve `true`
/// si se creó el lote (para recargar la lista que la abrió).
///
/// Con [producto] es «Editar lote» (borrador o publicado), con las reglas
/// del portal: los campos bloqueados por compras no se tocan, y en un lote
/// publicado el precio, la cantidad y la unidad se guardan con la huella
/// (`POST /products/{id}:update-terms`); lo demás va por `PATCH` sin firma.
/// Devuelve el lote actualizado si se guardó.
class PantallaPublicarLote extends StatefulWidget {
  /// Lote propio a editar (`GET /products/{id}`); nulo = publicar uno nuevo.
  final Map<String, dynamic>? producto;
  const PantallaPublicarLote({super.key, this.producto});
  @override
  State<PantallaPublicarLote> createState() => _PantallaPublicarLoteState();
}

class _PantallaPublicarLoteState extends State<PantallaPublicarLote> {
  final _form = GlobalKey<FormState>();
  var _lote = LoteNuevo();

  /// Al editar: el cuerpo del lote tal como llegó (para mandar solo lo que
  /// cambió) y los campos del formulario bloqueados por compras.
  Map<String, dynamic>? _original;
  late final Set<String> _bloqueados = {
    for (final campo in camposBloqueados(widget.producto ?? const {})) ?campoDelFormulario(campo),
  };

  /// Cambio comercial ya firmado que no llegó (sin señal): «Guardar cambios»
  /// manda la MISMA operación si el cuerpo no cambió.
  OperacionPendiente? _firmada;

  bool get _editando => widget.producto != null;
  bool _b(String campo) => _bloqueados.contains(campo);
  final _nombre = TextEditingController();
  final _codigo = TextEditingController();
  final _supervivencia = TextEditingController();
  final _tamano = TextEditingController();
  final _sanidad = TextEditingController();
  final _cantidad = TextEditingController();
  final _precio = TextEditingController();

  /// Errores por campo que devolvió el servidor (422); se borran al editar.
  final _errServidor = <String, String>{};
  bool _ocupado = false;
  String? _error;

  /// Clave de idempotencia del último cuerpo enviado: si se cortó la señal
  /// y se reintenta lo mismo, no se crea un segundo borrador.
  String? _idem;
  String? _ultimoCuerpo;

  /// Catálogo de etapas (para saber en el resumen si el lote es engorde).
  List<Map<String, dynamic>> _estadios = const [];

  @override
  void dispose() {
    for (final c in [_nombre, _codigo, _supervivencia, _tamano, _sanidad, _cantidad, _precio]) {
      c.dispose();
    }
    super.dispose();
  }

  String? _err(String campo, String? perfil) => _lote.errores(perfil)[campo] ?? _errServidor[campo];

  void _cambio(String campo, VoidCallback aplicar) {
    setState(() {
      aplicar();
      _errServidor.remove(campo);
    });
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));
  }

  /// Lleva la vista al campo con error que está más arriba.
  void _irAlPrimerError(Set<FormFieldState<Object?>> campos) {
    double alto(FormFieldState<Object?> c) {
      final r = c.context.findRenderObject();
      return r is RenderBox && r.attached ? r.localToGlobal(Offset.zero).dy : double.infinity;
    }

    final primero = campos.reduce((a, b) => alto(a) <= alto(b) ? a : b);
    if (!primero.mounted) return;
    Scrollable.ensureVisible(primero.context, duration: const Duration(milliseconds: 350), alignment: 0.2);
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
    if (_editando) {
      await _guardarCambios(form);
      return;
    }
    final cuerpo = _lote.aCuerpoApi();
    final texto = jsonEncode(cuerpo);
    if (texto != _ultimoCuerpo) {
      _ultimoCuerpo = texto;
      _idem = const Uuid().v4();
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    final sinc = context.read<Sincronizador>();
    try {
      final p = await sinc.crearBorradorLote(cuerpo, idempotencia: _idem!);
      if (!mounted) return;
      await Navigator.push(context,
          MaterialPageRoute(builder: (_) => PantallaConfirmarPublicacion(p: p, recienCreado: true, estadios: _estadios)));
      // El borrador ya existe: el formulario no se vuelve a usar (otro
      // «Guardar» crearía un segundo lote).
      if (mounted) Navigator.pop(context, true);
    } on SinConexion {
      if (mounted) setState(() => _error = 'Sin internet: el lote no se guardó. Revisa la señal y vuelve a pulsar «Guardar borrador».');
    } on ErrorApi catch (e) {
      if (!mounted) return;
      final campos = erroresDeCampo(e);
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

  /// Texto de un cambio comercial para confirmarlo («Precio: … → …»).
  String _describirCambio(String campo, Map<String, dynamic> p) {
    final uom = p['uom'];
    return switch (campo) {
      'price' => 'Precio: ${precioPorUnidad(p['price'], uom)} → ${precioPorUnidad({'amount': numeroEscrito(_lote.precio), 'currency': 'USD'}, _lote.unidad)}',
      'initial_qty' => 'Cantidad: ${cantidad(p['initial_qty'], uom)} → ${cantidad(numeroEscrito(_lote.cantidad), _lote.unidad)}',
      'uom' => 'Unidad: ${nombreDe(uom)} → ${nombreDe(_lote.unidad)}',
      _ => campo,
    };
  }

  Future<void> _guardarCambios(FormState form) async {
    final p = widget.producto!;
    final cambios = cambiosLote(_original ?? const {}, _lote.aCuerpoApi())
      ..removeWhere((campo, _) => _b(campoDelFormulario(campo) ?? ''));
    if (cambios.isEmpty) {
      _aviso('No cambiaste nada.');
      return;
    }
    final firmada = edicionVaFirmada(p, cambios);
    final texto = jsonEncode(cambios);
    if (firmada && _firmada?.cuerpoSerializado != texto) {
      _firmada = null;
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Confirmar el cambio'),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('El lote está publicado: los compradores verán el cambio enseguida.'),
            const SizedBox(height: 10),
            for (final campo in comercialesCambiados(cambios))
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(_describirCambio(campo, p), style: const TextStyle(fontWeight: FontWeight.w700)),
              ),
            const SizedBox(height: 6),
            const Text('Se te pedirá tu huella o el PIN del teléfono.'),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
            FilledButton.icon(onPressed: () => Navigator.pop(c, true), icon: const Icon(Icons.fingerprint), label: const Text('Confirmar')),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    } else if (!firmada && texto != _ultimoCuerpo) {
      _ultimoCuerpo = texto;
      _idem = const Uuid().v4();
    }
    setState(() {
      _ocupado = true;
      _error = null;
    });
    final sinc = context.read<Sincronizador>();
    final id = '${p['id']}';
    OperacionPendiente? op = _firmada;
    try {
      final Map<String, dynamic> r;
      if (firmada) {
        op ??= await sinc.firmarEdicionLote(id, cambios);
        r = await sinc.enviarFirmadaAhora(op);
      } else {
        r = await sinc.editarLote(id, cambios, idempotencia: _idem!);
      }
      _firmada = null;
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Cambios guardados.')));
      Navigator.pop(context, r);
    } on ErrorSeguridad catch (e) {
      if (mounted && e.cancelado) {
        _aviso(e.mensaje);
      } else if (mounted) {
        setState(() => _error = e.mensaje);
      }
    } on SinConexion {
      if (mounted) {
        setState(() {
          _firmada = op;
          _error = 'Sin internet: los cambios NO se guardaron. Cuando tengas señal vuelve a pulsar «Guardar cambios»'
              '${op != null ? ' (ya confirmado con tu huella: no se duplica)' : ''}.';
        });
      }
    } on ErrorApi catch (e) {
      if (!mounted) return;
      final campos = erroresDeCampo(e);
      setState(() {
        _firmada = e.esDefinitivo ? null : op;
        _errServidor
          ..clear()
          ..addAll(campos);
        _error = 'No se guardó. ${mensajeLote(e)}';
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

  /// Al editar, el formulario se llena una vez con el lote (con los
  /// catálogos ya cargados para la etapa y la unidad).
  void _prellenar(List<List<Map<String, dynamic>>> cat) {
    if (!_editando || _original != null) return;
    _lote = LoteNuevo.desdeProducto(widget.producto!, estadios: cat[0], unidades: cat[4]);
    _nombre.text = _lote.nombre;
    _codigo.text = _lote.codigoLote;
    _supervivencia.text = _lote.supervivencia;
    _tamano.text = _lote.tamanoMg;
    _sanidad.text = _lote.sanidad;
    _cantidad.text = _lote.cantidad;
    _precio.text = _lote.precio;
    _original = _lote.aCuerpoApi();
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final perfil = sesion.perfil;
    return Scaffold(
      appBar: AppBar(title: Text(_editando ? 'Editar lote' : 'Publicar lote')),
      body: sesion.soloLectura || !puedePublicarLotes(perfil)
          ? ListView(padding: const EdgeInsets.all(16), children: [
              if (sesion.soloLectura)
                const AvisoSoloLectura()
              else
                const AvisoWeb(texto: 'Tu perfil no publica lotes. Publican los nauplieros, los laboratorios y las camaroneras.'),
            ])
          : CargaRemota<List<ResultadoLista>>(
              cargar: cargarCatalogosLote,
              hijo: (c, datos, _, _, _) => _formulario(c, perfil, datos.map((r) => r.datos).toList()),
            ),
    );
  }

  Widget _formulario(BuildContext context, String? perfil, List<List<Map<String, dynamic>>> cat) {
    _prellenar(cat);
    final tipos = tiposQuePublica(perfil);
    _estadios = cat[0];
    final estadios = cat[0].where((s) => tipos.contains(tipoDeEstadio(s)) || s['id'] == _lote.etapaOriginal).toList()
      ..sort((a, b) => ((a['sequence'] as num?) ?? 0).compareTo((b['sequence'] as num?) ?? 0));
    final original = _lote.etapaOriginal;
    if (original != null && _lote.estadio != null && !estadios.any((s) => s['id'] == original) && idDe(_lote.estadio) == original) {
      estadios.add(_lote.estadio!);
    }
    final especies = cat[1];
    final geneticas = cat[2];
    final tallas = cat[3].where((t) => t['presentation'] == _lote.presentacion).toList();
    final unidades = cat[4];
    final instalaciones = cat[5].where(instalacionDeOrigen).toList();
    final piscinas = cat[6].where((p) => idDe(p['facility']) == _lote.instalacionId && _lote.instalacionId != null).toList();
    _lote.unidad ??= unidadPorDefecto(perfil, unidades);
    final esTanque = perfil != 'camaronera';
    final camaron = _lote.esCamaron;
    final engorde = exigeTalla(_lote.estadio);
    final por = porUnidad(_lote.unidad);
    final qty = numeroEscrito(_cantidad.text);
    final precio = numeroEscrito(_precio.text);
    final c = context.colores;
    final publicado = _editando && widget.producto!['state'] != 'draft';
    final motivo = _editando ? motivoBloqueo(widget.producto!) : null;
    const bloqueado = 'Bloqueado: el lote ya tiene compras.';
    final provinciasLote = [...provincias, if (_lote.provincia != null && !provincias.contains(_lote.provincia)) _lote.provincia!];

    return Form(
      key: _form,
      autovalidateMode: AutovalidateMode.onUserInteraction,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(
                !_editando
                    ? 'Completa la ficha del lote. Lo guardamos como borrador y después lo publicas con tu huella.'
                    : publicado
                        ? 'El lote está publicado: los cambios de precio, cantidad o unidad se confirman con tu huella.'
                        : 'Cambia lo que necesites y guarda el borrador.',
                style: TextStyle(color: c.tintaSuave)),
          ),
          if (_bloqueados.isNotEmpty) ...[
            const SizedBox(height: 12),
            CajaAviso(
              tipo: TipoAviso.advertencia,
              icono: Icons.lock_outline,
              texto: 'Precio y datos del lote bloqueados: el lote ${motivo ?? 'ya tiene compras'}.',
            ),
          ],
          // ---------------- 1. Información básica ----------------
          const Rotulo('Información básica'),
          _Seccion(children: [
            TextFormField(
              controller: _nombre,
              maxLength: 200,
              textCapitalization: TextCapitalization.sentences,
              validator: (_) => _err('nombre', perfil),
              onChanged: (v) => _cambio('nombre', () => _lote.nombre = v),
              decoration: InputDecoration(
                labelText: 'Nombre del producto *',
                hintText: perfil == 'camaronera' ? 'Ej. Camarón entero 30/40 - Piscina 3' : 'Ej. Nauplios L. vannamei - Corrida 032',
                helperText: 'Así lo verán los compradores.',
              ),
            ),
            const SizedBox(height: 10),
            TextFormField(
              controller: _codigo,
              maxLength: 64,
              onChanged: (v) => _lote.codigoLote = v,
              decoration: const InputDecoration(labelText: 'Lote del vendedor (opcional)', helperText: 'Tu propio código de lote.'),
            ),
            if (especies.isNotEmpty) ...[
              const SizedBox(height: 10),
              _Opciones(
                etiqueta: 'Especie (opcional)',
                opciones: [for (final e in especies) (e['id'] as String, nombreDe(e))],
                valor: _lote.especieId,
                bloqueado: _b('especie'),
                validator: () => _errServidor['especie'],
                alCambiar: (v) => _cambio('especie', () => _lote.especieId = v),
              ),
            ],
            const SizedBox(height: 10),
            _Opciones(
              etiqueta: 'Etapa *',
              ayuda: engorde
                  ? 'Engorde exige presentación y talla para publicar (se cruzan con las listas de precios).'
                  : (tipos.length > 1 ? 'Tu perfil publica nauplio y larva.' : 'Tu perfil publica camarón (juvenil y engorde).'),
              opciones: [for (final s in estadios) (s['id'] as String, nombreDe(s))],
              valor: idDe(_lote.estadio),
              validator: () => _err('estadio', perfil),
              vacio: 'No hay etapas disponibles para tu perfil.',
              obligatorio: true,
              bloqueado: _b('estadio'),
              alCambiar: (v) => _cambio('estadio', () {
                _lote.estadio = estadios.where((s) => s['id'] == v).firstOrNull;
                if (!_lote.esCamaron) {
                  _lote.presentacion = null;
                  _lote.tallaId = null;
                }
              }),
            ),
            if (geneticas.isNotEmpty) ...[
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                initialValue: _lote.geneticaId,
                isExpanded: true,
                validator: (_) => _errServidor['genetica'],
                decoration: InputDecoration(labelText: 'Línea genética (opcional)', helperText: _b('genetica') ? bloqueado : null),
                items: [
                  const DropdownMenuItem(value: null, child: Text('Sin indicar')),
                  for (final g in geneticas) DropdownMenuItem(value: g['id'] as String, child: Text(nombreDe(g))),
                ],
                onChanged: _b('genetica') ? null : (v) => _cambio('genetica', () => _lote.geneticaId = v),
              ),
            ],
          ]),

          // ---------------- 2. Producción y calidad ----------------
          const Rotulo('Producción y calidad'),
          _Seccion(children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                child: TextFormField(
                  controller: _supervivencia,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  validator: (_) => _err('supervivencia', perfil),
                  onChanged: (v) => _cambio('supervivencia', () => _lote.supervivencia = v),
                  decoration: const InputDecoration(labelText: 'Supervivencia', suffixText: '%'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextFormField(
                  controller: _tamano,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  validator: (_) => _err('tamano', perfil),
                  onChanged: (v) => _cambio('tamano', () => _lote.tamanoMg = v),
                  decoration: const InputDecoration(labelText: 'Tamaño promedio', suffixText: 'mg'),
                ),
              ),
            ]),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              initialValue: _lote.provincia,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Ubicación (opcional)', helperText: 'Se muestra en la tarjeta del marketplace.'),
              items: [
                const DropdownMenuItem(value: null, child: Text('Sin indicar')),
                for (final p in provinciasLote) DropdownMenuItem(value: p, child: Text(p)),
              ],
              onChanged: (v) => setState(() => _lote.provincia = v),
            ),
            const SizedBox(height: 10),
            InkWell(
              onTap: _elegirFecha,
              borderRadius: BorderRadius.circular(12),
              child: InputDecorator(
                decoration: InputDecoration(
                  labelText: 'Fecha estimada de entrega (opcional)',
                  helperText: 'Cuándo estaría listo el lote para entregar.',
                  errorText: _errServidor['fechaEntrega'],
                  suffixIcon: _lote.fechaEntrega == null
                      ? const Icon(Icons.event_outlined)
                      : IconButton(
                          tooltip: 'Quitar la fecha',
                          icon: const Icon(Icons.close),
                          onPressed: () => setState(() => _lote.fechaEntrega = null),
                        ),
                ),
                child: Text(_lote.fechaEntrega == null ? 'Sin fecha' : fechaCorta(_lote.fechaEntrega!)),
              ),
            ),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              initialValue: _lote.instalacionId,
              isExpanded: true,
              validator: (_) => _errServidor['instalacion'],
              decoration: InputDecoration(
                labelText: 'Instalación de origen (opcional)',
                helperText: _b('instalacion')
                    ? bloqueado
                    : instalaciones.isEmpty
                        ? 'No tienes instalaciones registradas: agrégalas en «Instalaciones» del panel.'
                        : 'Las bodegas y oficinas no se ofrecen: no producen lotes.',
              ),
              items: [
                const DropdownMenuItem(value: null, child: Text('Sin instalación')),
                for (final f in instalaciones) DropdownMenuItem(value: f['id'] as String, child: Text(nombreDe(f))),
                if (_lote.instalacionId != null && !instalaciones.any((f) => f['id'] == _lote.instalacionId))
                  DropdownMenuItem(value: _lote.instalacionId, child: Text(nombreDe(widget.producto?['origin_facility']))),
              ],
              onChanged: _b('instalacion') ? null : (v) => _cambio('instalacion', () {
                _lote.instalacionId = v;
                _lote.piscinaId = null;
                _errServidor.remove('piscina');
              }),
            ),
            if (_lote.instalacionId != null) ...[
              const SizedBox(height: 10),
              DropdownButtonFormField<String>(
                key: ValueKey('piscina-${_lote.instalacionId}'),
                initialValue: _lote.piscinaId,
                isExpanded: true,
                validator: (_) => _errServidor['piscina'],
                decoration: InputDecoration(
                  labelText: '${esTanque ? 'Tanque' : 'Piscina'} de origen (opcional)',
                  helperText: _b('piscina')
                      ? bloqueado
                      : piscinas.isEmpty
                          ? 'Esta instalación todavía no tiene piscinas ni tanques registrados.'
                          : 'Solo aparecen los de la instalación elegida.',
                ),
                items: [
                  DropdownMenuItem(value: null, child: Text(esTanque ? 'Sin tanque' : 'Sin piscina')),
                  for (final p in piscinas) DropdownMenuItem(value: p['id'] as String, child: Text(nombreDe(p))),
                  if (_lote.piscinaId != null && !piscinas.any((p) => p['id'] == _lote.piscinaId))
                    DropdownMenuItem(value: _lote.piscinaId, child: Text(nombreDe(widget.producto?['origin_pond']))),
                ],
                onChanged: _b('piscina') ? null : (v) => _cambio('piscina', () => _lote.piscinaId = v),
              ),
            ],
            const SizedBox(height: 10),
            TextFormField(
              controller: _sanidad,
              maxLines: 3,
              minLines: 2,
              onChanged: (v) => _lote.sanidad = v,
              decoration: const InputDecoration(
                labelText: 'Estado sanitario / observaciones (opcional)',
                hintText: 'Ej. pruebas PCR, laboratorio, notas del lote…',
              ),
            ),
          ]),

          // ---------------- 3. Información comercial ----------------
          const Rotulo('Información comercial'),
          _Seccion(children: [
            if (camaron) ...[
              _Opciones(
                etiqueta: engorde ? 'Presentación *' : 'Presentación (opcional)',
                opciones: const [('entero', 'Entero'), ('cola', 'Cola')],
                valor: _lote.presentacion,
                obligatorio: engorde,
                bloqueado: _b('presentacion'),
                validator: () => _err('presentacion', perfil),
                alCambiar: (v) => _cambio('presentacion', () {
                  if (_lote.presentacion != v) _lote.tallaId = null;
                  _lote.presentacion = v;
                  _errServidor.remove('talla');
                }),
              ),
              const SizedBox(height: 10),
              _Opciones(
                etiqueta: engorde ? 'Talla *' : 'Talla (opcional)',
                opciones: [for (final t in tallas) (t['id'] as String, nombreDe(t))],
                valor: _lote.tallaId,
                obligatorio: engorde,
                bloqueado: _b('talla'),
                vacio: _lote.presentacion == null ? 'Elige primero la presentación.' : 'No hay tallas para esta presentación.',
                ayuda: 'Obligatorias para publicar camarón de engorde (en juvenil son opcionales).',
                validator: () => _err('talla', perfil),
                alCambiar: (v) => _cambio('talla', () => _lote.tallaId = v),
              ),
              const SizedBox(height: 10),
            ],
            if (unidades.isNotEmpty)
              _Opciones(
                etiqueta: 'Unidad',
                ayuda: 'Por defecto en millares para nauplieros y laboratorios, y en libras para camaroneras.',
                opciones: [for (final u in unidades) (u['id'] as String, nombreDe(u))],
                valor: idDe(_lote.unidad),
                obligatorio: true,
                bloqueado: _b('unidad'),
                validator: () => _errServidor['unidad'],
                alCambiar: (v) => _cambio('unidad', () => _lote.unidad = unidades.where((u) => u['id'] == v).firstOrNull),
              ),
            const SizedBox(height: 10),
            TextFormField(
              controller: _cantidad,
              enabled: !_b('cantidad'),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              validator: (_) => _err('cantidad', perfil),
              onChanged: (v) => _cambio('cantidad', () => _lote.cantidad = v),
              decoration: InputDecoration(
                labelText: _lote.unidad == null ? 'Cantidad disponible *' : 'Cantidad disponible en ${unidadPlural(_lote.unidad)} *',
                suffixText: unidadPlural(_lote.unidad),
                helperText: _b('cantidad') ? bloqueado : 'Debe ser mayor a 0.',
              ),
            ),
            const SizedBox(height: 10),
            TextFormField(
              controller: _precio,
              enabled: !_b('precio'),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              validator: (_) => _err('precio', perfil),
              onChanged: (v) => _cambio('precio', () => _lote.precio = v),
              decoration: InputDecoration(
                labelText: por.isEmpty ? 'Precio *' : 'Precio $por *',
                prefixText: r'USD $ ',
                suffixText: por,
                helperText: _b('precio') ? 'Bloqueado: el lote tiene compras en curso.' : null,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Total del lote: ${qty != null && qty > 0 && precio != null && precio >= 0 ? dinero({'amount': qty * precio, 'currency': 'USD'}) : '—'}',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ]),
          const SizedBox(height: 16),
          _AvisoVerificacion(perfil: perfil),
          if (!_editando) ...[
            const SizedBox(height: 12),
            const CajaAviso(
              tipo: TipoAviso.info,
              icono: Icons.photo_library_outlined,
              texto: 'Las fotos y los certificados del lote los agregas después de guardar el borrador, desde el resumen.',
            ),
          ],
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
                  ? SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: context.colores.tintaSuave))
                  : const Icon(Icons.save_outlined),
              label: Text(_ocupado ? 'Guardando…' : (_editando ? 'Guardar cambios' : 'Guardar borrador')),
            ),
          ),
          if (!_editando || publicado) ...[
            const SizedBox(height: 8),
            Text(
                _editando
                    ? 'Si cambias el precio, la cantidad o la unidad, se te pedirá tu huella o el PIN del teléfono.'
                    : 'Después revisas el resumen y lo publicas con tu huella.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: c.tintaSuave)),
          ],
        ],
      ),
    );
  }

  Future<void> _elegirFecha() async {
    final hoy = DateTime.now();
    final desde = DateTime(hoy.year, hoy.month, hoy.day);
    final hasta = desde.add(const Duration(days: 730));
    final f = await showDatePicker(
      context: context,
      initialDate: fechaEntre(_lote.fechaEntrega ?? desde, desde, hasta),
      firstDate: desde,
      lastDate: hasta,
    );
    if (f != null && mounted) setState(() => _lote.fechaEntrega = f);
  }
}

/// Sección del formulario en su tarjeta (`.pf-sec` del portal).
class _Seccion extends StatelessWidget {
  final List<Widget> children;
  const _Seccion({required this.children});
  @override
  Widget build(BuildContext context) => Tarjeta(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children),
      );
}

/// Opciones en fichas (etapa, presentación, talla, unidad) como campo del
/// formulario: muestra su error y la validación puede llevar la vista a él.
class _Opciones extends StatelessWidget {
  final String etiqueta;
  final List<(String, String)> opciones;
  final String? valor;
  final ValueChanged<String?> alCambiar;
  final String? Function()? validator;
  final String? ayuda;
  final String? vacio;

  /// Obligatorio: al tocar la ficha elegida no se desmarca.
  final bool obligatorio;

  /// Bloqueado por compras: se ve, pero no se cambia.
  final bool bloqueado;
  const _Opciones({
    required this.etiqueta,
    required this.opciones,
    required this.valor,
    required this.alCambiar,
    this.validator,
    this.ayuda,
    this.vacio,
    this.obligatorio = false,
    this.bloqueado = false,
  });

  @override
  Widget build(BuildContext context) => FormField<String>(
        validator: validator == null ? null : (_) => validator!(),
        builder: (estado) => InputDecorator(
          decoration: InputDecoration(
            labelText: etiqueta,
            helperText: bloqueado ? 'Bloqueado: el lote ya tiene compras.' : ayuda,
            helperMaxLines: 3,
            errorText: estado.errorText,
            errorMaxLines: 3,
            border: InputBorder.none,
            contentPadding: const EdgeInsets.only(top: 8, bottom: 4),
          ),
          child: opciones.isEmpty
              ? Text(vacio ?? 'Sin opciones.', style: TextStyle(color: context.colores.tintaSuave))
              : Wrap(spacing: 8, runSpacing: 4, children: [
                  for (final (id, nombre) in opciones)
                    ChoiceChip(
                      label: Text(nombre),
                      selected: valor == id,
                      onSelected: bloqueado
                          ? null
                          : (sel) {
                              if (!sel && obligatorio) return;
                              alCambiar(sel ? id : null);
                              estado.didChange(sel ? id : null);
                            },
                    ),
                ]),
        ),
      );
}

class _AvisoVerificacion extends StatelessWidget {
  final String? perfil;
  const _AvisoVerificacion({required this.perfil});
  @override
  Widget build(BuildContext context) => CajaAviso(
        tipo: perfil == 'camaronera' ? TipoAviso.advertencia : TipoAviso.info,
        icono: Icons.verified_user_outlined,
        texto: avisoVerificacion(perfil),
      );
}

/// «Publicar lote», pasos 2 y 3: el resumen del borrador y «Publicar con mi
/// huella» (`POST /products/{id}:publish`, firmado). Sin señal no se
/// publica: el lote queda en borrador y «Reintentar» manda la MISMA
/// operación firmada (misma Idempotency-Key: no se publica dos veces).
/// Desde aquí también se edita el borrador y se le agregan fotos y
/// certificados. Devuelve `true` si se publicó.
class PantallaConfirmarPublicacion extends StatefulWidget {
  final Map<String, dynamic> p;

  /// Viene del formulario: el borrador se acaba de guardar.
  final bool recienCreado;

  /// Catálogo de etapas, si se tiene (ver [faltantesParaPublicar]).
  final List<Map<String, dynamic>> estadios;
  const PantallaConfirmarPublicacion({super.key, required this.p, this.recienCreado = false, this.estadios = const []});
  @override
  State<PantallaConfirmarPublicacion> createState() => _PantallaConfirmarPublicacionState();
}

class _PantallaConfirmarPublicacionState extends State<PantallaConfirmarPublicacion> {
  bool _ocupado = false;
  String? _error;

  /// El borrador como está ahora (cambia al editarlo).
  late Map<String, dynamic> _p = widget.p;

  /// Publicación ya firmada que no llegó (sin señal o fallo del servidor).
  OperacionPendiente? _firmada;

  Future<void> _publicar() async {
    if (_ocupado) return;
    final sinc = context.read<Sincronizador>();
    setState(() {
      _ocupado = true;
      _error = null;
    });
    var op = _firmada;
    try {
      op ??= await sinc.firmarPublicacionLote('${_p['id']}');
      await sinc.enviarFirmadaAhora(op);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Lote publicado: ya está en el marketplace.')));
      Navigator.pop(context, true);
      return;
    } on ErrorSeguridad catch (e) {
      if (mounted && e.cancelado) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.mensaje)));
      } else if (mounted) {
        setState(() => _error = e.mensaje);
      }
    } on SinConexion {
      if (mounted) {
        setState(() {
          _firmada = op;
          _error = 'Sin internet: el lote NO se publicó y sigue guardado como borrador. Cuando tengas señal '
              'pulsa «Reintentar» (es la misma publicación ya confirmada: no se duplica).';
        });
      }
    } on ErrorApi catch (e) {
      if (mounted) {
        setState(() {
          // Un fallo pasajero (servidor, demasiados intentos) se reintenta con
          // la misma firma; un rechazo del dato o del permiso, no.
          _firmada = e.esDefinitivo ? null : op;
          _error = 'No se publicó; el lote sigue en borrador. ${mensajeLote(e)}';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  /// Tras volver de fotos o certificados: el borrador al día (sin señal se
  /// queda como estaba).
  Future<void> _recargar() async {
    try {
      final r = await context.read<Repositorio>().detalle('/products/${_p['id']}');
      if (mounted && r.datos.isNotEmpty) setState(() => _p = r.datos.first);
    } catch (_) {}
  }

  Future<void> _editar() async {
    final r = await Navigator.push<Map<String, dynamic>>(context, MaterialPageRoute(builder: (_) => PantallaPublicarLote(producto: _p)));
    if (r != null && mounted) {
      setState(() {
        _p = r;
        _firmada = null;
        _error = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _p;
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final faltan = faltantesParaPublicar(p, estadios: widget.estadios);
    final qty = p['available_qty'];
    final precio = p['price'];
    final total = qty is num && precio is Map && precio['amount'] is num
        ? dinero({'amount': qty * (precio['amount'] as num), 'currency': precio['currency']})
        : '—';
    final borrador = p['state'] == 'draft';
    return Scaffold(
      appBar: AppBar(title: const Text('Publicar lote')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          if (widget.recienCreado)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: CajaAviso(
                tipo: TipoAviso.ok,
                icono: Icons.check_circle_outline,
                peso: FontWeight.w700,
                texto: 'Borrador guardado. Revisa el resumen, agrega fotos y certificados, y publícalo con tu huella.',
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Row(children: [
              Expanded(child: Text('${p['name'] ?? ''}', style: Theme.of(context).textTheme.titleLarge)),
              Insignia(estado: '${p['state'] ?? ''}', texto: estadoProducto(p['state'] as String?)),
            ]),
          ),
          const Rotulo('Resumen'),
          SeccionDatos(filas: [
            ('Etapa', nombreDe(p['stage'])),
            if (p['species'] is Map) ('Especie', nombreDe(p['species'])),
            if (p['genetics_line'] is Map) ('Genética', nombreDe(p['genetics_line'])),
            if (p['presentation'] != null) ('Presentación', presentacion(p['presentation'] as String?)),
            if (p['size_grade'] is Map) ('Talla', nombreDe(p['size_grade'])),
            ('Cantidad', cantidad(qty, p['uom'])),
            ('Precio', precioPorUnidad(precio, p['uom'])),
            ('Total del lote', total),
            if (p['survival_rate'] != null) ('Supervivencia', porcentaje(p['survival_rate'])),
            if (p['avg_size_mg'] != null) ('Tamaño promedio', '${numero(p['avg_size_mg'])} mg'),
            if (p['location'] != null) ('Ubicación', '${p['location']}'),
            if (p['expected_delivery_date'] != null) ('Entrega estimada', fecha(p['expected_delivery_date'])),
            if (p['origin_facility'] is Map)
              ('Origen', [nombreDe(p['origin_facility']), if (p['origin_pond'] is Map) nombreDe(p['origin_pond'])].join(' · ')),
            if (p['batch_code'] != null) ('Lote del vendedor', '${p['batch_code']}'),
            ('Verificación en campo', p['requires_verification'] == true ? 'Obligatoria' : 'Opcional (si el comprador la pide)'),
          ]),
          if (!soloLectura && borrador && p['id'] != null) ...[
            const SizedBox(height: 12),
            BotonesGestionLote(p: p, alEditar: _editar, alVolver: _recargar),
          ],
          const SizedBox(height: 16),
          if (soloLectura)
            const AvisoSoloLectura()
          else if (faltan.isNotEmpty)
            CajaAviso(
              tipo: TipoAviso.advertencia,
              icono: Icons.edit_note_outlined,
              texto: 'Para publicarlo falta ${faltan.join(', ')}. Complétalo con «Editar».',
            )
          else ...[
            Text('Al publicarlo, los compradores lo ven en el marketplace. Se te pedirá tu huella o el PIN del teléfono.',
                style: TextStyle(color: context.colores.tintaSuave)),
            if (_error != null) ...[
              const SizedBox(height: 12),
              CajaError(texto: _error!),
            ],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _ocupado ? null : _publicar,
                icon: _ocupado
                    ? SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: context.colores.tintaSuave))
                    : Icon(_firmada == null ? Icons.fingerprint : Icons.refresh),
                label: Text(_ocupado ? 'Publicando…' : (_firmada == null ? 'Publicar con mi huella' : 'Reintentar')),
              ),
            ),
          ],
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: TextButton(
              onPressed: _ocupado ? null : () => Navigator.pop(context, false),
              child: Text(borrador ? 'Dejar en borrador' : 'Volver'),
            ),
          ),
        ],
      ),
    );
  }
}

/// «Editar», «Fotos» y «Certificados» de un lote propio.
class BotonesGestionLote extends StatelessWidget {
  final Map<String, dynamic> p;
  final Future<void> Function() alEditar;

  /// Tras volver de fotos o certificados (para recargar lo que se muestra).
  final Future<void> Function()? alVolver;
  const BotonesGestionLote({super.key, required this.p, required this.alEditar, this.alVolver});

  @override
  Widget build(BuildContext context) {
    final fotos = p['photos'] is List ? (p['photos'] as List).length : null;
    final certificados = p['certificate_lines'] is List ? (p['certificate_lines'] as List).length : null;
    Future<void> abrir(Widget pantalla) async {
      await Navigator.push(context, MaterialPageRoute(builder: (_) => pantalla));
      await alVolver?.call();
    }

    final nombre = '${p['name'] ?? ''}';
    final id = '${p['id']}';
    // Del ancho de su texto (el tema los hace de todo el ancho).
    final estilo = OutlinedButton.styleFrom(minimumSize: const Size(0, 48));
    return Wrap(spacing: 8, runSpacing: 8, children: [
      OutlinedButton.icon(style: estilo, onPressed: alEditar, icon: const Icon(Icons.edit_outlined), label: const Text('Editar')),
      OutlinedButton.icon(
        style: estilo,
        onPressed: () => abrir(PantallaFotosLote(productoId: id, nombre: nombre)),
        icon: const Icon(Icons.photo_library_outlined),
        label: Text(fotos == null ? 'Fotos' : 'Fotos ($fotos)'),
      ),
      OutlinedButton.icon(
        style: estilo,
        onPressed: () => abrir(PantallaCertificadosLote(productoId: id, nombre: nombre)),
        icon: const Icon(Icons.verified_outlined),
        label: Text(certificados == null ? 'Certificados' : 'Certificados ($certificados)'),
      ),
    ]);
  }
}

/// Estado del lote para mostrarlo: un lote retirado (archivado) se ve como
/// «Archivado» aunque por dentro esté en borrador.
(String, String) estadoDeLote(Map<String, dynamic> p) {
  if (p['active'] == false && p['state'] != 'cancel') return ('archived', 'Archivado');
  final s = p['state'] as String?;
  return (s ?? '', estadoProducto(s));
}

/// Acciones del vendedor en el detalle de su lote: publicar, editar, fotos
/// y certificados de un borrador (y eliminarlo); editar, fotos, certificados
/// y retirar del marketplace un lote publicado; reactivar con la huella uno
/// retirado. En modo supervisor, o en un perfil que no vende, no aparece
/// nada.
class AccionesLote extends StatefulWidget {
  final Map<String, dynamic> p;

  /// Tras publicar, editar, retirar o reactivar: recarga lo que muestra el lote.
  final Future<void> Function() alCambiar;

  /// Tras eliminar el borrador (el detalle ya no tiene qué mostrar).
  final VoidCallback? alEliminar;
  const AccionesLote({super.key, required this.p, required this.alCambiar, this.alEliminar});
  @override
  State<AccionesLote> createState() => _AccionesLoteState();
}

class _AccionesLoteState extends State<AccionesLote> {
  /// Reactivación ya firmada que no llegó: «Reintentar» manda la misma.
  OperacionPendiente? _reactivacion;

  Map<String, dynamic> get p => widget.p;

  void _avisar(ScaffoldMessengerState m, String texto, {Color? error}) =>
      m.showSnackBar(SnackBar(content: Text(texto), backgroundColor: error, duration: Duration(seconds: error != null ? 6 : 3)));

  Future<void> _retirar(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Retirar del marketplace'),
        content: Text('«${p['name']}» deja de verse en el marketplace y queda archivado. '
            'Las compras que ya están en curso no cambian. Para volver a ofrecerlo, reactívalo desde «Mis lotes › Archivados».'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Retirar')),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final mensajero = ScaffoldMessenger.of(context);
    final peligro = context.colores.peligro;
    try {
      await context.read<Sincronizador>().archivarLote('${p['id']}', idempotencia: const Uuid().v4());
      _avisar(mensajero, 'Lote retirado del marketplace.');
      await widget.alCambiar();
    } on SinConexion {
      _avisar(mensajero, 'Sin internet: el lote sigue publicado. Inténtalo cuando tengas señal.', error: peligro);
    } on ErrorApi catch (e) {
      _avisar(mensajero, 'No se retiró. ${mensajeLote(e)}', error: peligro);
    } catch (e) {
      _avisar(mensajero, mensajeDeError(e), error: peligro);
    }
  }

  Future<void> _reactivar(BuildContext context) async {
    final mensajero = ScaffoldMessenger.of(context);
    final peligro = context.colores.peligro;
    final sinc = context.read<Sincronizador>();
    var op = _reactivacion;
    try {
      op ??= await sinc.firmarReactivacionLote('${p['id']}');
      await sinc.enviarFirmadaAhora(op);
      _reactivacion = null;
      _avisar(mensajero, 'Lote reactivado: quedó en borrador. Publícalo cuando quieras volver a venderlo.');
      await widget.alCambiar();
    } on ErrorSeguridad catch (e) {
      _avisar(mensajero, e.mensaje, error: e.cancelado ? null : peligro);
    } on SinConexion {
      if (mounted) setState(() => _reactivacion = op);
      _avisar(mensajero, 'Sin internet: el lote sigue archivado. Pulsa «Reintentar» cuando tengas señal (no se te vuelve a pedir la huella).',
          error: peligro);
    } on ErrorApi catch (e) {
      if (mounted) setState(() => _reactivacion = e.esDefinitivo ? null : op);
      _avisar(mensajero, 'No se reactivó. ${mensajeLote(e)}', error: peligro);
    } catch (e) {
      _avisar(mensajero, mensajeDeError(e), error: peligro);
    }
  }

  Future<void> _eliminar(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Eliminar el borrador'),
        content: Text('«${p['name']}» se elimina: deja de verse en tus lotes y no se puede recuperar.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Eliminar')),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final mensajero = ScaffoldMessenger.of(context);
    final peligro = context.colores.peligro;
    try {
      await context.read<Sincronizador>().eliminarBorradorLote('${p['id']}');
      _avisar(mensajero, 'Borrador eliminado.');
      if (widget.alEliminar != null) {
        widget.alEliminar!();
      } else {
        await widget.alCambiar();
      }
    } on SinConexion {
      _avisar(mensajero, 'Sin internet: el borrador no se eliminó. Inténtalo cuando tengas señal.', error: peligro);
    } on ErrorApi catch (e) {
      _avisar(mensajero, 'No se eliminó. ${mensajeLote(e)}', error: peligro);
    } catch (e) {
      _avisar(mensajero, mensajeDeError(e), error: peligro);
    }
  }

  Future<void> _editar(BuildContext context) async {
    final r = await Navigator.push<Map<String, dynamic>>(context, MaterialPageRoute(builder: (_) => PantallaPublicarLote(producto: p)));
    if (r != null) await widget.alCambiar();
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    if (sesion.soloLectura || !puedePublicarLotes(sesion.perfil)) return const SizedBox.shrink();
    final estado = p['state'];
    final eliminar = SizedBox(
      width: double.infinity,
      child: BotonAccion(
        relleno: false,
        icono: Icons.delete_outline,
        texto: 'Eliminar borrador',
        textoOcupado: 'Eliminando…',
        estilo: EstilosBoton.peligro(context),
        onPressed: () => _eliminar(context),
      ),
    );
    if (p['active'] == false) {
      if (p['can_reactivate'] != true && estado == 'cancel') return const SizedBox.shrink();
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('Lote retirado del marketplace. Al reactivarlo vuelve en borrador; para venderlo, publícalo otra vez.',
            style: TextStyle(color: context.colores.tintaSuave)),
        const SizedBox(height: 12),
        BotonAccion(
          icono: _reactivacion == null ? Icons.fingerprint : Icons.refresh,
          texto: _reactivacion == null ? 'Reactivar con mi huella' : 'Reintentar',
          textoOcupado: 'Reactivando…',
          onPressed: () => _reactivar(context),
        ),
        if (estado == 'draft') ...[const SizedBox(height: 10), eliminar],
      ]);
    }
    if (estado == 'cancel') return const SizedBox.shrink();
    final gestion = BotonesGestionLote(p: p, alEditar: () => _editar(context), alVolver: widget.alCambiar);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (estado == 'draft') ...[
        FilledButton.icon(
          icon: const Icon(Icons.publish_outlined),
          label: const Text('Publicar'),
          onPressed: () async {
            final r = await Navigator.push<bool>(context, MaterialPageRoute(builder: (_) => PantallaConfirmarPublicacion(p: p)));
            if (r == true) await widget.alCambiar();
          },
        ),
        const SizedBox(height: 10),
      ],
      gestion,
      if (estado == 'draft') ...[const SizedBox(height: 10), eliminar],
      if (estado == 'published') ...[
        const SizedBox(height: 10),
        BotonAccion(
          relleno: false,
          icono: Icons.unpublished_outlined,
          texto: 'Retirar del marketplace',
          textoOcupado: 'Retirando…',
          estilo: EstilosBoton.peligro(context),
          onPressed: () => _retirar(context),
        ),
      ],
    ]);
  }
}
