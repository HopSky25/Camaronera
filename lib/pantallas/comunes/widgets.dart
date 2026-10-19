import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../datos/sincronizador.dart';
import '../../modelos/supervisor.dart';
import '../../tema.dart';

/// Tarjeta con filas etiqueta → valor, como en el detalle de la verificación.
class SeccionDatos extends StatelessWidget {
  final List<(String, String)> filas;
  const SeccionDatos({super.key, required this.filas});

  @override
  Widget build(BuildContext context) {
    return Tarjeta(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Column(
        children: [
          for (var i = 0; i < filas.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 10),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(width: 130, child: Text(filas[i].$1, style: TextStyle(fontSize: 13, color: context.colores.tintaSuave))),
                  Expanded(
                    child: Text(filas[i].$2, textAlign: TextAlign.right,
                        style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: context.colores.tinta)),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Tarjeta grande del panel: un número y qué es, con un toque para ir a la lista.
class TarjetaResumen extends StatelessWidget {
  final IconData icono;
  final String valor;
  final String titulo;
  final String? detalle;
  final VoidCallback? onTap;

  /// Color del número y del icono (texto legible); por defecto, el de marca.
  final Color? color;
  const TarjetaResumen({
    super.key,
    required this.icono,
    required this.valor,
    required this.titulo,
    this.detalle,
    this.onTap,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final color = this.color ?? context.colores.marcaTexto;
    return Tarjeta(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 12),
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(children: [
            Icon(icono, size: 20, color: color),
            const Spacer(),
            if (onTap != null) Icon(Icons.chevron_right, size: 18, color: context.colores.tintaTenue),
          ]),
          const SizedBox(height: 8),
          Text(valor, style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800, color: color, height: 1.1)),
          const SizedBox(height: 4),
          Text(titulo, maxLines: 2, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: context.colores.titulo)),
          if (detalle != null)
            Text(detalle!, maxLines: 2, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, color: context.colores.tintaSuave)),
        ],
      ),
    );
  }
}

/// Rejilla de tarjetas resumen (dos por fila).
class RejillaResumen extends StatelessWidget {
  final List<Widget> tarjetas;
  const RejillaResumen({super.key, required this.tarjetas});
  @override
  Widget build(BuildContext context) => GridView.count(
        crossAxisCount: 2,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 1.15,
        children: tarjetas,
      );
}

/// Entrada de un menú del panel ("Mis lotes", "Mis ventas"...).
class FilaMenu extends StatelessWidget {
  final IconData icono;
  final String titulo;
  final String? subtitulo;
  final VoidCallback onTap;
  const FilaMenu({super.key, required this.icono, required this.titulo, this.subtitulo, required this.onTap});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Tarjeta(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          onTap: onTap,
          child: Row(children: [
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(color: context.colores.marcaSuave, borderRadius: BorderRadius.circular(12)),
              child: Icon(icono, color: context.colores.marcaTexto),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(titulo, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: context.colores.titulo)),
                if (subtitulo != null) Text(subtitulo!, style: TextStyle(fontSize: 12.5, color: context.colores.tintaSuave)),
              ]),
            ),
            Icon(Icons.chevron_right, color: context.colores.tintaTenue),
          ]),
        ),
      );
}

/// Lo que la API no permite hacer desde la app.
class AvisoWeb extends StatelessWidget {
  final String texto;
  const AvisoWeb({super.key, this.texto = 'Esto se hace desde el portal web.'});
  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: c.fondoAlt,
        borderRadius: BorderRadius.circular(Radios.campo),
        border: Border.all(color: c.borde),
      ),
      child: Row(children: [
        Icon(Icons.language, size: 18, color: c.tintaSuave),
        const SizedBox(width: 10),
        Expanded(child: Text(texto, style: TextStyle(fontSize: 13, color: c.tintaSuave))),
      ]),
    );
  }
}

/// Reemplaza a las acciones de escritura en modo supervisor.
class AvisoSoloLectura extends StatelessWidget {
  const AvisoSoloLectura({super.key});
  @override
  Widget build(BuildContext context) => const CajaAviso(
        tipo: TipoAviso.advertencia,
        icono: Icons.visibility_outlined,
        texto: textoSoloLectura,
        peso: FontWeight.w700,
      );
}

/// "Datos de la última sincronización": se muestra cuando no hay señal.
class AvisoCache extends StatelessWidget {
  final DateTime? guardadoEn;
  const AvisoCache({super.key, this.guardadoEn});
  @override
  Widget build(BuildContext context) => CajaAviso(
        tipo: TipoAviso.advertencia,
        icono: Icons.cloud_off_outlined,
        franja: true,
        tamTexto: 13,
        peso: FontWeight.w600,
        texto: guardadoEn == null
            ? 'Sin internet. Datos de la última sincronización.'
            : 'Sin internet. Datos de la última sincronización (${DateFormat('dd/MM HH:mm').format(guardadoEn!)}).',
      );
}

class CajaError extends StatelessWidget {
  final String texto;
  final VoidCallback? reintentar;
  const CajaError({super.key, required this.texto, this.reintentar});
  @override
  Widget build(BuildContext context) {
    final e = context.colores.aviso(TipoAviso.peligro);
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: e.fondo,
        borderRadius: BorderRadius.circular(Radios.campo),
        border: Border.all(color: e.borde),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.error_outline, color: e.texto),
          const SizedBox(width: 10),
          Expanded(child: Text(texto, style: TextStyle(color: e.texto))),
        ]),
        if (reintentar != null)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              style: TextButton.styleFrom(foregroundColor: e.texto),
              onPressed: reintentar,
              child: const Text('Reintentar'),
            ),
          ),
      ]),
    );
  }
}

class VacioLista extends StatelessWidget {
  final String texto;
  final IconData icono;
  const VacioLista({super.key, required this.texto, this.icono = Icons.inbox_outlined});
  @override
  Widget build(BuildContext context) => ListView(children: [
        const SizedBox(height: 90),
        Icon(icono, size: 56, color: context.colores.tintaTenue.withValues(alpha: 0.6)),
        const SizedBox(height: 12),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(texto, textAlign: TextAlign.center, style: TextStyle(color: context.colores.tintaSuave)),
          ),
        ),
      ]);
}

/// Franja con el estado de la sincronización (sin internet / sincronizado a las...).
class BarraEstado extends StatelessWidget {
  final Sincronizador sinc;
  const BarraEstado({super.key, required this.sinc});

  @override
  Widget build(BuildContext context) {
    final sinRed = !sinc.hayRed;
    final texto = sinRed
        ? 'Sin internet. Lo que captures queda guardado en el teléfono.'
        : sinc.ultimoMensaje ??
            (sinc.ultimaSincronizacion == null
                ? 'Todavía no se ha sincronizado'
                : 'Sincronizado el ${DateFormat('dd/MM').format(sinc.ultimaSincronizacion!)} a las ${DateFormat('HH:mm').format(sinc.ultimaSincronizacion!)}');
    final e = context.colores.aviso(sinRed ? TipoAviso.advertencia : TipoAviso.info);
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(color: e.fondo, border: Border(bottom: BorderSide(color: e.borde))),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      child: Row(children: [
        Icon(sinRed ? Icons.cloud_off_outlined : Icons.cloud_done_outlined, size: 18, color: e.texto),
        const SizedBox(width: 8),
        Expanded(
          child: Text(texto, style: TextStyle(fontSize: 13, color: e.texto, fontWeight: FontWeight.w600)),
        ),
        if (sinc.pendientes > 0)
          Text('${sinc.pendientes} por enviar',
              style: TextStyle(fontSize: 12.5, color: context.colores.aviso(TipoAviso.advertencia).texto, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// Fila icono + texto de las tarjetas de lista.
class Dato extends StatelessWidget {
  final IconData icono;
  final String texto;
  final Color? color;
  const Dato({super.key, required this.icono, required this.texto, this.color});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(children: [
          Icon(icono, size: 16, color: context.colores.tintaTenue),
          const SizedBox(width: 8),
          Expanded(
            child: Text(texto, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 13.5, color: color ?? context.colores.tintaSuave)),
          ),
        ]),
      );
}

/// Tarjeta de lista con referencia, insignia de estado y filas de datos.
class TarjetaRegistro extends StatelessWidget {
  final String titulo;
  final String? subtitulo;
  final String estado;
  final String estadoTexto;
  final List<Widget> datos;
  final VoidCallback? onTap;
  const TarjetaRegistro({
    super.key,
    required this.titulo,
    this.subtitulo,
    required this.estado,
    required this.estadoTexto,
    this.datos = const [],
    this.onTap,
  });

  @override
  Widget build(BuildContext context) => Tarjeta(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Expanded(
                child: Text(titulo, maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: context.colores.titulo)),
              ),
              const SizedBox(width: 8),
              Insignia(estado: estado, texto: estadoTexto),
            ]),
            if (subtitulo != null) ...[
              const SizedBox(height: 6),
              Text(subtitulo!, maxLines: 2, overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: context.colores.tinta)),
            ],
            if (datos.isNotEmpty) const SizedBox(height: 8),
            ...datos,
          ],
        ),
      );
}

/// Botón que se bloquea mientras corre la acción: evita el doble envío al
/// pulsar dos veces seguidas. Con `relleno` es un FilledButton; si no, un
/// OutlinedButton.
class BotonAccion extends StatefulWidget {
  final Future<void> Function()? onPressed;
  final IconData icono;
  final String texto;
  final String? textoOcupado;
  final bool relleno;
  final ButtonStyle? estilo;
  const BotonAccion({
    super.key,
    required this.onPressed,
    required this.icono,
    required this.texto,
    this.textoOcupado,
    this.relleno = true,
    this.estilo,
  });

  @override
  State<BotonAccion> createState() => _BotonAccionState();
}

class _BotonAccionState extends State<BotonAccion> {
  bool _ocupado = false;

  Future<void> _pulsar() async {
    if (_ocupado) return;
    setState(() => _ocupado = true);
    try {
      await widget.onPressed?.call();
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final icono = _ocupado
        ? SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2, color: widget.relleno ? Colors.white : null),
          )
        : Icon(widget.icono);
    final etiqueta = Text(_ocupado ? (widget.textoOcupado ?? widget.texto) : widget.texto);
    final onPressed = widget.onPressed == null || _ocupado ? null : _pulsar;
    return widget.relleno
        ? FilledButton.icon(style: widget.estilo, onPressed: onPressed, icon: icono, label: etiqueta)
        : OutlinedButton.icon(style: widget.estilo, onPressed: onPressed, icon: icono, label: etiqueta);
  }
}

/// Avisa del resultado de una acción encolada, con el texto que corresponde.
void avisarResultado(BuildContext context, ResultadoEnvio r, {required String hecho}) {
  final texto = r.enviado
      ? hecho
      : r.sinConfirmar || r.soloLectura
          ? r.error!
          : r.error != null
              ? 'El servidor no lo aceptó: ${r.error}'
              : 'Guardado en el teléfono; se enviará cuando haya internet.';
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Text(texto),
    backgroundColor: r.error != null ? context.colores.peligro : null,
    duration: Duration(seconds: r.error != null ? 6 : 3),
  ));
}
