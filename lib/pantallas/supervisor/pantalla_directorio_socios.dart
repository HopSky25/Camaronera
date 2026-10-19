import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/sesion.dart';
import '../../modelos/perfil.dart';
import '../../modelos/supervisor.dart';
import '../../seguridad/firmador.dart';
import '../../tema.dart';
import '../comunes/widgets.dart';
import '../seguridad/pantalla_seguridad.dart';
import 'franja_supervisor.dart';

/// Modo supervisor: "Elige un socio". Directorio de socios aprobados
/// (`GET /supervisor/partners`) con buscador, filtro por tipo y paginación.
/// Al elegir uno se confirma con la huella (act-as firmado) y se abre su
/// app en solo lectura.
class PantallaDirectorioSocios extends StatefulWidget {
  const PantallaDirectorioSocios({super.key});
  @override
  State<PantallaDirectorioSocios> createState() => _PantallaDirectorioSociosState();
}

class _PantallaDirectorioSociosState extends State<PantallaDirectorioSocios> {
  final _buscar = TextEditingController();
  Timer? _espera;
  String? _tipo;
  List<SocioDirectorio> _socios = [];
  String? _cursor;
  bool _cargando = true;
  bool _cargandoMas = false;
  String? _error;

  /// Socio que se está abriendo (huella + act-as).
  String? _abriendo;

  /// Una respuesta de una búsqueda anterior que llega tarde se ignora.
  int _generacion = 0;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  @override
  void dispose() {
    _espera?.cancel();
    _buscar.dispose();
    super.dispose();
  }

  Future<void> _cargar() async {
    final g = ++_generacion;
    setState(() {
      _cargando = true;
      _error = null;
    });
    try {
      final p = await context.read<Sesion>().buscarSocios(texto: _buscar.text, tipo: _tipo);
      if (!mounted || g != _generacion) return;
      setState(() {
        _socios = _alfabetico(p.socios);
        _cursor = p.siguienteCursor;
      });
    } catch (e) {
      if (!mounted || g != _generacion) return;
      setState(() => _error = mensajeDeError(e));
    } finally {
      if (mounted && g == _generacion) setState(() => _cargando = false);
    }
  }

  Future<void> _cargarMas() async {
    final cursor = _cursor;
    if (cursor == null || _cargandoMas) return;
    final g = _generacion;
    setState(() => _cargandoMas = true);
    try {
      final p = await context.read<Sesion>().buscarSocios(texto: _buscar.text, tipo: _tipo, cursor: cursor);
      if (!mounted || g != _generacion) return;
      setState(() {
        _socios = _alfabetico([..._socios, ...p.socios]);
        _cursor = p.siguienteCursor;
      });
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(mensajeDeError(e))));
    } finally {
      if (mounted) setState(() => _cargandoMas = false);
    }
  }

  /// El servidor pagina por fecha de modificación: lo cargado se muestra en
  /// orden alfabético (sin repetir si un socio cambió entre páginas).
  static List<SocioDirectorio> _alfabetico(List<SocioDirectorio> lista) {
    final porId = <String, SocioDirectorio>{for (final s in lista) s.id: s};
    return porId.values.toList()..sort((a, b) => a.nombre.toLowerCase().compareTo(b.nombre.toLowerCase()));
  }

  void _alEscribir(String _) {
    _espera?.cancel();
    _espera = Timer(const Duration(milliseconds: 400), _cargar);
    setState(() {}); // botón de borrar
  }

  void _elegirTipo(String? tipo) {
    if (tipo == _tipo) return;
    setState(() => _tipo = tipo);
    _cargar();
  }

  Future<void> _abrir(SocioDirectorio socio, {bool conPin = false}) async {
    if (_abriendo != null) return;
    setState(() => _abriendo = socio.id);
    try {
      await context.read<Sesion>().actuarComo(socio, conPin: conPin);
    } on ErrorSeguridad catch (e) {
      if (mounted && !e.cancelado) _aviso(e.mensaje, error: true);
    } catch (e) {
      if (mounted) _aviso(mensajeDeError(e), error: true);
    } finally {
      if (mounted) setState(() => _abriendo = null);
    }
  }

  void _aviso(String texto, {bool error = false}) => ScaffoldMessenger.of(context)
      .showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    final aviso = sesion.avisoDirectorio;
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: const LogoMarca(),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.account_circle_outlined),
            onSelected: (v) async {
              if (v == 'seguridad') {
                await Navigator.push(context, MaterialPageRoute(builder: (_) => const PantallaSeguridad()));
              } else if (v == 'salir') {
                await confirmarSalirSupervisor(context);
              }
            },
            itemBuilder: (_) => [
              PopupMenuItem(
                enabled: false,
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                  Text(sesion.yoPropio?.nombre ?? 'Supervisor', style: TextStyle(fontWeight: FontWeight.w700, color: context.colores.titulo)),
                  Text('Supervisor de la app', style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave)),
                ]),
              ),
              const PopupMenuDivider(),
              const PopupMenuItem(value: 'seguridad', child: ListTile(leading: Icon(Icons.shield_outlined), title: Text('Seguridad'))),
              const PopupMenuItem(value: 'salir', child: ListTile(leading: Icon(Icons.logout), title: Text('Salir del modo supervisor'))),
            ],
          ),
        ],
      ),
      body: Column(children: [
        _Cabecera(nombre: sesion.yoPropio?.nombre),
        if (aviso != null)
          CajaAviso(
            tipo: TipoAviso.advertencia,
            icono: Icons.timer_off_outlined,
            franja: true,
            tamTexto: 13,
            peso: FontWeight.w600,
            texto: aviso,
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
          child: TextField(
            controller: _buscar,
            onChanged: _alEscribir,
            onSubmitted: (_) {
              _espera?.cancel();
              _cargar();
            },
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              hintText: 'Buscar socio por nombre',
              prefixIcon: const Icon(Icons.search),
              suffixIcon: _buscar.text.isEmpty
                  ? null
                  : IconButton(
                      tooltip: 'Borrar',
                      icon: const Icon(Icons.close),
                      onPressed: () {
                        _buscar.clear();
                        _espera?.cancel();
                        _cargar();
                      },
                    ),
            ),
          ),
        ),
        SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            children: [
              _Chip(etiqueta: 'Todos', elegido: _tipo == null, onTap: () => _elegirTipo(null)),
              for (final t in tiposDeSocio)
                _Chip(etiqueta: nombreDePerfil(t), icono: iconoDePerfil(t), elegido: _tipo == t, onTap: () => _elegirTipo(t)),
            ],
          ),
        ),
        Expanded(child: RefreshIndicator(color: context.colores.marcaTexto, onRefresh: _cargar, child: _lista())),
      ]),
    );
  }

  Widget _lista() {
    if (_cargando && _socios.isEmpty) return const Center(child: CircularProgressIndicator());
    if (_error != null && _socios.isEmpty) {
      return ListView(padding: const EdgeInsets.all(16), children: [CajaError(texto: _error!, reintentar: _cargar)]);
    }
    if (_socios.isEmpty) {
      return VacioLista(
        texto: _buscar.text.trim().isEmpty && _tipo == null
            ? 'No hay socios aprobados con usuario del portal.'
            : 'Ningún socio coincide con la búsqueda.',
        icono: Icons.search_off,
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 24),
      itemCount: _socios.length + 1,
      separatorBuilder: (_, _) => const SizedBox(height: 10),
      itemBuilder: (c, i) {
        if (i == _socios.length) {
          return Column(children: [
            if (_cursor != null)
              Center(
                child: _cargandoMas
                    ? const Padding(padding: EdgeInsets.all(12), child: CircularProgressIndicator())
                    : TextButton(onPressed: _cargarMas, child: const Text('Cargar más')),
              ),
            Padding(
              padding: EdgeInsets.fromLTRB(8, 8, 8, 0),
              child: Text(
                'Cada vez que abres la app de un socio lo confirmas con tu huella y queda anotado en su historial. '
                'Solo puedes mirar: ninguna acción se hace en su nombre.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave),
              ),
            ),
          ]);
        }
        final s = _socios[i];
        return _TarjetaSocio(
          socio: s,
          abriendo: _abriendo == s.id,
          onTap: _abriendo == null ? () => _abrir(s) : null,
          onPin: _abriendo == null ? () => _abrir(s, conPin: true) : null,
        );
      },
    );
  }
}

class _Cabecera extends StatelessWidget {
  final String? nombre;
  const _Cabecera({this.nombre});

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        decoration: const BoxDecoration(gradient: ColoresMarca.degradado),
        child: Row(children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(12)),
            child: const Icon(Icons.admin_panel_settings_outlined, color: Colors.white),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              const Text('Elige un socio', style: TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w800)),
              Text(
                '${nombre == null || nombre!.isEmpty ? '' : '$nombre · '}modo supervisor, solo lectura',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 13),
              ),
            ]),
          ),
        ]),
      );
}

class _Chip extends StatelessWidget {
  final String etiqueta;
  final IconData? icono;
  final bool elegido;
  final VoidCallback onTap;
  const _Chip({required this.etiqueta, this.icono, required this.elegido, required this.onTap});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(right: 8),
        child: ChoiceChip(
          label: Text(etiqueta),
          avatar: icono == null ? null : Icon(icono, size: 16, color: elegido ? Colors.white : context.colores.marcaTexto),
          selected: elegido,
          showCheckmark: false,
          onSelected: (_) => onTap(),
          selectedColor: context.colores.marca,
          backgroundColor: context.colores.superficie,
          side: BorderSide(color: elegido ? context.colores.marca : context.colores.borde),
          labelStyle: TextStyle(color: elegido ? Colors.white : context.colores.tintaSuave, fontWeight: FontWeight.w600, fontSize: 13),
        ),
      );
}

class _TarjetaSocio extends StatelessWidget {
  final SocioDirectorio socio;
  final bool abriendo;
  final VoidCallback? onTap;
  final VoidCallback? onPin;
  const _TarjetaSocio({required this.socio, required this.abriendo, this.onTap, this.onPin});

  @override
  Widget build(BuildContext context) {
    final lugar = socio.lugar;
    return Tarjeta(
      padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
      onTap: onTap,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(color: context.colores.marcaSuave, borderRadius: BorderRadius.circular(12)),
          child: Icon(iconoDePerfil(socio.tipo ?? ''), color: context.colores.marcaTexto),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(socio.nombre, maxLines: 2, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: context.colores.titulo)),
            const SizedBox(height: 2),
            Text(lugar.isEmpty ? socio.tipoEtiqueta : '${socio.tipoEtiqueta} · $lugar',
                maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 13, color: context.colores.tintaSuave)),
            if (socio.perfiles.isNotEmpty) ...[
              const SizedBox(height: 8),
              Wrap(spacing: 6, runSpacing: 6, children: [
                for (final p in socio.perfiles)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: p == socio.tipo ? context.colores.marcaSuave : context.colores.fondoAlt,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(nombreDePerfil(p),
                        style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: p == socio.tipo ? context.colores.marcaTexto : context.colores.tintaSuave)),
                  ),
              ]),
            ],
          ]),
        ),
        if (abriendo)
          const Padding(
            padding: EdgeInsets.all(12),
            child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2)),
          )
        else
          PopupMenuButton<String>(
            tooltip: 'Ver su app',
            icon: Icon(Icons.more_vert, color: context.colores.tintaTenue),
            enabled: onTap != null,
            onSelected: (v) => v == 'pin' ? onPin?.call() : onTap?.call(),
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'huella', child: ListTile(leading: Icon(Icons.fingerprint), title: Text('Ver su app'))),
              PopupMenuItem(value: 'pin', child: ListTile(leading: Icon(Icons.pin_outlined), title: Text('Ver su app con el PIN'))),
            ],
          ),
      ]),
    );
  }
}
