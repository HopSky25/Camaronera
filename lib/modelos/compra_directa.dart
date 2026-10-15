/// Compra directa (C5): qué lotes se compran así y la validación de la
/// cantidad antes de pedir la huella. Funciones puras (se prueban sin teléfono).
library;

import '../pantallas/comunes/formato.dart';
import 'cuerpos.dart';

/// El lote se compra directo (sin verificación en campo): larva, nauplio y
/// lotes publicados sin verificación obligatoria. Los que la exigen siguen
/// con "Comprar con verificación".
bool esCompraDirecta(Map<String, dynamic> lote) => lote['requires_verification'] != true;

/// Lo que falla en la cantidad escrita (nulo si vale): mayor que cero, no
/// más que lo disponible y con los decimales que admite la unidad (unidades
/// enteras, millares hasta 3 decimales, libras hasta 2).
String? validarCantidadCompra(String? texto, Map<String, dynamic> lote) {
  final qty = numeroEscrito(texto);
  final uom = lote['uom'];
  if (qty == null || qty <= 0) return 'Escribe la cantidad a comprar (en ${unidadPlural(uom)}).';
  final disponible = lote['available_qty'] is num ? (lote['available_qty'] as num).toDouble() : 0.0;
  if (disponible <= 0) return 'Este lote ya no tiene disponible.';
  if (qty > disponible + 1e-9) return 'Solo hay ${cantidad(disponible, uom)} disponibles.';
  final decimales = decimalesDeUnidad(uom);
  final escala = _potencia(decimales);
  if (((qty * escala) - (qty * escala).roundToDouble()).abs() > 1e-6) {
    return decimales == 0
        ? 'En ${unidadPlural(uom)} la cantidad tiene que ser un número entero.'
        : 'En ${unidadPlural(uom)} se admiten hasta $decimales decimales.';
  }
  return null;
}

int _potencia(int n) {
  var r = 1;
  for (var i = 0; i < n; i++) {
    r *= 10;
  }
  return r;
}

/// Total estimado (precio de catálogo × cantidad); el definitivo lo da el
/// servidor (puede haber precio especial para el comprador).
double? totalEstimado(Map<String, dynamic> lote, double qty) {
  final precio = lote['price'];
  if (precio is! Map || precio['amount'] is! num) return null;
  return (precio['amount'] as num).toDouble() * qty;
}
