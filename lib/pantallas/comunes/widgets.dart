import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../datos/sincronizador.dart';
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
                  SizedBox(width: 130, child: Text(filas[i].$1, style: const TextStyle(fontSize: 13, color: Marca.tintaSuave))),
                  Expanded(
                    child: Text(filas[i].$2, textAlign: TextAlign.right,
                        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Marca.tinta)),
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
  final Color color;
  const TarjetaResumen({
    super.key,
    required this.icono,
    required this.valor,
    required this.titulo,
    this.detalle,
    this.onTap,
    this.color = Marca.brand,
  });

  @override
  Widget build(BuildContext context) {
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
            if (onTap != null) const Icon(Icons.chevron_right, size: 18, color: Marca.gris),
          ]),
          const SizedBox(height: 8),
          Text(valor, style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800, color: color, height: 1.1)),
          const SizedBox(height: 4),
          Text(titulo, maxLines: 2, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Marca.navy)),
          if (detalle != null)
            Text(detalle!, maxLines: 2, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: Marca.tintaSuave)),
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
              decoration: BoxDecoration(color: Marca.tealClaro, borderRadius: BorderRadius.circular(12)),
              child: Icon(icono, color: Marca.brand),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(titulo, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: Marca.navy)),
                if (subtitulo != null) Text(subtitulo!, style: const TextStyle(fontSize: 12.5, color: Marca.tintaSuave)),
              ]),
            ),
            const Icon(Icons.chevron_right, color: Marca.gris),
          ]),
        ),
      );
}

/// Lo que la API no permite hacer desde la app.
class AvisoWeb extends StatelessWidget {
  final String texto;
  const AvisoWeb({super.key, this.texto = 'Esto se hace desde el portal web.'});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: Marca.fondoSuave, borderRadius: BorderRadius.circular(12)),
        child: Row(children: [
          const Icon(Icons.language, size: 18, color: Marca.tintaSuave),
          const SizedBox(width: 10),
          Expanded(child: Text(texto, style: const TextStyle(fontSize: 13, color: Marca.tintaSuave))),
        ]),
      );
}

/// "Datos de la última sincronización": se muestra cuando no hay señal.
class AvisoCache extends StatelessWidget {
  final DateTime? guardadoEn;
  const AvisoCache({super.key, this.guardadoEn});
  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        color: const Color(0xFFFDF1E0),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Row(children: [
          const Icon(Icons.cloud_off_outlined, size: 18, color: Marca.ambar),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              guardadoEn == null
                  ? 'Sin internet. Datos de la última sincronización.'
                  : 'Sin internet. Datos de la última sincronización (${DateFormat('dd/MM HH:mm').format(guardadoEn!)}).',
              style: const TextStyle(fontSize: 13, color: Marca.ambar, fontWeight: FontWeight.w600),
            ),
          ),
        ]),
      );
}

class CajaError extends StatelessWidget {
  final String texto;
  final VoidCallback? reintentar;
  const CajaError({super.key, required this.texto, this.reintentar});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: const Color(0xFFFDEAEA), borderRadius: BorderRadius.circular(12)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.error_outline, color: Marca.rojo),
            const SizedBox(width: 10),
            Expanded(child: Text(texto, style: const TextStyle(color: Marca.rojo))),
          ]),
          if (reintentar != null)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(onPressed: reintentar, child: const Text('Reintentar')),
            ),
        ]),
      );
}

class VacioLista extends StatelessWidget {
  final String texto;
  final IconData icono;
  const VacioLista({super.key, required this.texto, this.icono = Icons.inbox_outlined});
  @override
  Widget build(BuildContext context) => ListView(children: [
        const SizedBox(height: 90),
        Icon(icono, size: 56, color: Marca.gris.withValues(alpha: 0.6)),
        const SizedBox(height: 12),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(texto, textAlign: TextAlign.center, style: const TextStyle(color: Marca.tintaSuave)),
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
    return Container(
      width: double.infinity,
      color: sinRed ? const Color(0xFFFDF1E0) : Marca.tealClaro,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      child: Row(children: [
        Icon(sinRed ? Icons.cloud_off_outlined : Icons.cloud_done_outlined, size: 18, color: sinRed ? Marca.ambar : Marca.brand),
        const SizedBox(width: 8),
        Expanded(
          child: Text(texto, style: TextStyle(fontSize: 13, color: sinRed ? Marca.ambar : Marca.brand, fontWeight: FontWeight.w600)),
        ),
        if (sinc.pendientes > 0)
          Text('${sinc.pendientes} por enviar', style: const TextStyle(fontSize: 12.5, color: Marca.ambar, fontWeight: FontWeight.w700)),
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
          Icon(icono, size: 16, color: Marca.gris),
          const SizedBox(width: 8),
          Expanded(
            child: Text(texto, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 13.5, color: color ?? Marca.tintaSuave)),
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
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: Marca.navy)),
              ),
              const SizedBox(width: 8),
              Insignia(estado: estado, texto: estadoTexto),
            ]),
            if (subtitulo != null) ...[
              const SizedBox(height: 6),
              Text(subtitulo!, maxLines: 2, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Marca.tinta)),
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
      : r.error != null
          ? 'El servidor no lo aceptó: ${r.error}'
          : 'Guardado en el teléfono; se enviará cuando haya internet.';
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Text(texto),
    backgroundColor: r.error != null ? Marca.rojo : null,
    duration: Duration(seconds: r.error != null ? 6 : 3),
  ));
}
