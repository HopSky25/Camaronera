import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../datos/api_cliente.dart';
import '../../datos/cuenta_vendedor.dart';
import '../../datos/sesion.dart';
import '../../datos/sincronizador.dart';
import '../../modelos/gestion_lote.dart';
import '../../modelos/lote_nuevo.dart';
import '../../tema.dart';
import '../comunes/formato.dart';
import '../comunes/widgets.dart';
import 'formulario.dart';
import 'pantalla_certificados_lote.dart';
import 'pantalla_fotos_lote.dart';

/// «Mis certificados» de la cuenta (`/me/certificates`), como la sección
/// Certificados de Mi cuenta del portal: cada uno con su revisión, subir uno
/// nuevo (PDF o imagen, con sus datos), renovarlo y quitarlo. Los aprobados
/// y vigentes se pueden ligar a los lotes. No se firman: la plataforma los
/// revisa antes de que cuenten.
class PantallaMisCertificados extends StatefulWidget {
  final ElegirImagenes elegirImagen;
  final ComprimirImagen comprimir;
  final ElegirPdf elegirPdf;
  const PantallaMisCertificados({
    super.key,
    this.elegirImagen = elegirConImagePicker,
    this.comprimir = comprimirEnSegundoPlano,
    this.elegirPdf = elegirPdfConFilePicker,
  });
  @override
  State<PantallaMisCertificados> createState() => _PantallaMisCertificadosState();
}

class _PantallaMisCertificadosState extends State<PantallaMisCertificados> {
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
      final d = await context.read<Sincronizador>().misCertificados();
      if (mounted) setState(() => _datos = d);
    } catch (e) {
      if (mounted) setState(() => _error = e is ErrorApi ? mensajeLote(e) : mensajeDeError(e));
    }
  }

  List<Map<String, dynamic>> _lista(String clave) {
    final l = _datos?[clave];
    return l is List ? [for (final x in l.whereType<Map>()) Map<String, dynamic>.from(x)] : [];
  }

  Future<void> _formulario({Map<String, dynamic>? linea}) async {
    final catalogo = _lista('catalog');
    final tipo = linea == null ? null : catalogo.where((t) => t['id'] == idDe(linea['certificate'])).firstOrNull;
    final r = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => PantallaSubirCertificado(
          catalogo: catalogo,
          inicial: linea == null ? null : CertificadoNuevo.desde(linea, tipo: tipo),
          guardar: linea == null
              ? (s, c, idem) => s.subirMiCertificado(c, idempotencia: idem)
              : (s, c, _) => s.renovarMiCertificado('${linea['id']}', c),
          elegirImagen: widget.elegirImagen,
          comprimir: widget.comprimir,
          elegirPdf: widget.elegirPdf,
        ),
      ),
    );
    if (r != null && mounted) {
      setState(() => _datos = r);
      avisar(context, linea == null
          ? 'Certificado subido: queda en revisión hasta que la plataforma lo apruebe.'
          : 'Certificado renovado: vuelve a revisión.');
    }
  }

  Future<void> _quitar(Map<String, dynamic> linea) async {
    final enLotes = linea['products'] is int && (linea['products'] as int) > 0;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Quitar el certificado'),
        content: Text('«${nombreDe(linea['certificate'])}» sale de tu cuenta.'
            '${enLotes ? ' Los lotes que ya lo tienen conservan su copia.' : ''}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(style: EstilosBoton.peligroRelleno(c), onPressed: () => Navigator.pop(c, true), child: const Text('Quitar')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final d = await context.read<Sincronizador>().quitarMiCertificado('${linea['id']}');
      if (mounted) setState(() => _datos = d);
      if (mounted) avisar(context, 'Certificado quitado de tu cuenta.');
    } on SinConexion {
      if (mounted) avisar(context, 'Sin internet: no se quitó. Inténtalo cuando tengas señal.', error: true);
    } catch (e) {
      if (mounted) avisar(context, e is ErrorApi ? mensajeLote(e) : mensajeDeError(e), error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final soloLectura = context.select<Sesion, bool>((s) => s.soloLectura);
    final c = context.colores;
    final lineas = _lista('data');
    return Scaffold(
      appBar: AppBar(title: const Text('Mis certificados')),
      body: _datos == null && _error == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              color: c.marcaTexto,
              onRefresh: _cargar,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                children: [
                  if (_error != null) ...[const SizedBox(height: 12), CajaError(texto: _error!, reintentar: _cargar)],
                  if (soloLectura) ...[const SizedBox(height: 12), const AvisoSoloLectura()],
                  const SizedBox(height: 8),
                  Text('Los certificados de tu cuenta (BAP, ASC, permisos…). Cuando la plataforma los aprueba, '
                      'y mientras estén vigentes, los agregas a cualquiera de tus lotes.',
                      style: TextStyle(color: c.tintaSuave)),
                  const Rotulo('En tu cuenta'),
                  if (lineas.isEmpty)
                    Text('Todavía no subiste certificados.', style: TextStyle(color: c.tintaSuave))
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
                                Text(
                                    [
                                      if (l['issuer'] is String) l['issuer'] as String,
                                      if (l['number'] is String) 'N.º ${l['number']}',
                                      l['expiry_date'] is String ? 'vence ${fecha(l['expiry_date'])}' : 'sin vencimiento',
                                    ].join(' · '),
                                    style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                                if (l['products'] is int && (l['products'] as int) > 0)
                                  Text(l['products'] == 1 ? 'En 1 lote' : 'En ${l['products']} lotes',
                                      style: TextStyle(fontSize: 13, color: c.tintaSuave)),
                                const SizedBox(height: 6),
                                Wrap(spacing: 6, runSpacing: 4, children: [
                                  Insignia(
                                    estado: '${l['status']}',
                                    texto: estadoCertificado(l['status'] as String?),
                                    tipo: switch (l['status']) {
                                      'approved' => TipoPildora.ok,
                                      'rejected' => TipoPildora.peligro,
                                      _ => TipoPildora.advertencia,
                                    },
                                  ),
                                  if (l['valid'] == false) const Insignia(estado: 'vencido', texto: 'Vencido', tipo: TipoPildora.peligro),
                                ]),
                              ]),
                            ),
                            if (!soloLectura) ...[
                              IconButton(
                                tooltip: 'Renovar o corregir',
                                onPressed: _ocupado ? null : () => _formulario(linea: l),
                                icon: Icon(Icons.autorenew, color: c.tintaSuave),
                              ),
                              IconButton(
                                tooltip: 'Quitar de mi cuenta',
                                onPressed: _ocupado ? null : () => _quitar(l),
                                icon: Icon(Icons.delete_outline, color: c.peligroTexto),
                              ),
                            ],
                          ]),
                        ),
                      ),
                  if (!soloLectura) ...[
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: _ocupado || _datos == null ? null : () => _formulario(),
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
