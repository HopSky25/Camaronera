import 'package:flutter/material.dart';

/// Colores y estilos tomados del sitio web de CamaronMarket
/// (shrimp_base.css, verification.css, shrimp_panel.css) para que la app se
/// vea de la misma familia que el portal.
class Marca {
  static const navy = Color(0xFF123E5C); // --sp-navy / texto de títulos
  static const navyOscuro = Color(0xFF0B2F47); // --sl-navy
  static const teal = Color(0xFF14A0B0); // --shrimp-teal
  static const brand = Color(0xFF0D7C88); // --shrimp-brand (teal oscuro)
  static const tinta = Color(0xFF0D1B26); // --sl-ink
  static const tintaSuave = Color(0xFF55636D); // --sl-ink-soft
  static const gris = Color(0xFF8B9BA3);
  static const linea = Color(0x1A000000); // --sp-line rgba(0,0,0,.10)
  static const fondo = Color(0xFFF7FAFB);
  static const fondoSuave = Color(0xFFEEF2F4);
  static const tealClaro = Color(0xFFEAF7F8);
  static const verde = Color(0xFF217A48);
  static const rojo = Color(0xFFB3261E);
  static const ambar = Color(0xFFA86A10);

  static const degradado = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF0F3A4D), Color(0xFF14A0B0)],
  );

  /// Insignia por estado, idéntica a `.sv-badge--*` del sitio de verificadores.
  static (Color fondo, Color texto) insignia(String estado) => switch (estado) {
        'received' => (const Color(0xFFEEF1F4), const Color(0xFF46545E)),
        'assigned' => (const Color(0xFFE6F6F8), const Color(0xFF0D7C88)),
        'in_field' => (const Color(0xFFFDF1E0), const Color(0xFFA86A10)),
        'done' => (const Color(0xFFE9F0FB), const Color(0xFF2A5DB0)),
        'approved' || 'approved_obs' => (const Color(0xFFE6F6EC), const Color(0xFF217A48)),
        'rejected' => (const Color(0xFFFDEAEA), const Color(0xFFB3261E)),
        'cancelled' || 'cancel' || 'withdrawn' || 'archived' => (const Color(0xFFF2F3F5), const Color(0xFF6B7780)),
        // Compras y ventas
        'pending_verification' || 'pending_acceptance' || 'waiting' || 'open' => (const Color(0xFFFDF1E0), const Color(0xFFA86A10)),
        'confirmed' || 'published' || 'sent' || 'accepted' || 'closed' || 'sold' => (const Color(0xFFE6F6EC), const Color(0xFF217A48)),
        'draft' => (const Color(0xFFEEF1F4), const Color(0xFF46545E)),
        'broken' || 'disputed' => (const Color(0xFFFDEAEA), const Color(0xFFB3261E)),
        // Despacho
        'sin_cita' => (const Color(0xFFEEF1F4), const Color(0xFF46545E)),
        'programado' => (const Color(0xFFE6F6F8), const Color(0xFF0D7C88)),
        'en_ruta' => (const Color(0xFFFDF1E0), const Color(0xFFA86A10)),
        'llegado' => (const Color(0xFFE6F6EC), const Color(0xFF217A48)),
        // Maquila
        'packed' || 'signed' => (const Color(0xFFE9F0FB), const Color(0xFF2A5DB0)),
        _ => (const Color(0xFFEEF1F4), const Color(0xFF46545E)),
      };
}

ThemeData temaCamaronMarket() {
  const fuente = 'Inter';
  final base = ThemeData(
    useMaterial3: true,
    fontFamily: fuente,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Marca.brand,
      primary: Marca.brand,
      secondary: Marca.teal,
      surface: Colors.white,
      error: Marca.rojo,
    ),
    scaffoldBackgroundColor: Marca.fondo,
  );
  return base.copyWith(
    textTheme: base.textTheme.apply(bodyColor: Marca.tinta, displayColor: Marca.navy).copyWith(
          titleLarge: const TextStyle(fontFamily: fuente, fontSize: 20, fontWeight: FontWeight.w800, color: Marca.navy),
          titleMedium: const TextStyle(fontFamily: fuente, fontSize: 16, fontWeight: FontWeight.w700, color: Marca.navy),
          titleSmall: const TextStyle(fontFamily: fuente, fontSize: 13, fontWeight: FontWeight.w700, color: Marca.tintaSuave, letterSpacing: 0.6),
          bodyMedium: const TextStyle(fontFamily: fuente, fontSize: 15, color: Marca.tinta, height: 1.35),
          bodySmall: const TextStyle(fontFamily: fuente, fontSize: 13, color: Marca.tintaSuave, height: 1.3),
          labelLarge: const TextStyle(fontFamily: fuente, fontSize: 15, fontWeight: FontWeight.w700),
        ),
    appBarTheme: const AppBarTheme(
      backgroundColor: Colors.white,
      foregroundColor: Marca.navy,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0.5,
      shadowColor: Marca.linea,
      centerTitle: false,
      titleTextStyle: TextStyle(fontFamily: fuente, fontSize: 18, fontWeight: FontWeight.w800, color: Marca.navy),
    ),
    cardTheme: const CardThemeData(
      color: Colors.white,
      elevation: 0,
      surfaceTintColor: Colors.transparent,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(16)), side: BorderSide(color: Marca.linea)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Marca.linea)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Color(0xFFCFD8DC))),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Marca.teal, width: 2)),
      labelStyle: const TextStyle(fontFamily: fuente, color: Marca.tintaSuave),
      helperStyle: const TextStyle(fontFamily: fuente, color: Marca.gris, fontSize: 12),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: Marca.brand,
        foregroundColor: Colors.white,
        minimumSize: const Size.fromHeight(52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: const TextStyle(fontFamily: fuente, fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: Marca.brand,
        minimumSize: const Size.fromHeight(52),
        side: const BorderSide(color: Marca.brand, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: const TextStyle(fontFamily: fuente, fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    // Todo lo que se pulsa mide al menos 48 px de alto: en campo se usa con
    // los dedos mojados o con guantes.
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: Marca.brand,
        minimumSize: const Size(48, 48),
        textStyle: const TextStyle(fontFamily: fuente, fontSize: 15, fontWeight: FontWeight.w700),
      ),
    ),
    segmentedButtonTheme: SegmentedButtonThemeData(
      style: ButtonStyle(
        visualDensity: VisualDensity.standard,
        minimumSize: const WidgetStatePropertyAll(Size(0, 48)),
        textStyle: WidgetStatePropertyAll(const TextStyle(fontFamily: fuente, fontSize: 14, fontWeight: FontWeight.w600)),
        backgroundColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? Marca.tealClaro : Colors.white),
        foregroundColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? Marca.brand : Marca.tintaSuave),
        side: const WidgetStatePropertyAll(BorderSide(color: Color(0xFFCFD8DC))),
      ),
    ),
    snackBarTheme: const SnackBarThemeData(backgroundColor: Marca.navy, contentTextStyle: TextStyle(fontFamily: fuente, color: Colors.white), behavior: SnackBarBehavior.floating),
    dividerTheme: const DividerThemeData(color: Marca.linea, thickness: 1),
    floatingActionButtonTheme: const FloatingActionButtonThemeData(backgroundColor: Marca.brand, foregroundColor: Colors.white),
  );
}

/// Insignia de estado, como en el portal.
class Insignia extends StatelessWidget {
  final String estado;
  final String texto;
  const Insignia({super.key, required this.estado, required this.texto});

  @override
  Widget build(BuildContext context) {
    final (fondo, color) = Marca.insignia(estado);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(color: fondo, borderRadius: BorderRadius.circular(999)),
      child: Text(texto, maxLines: 1, overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: color, letterSpacing: 0.2)),
    );
  }
}

/// Encabezado de sección en mayúsculas pequeñas, como "TU PANEL" en el portal.
class Rotulo extends StatelessWidget {
  final String texto;
  const Rotulo(this.texto, {super.key});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 22, bottom: 10),
        child: Text(texto.toUpperCase(), style: Theme.of(context).textTheme.titleSmall),
      );
}

/// Tarjeta blanca con borde fino y radio 16, como `.sp-card`.
class Tarjeta extends StatelessWidget {
  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  const Tarjeta({super.key, required this.child, this.padding = const EdgeInsets.all(16), this.onTap});
  @override
  Widget build(BuildContext context) => Card(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(16),
          child: Padding(padding: padding, child: child),
        ),
      );
}
