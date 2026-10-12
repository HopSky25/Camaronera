import 'package:flutter/material.dart';

import '../../tema.dart';

/// Cabecera con el degradado de la marca, como la pantalla de entrada.
class CabeceraSeguridad extends StatelessWidget {
  final IconData icono;
  final String titulo;
  final String texto;
  const CabeceraSeguridad({super.key, required this.icono, required this.titulo, required this.texto});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(24, 64, 24, 28),
      decoration: const BoxDecoration(gradient: Marca.degradado),
      child: Column(children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(18)),
          child: Icon(icono, color: Colors.white, size: 36),
        ),
        const SizedBox(height: 16),
        Text(titulo, textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w800)),
        const SizedBox(height: 6),
        Text(texto, textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white.withValues(alpha: 0.88), fontSize: 14)),
      ]),
    );
  }
}

/// Una línea con icono y texto (explicaciones cortas).
class PuntoSeguridad extends StatelessWidget {
  final IconData icono;
  final String texto;
  const PuntoSeguridad({super.key, required this.icono, required this.texto});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icono, color: Marca.brand, size: 22),
          const SizedBox(width: 12),
          Expanded(child: Text(texto, style: const TextStyle(fontSize: 14.5, color: Marca.tinta, height: 1.35))),
        ]),
      );
}

/// Texto que se repite en "Protege tu cuenta" y en "Seguridad".
const textoHuellaNoSale =
    'Tu huella nunca sale del teléfono. CamaronMarket no la ve ni la guarda: el teléfono solo comprueba que eres tú '
    'y firma la operación con una clave que vive en su chip de seguridad y no se puede copiar.';
