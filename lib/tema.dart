import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

// Tema de la app, con los tokens del portal web de CamaronMarket (resumen en
// docs/DISENO-APP.md):
//  - claro: shrimp_user_registry/static/src/css/shrimp_base.css (:root),
//    shrimp_marketplace/static/src/css/shrimp_product_form.css (.pf-sec,
//    .pf-alert) y shrimp_verification/static/src/css/verification.css (.vr-pill);
//  - oscuro: shrimp_marketplace/static/src/css/shrimp_dark.css (--dk-*) y
//    shrimp_dark_auto.css (avisos .pf-a-* en oscuro).
// Los colores se piden con `context.colores` (cambian con el modo); los que
// están en ColorScheme, con Theme.of(context).colorScheme.

/// Estilo de un aviso (`.pf-alert`): fondo suave, borde fino y texto.
@immutable
class EstiloAviso {
  final Color fondo;
  final Color borde;
  final Color texto;
  const EstiloAviso({required this.fondo, required this.borde, required this.texto});

  static EstiloAviso lerp(EstiloAviso a, EstiloAviso b, double t) => EstiloAviso(
        fondo: Color.lerp(a.fondo, b.fondo, t)!,
        borde: Color.lerp(a.borde, b.borde, t)!,
        texto: Color.lerp(a.texto, b.texto, t)!,
      );
}

/// Avisos del portal: `.pf-a-info`, `.vr-a-ok`, `.pf-a-warn`, `.pf-a-bad`, `.pf-a-brand`.
enum TipoAviso { info, ok, advertencia, peligro, marca }

/// Píldoras de estado del portal (`.vr-pill--*`).
enum TipoPildora {
  /// received: gris, texto secundario.
  neutro,

  /// assigned / info: teal suave.
  info,

  /// in_field / warn: ámbar.
  advertencia,

  /// done: marca suave.
  hecho,

  /// approved / ok: verde.
  ok,

  /// rejected / bad: rojo.
  peligro,

  /// cancelled / muted: gris, texto tenue.
  apagado,
}

/// Colores de la marca que no caben en [ColorScheme] (superficies, tinta,
/// semánticos, avisos y píldoras). Hay una paleta clara y una oscura; se
/// piden con `context.colores`.
@immutable
class ColoresMarca extends ThemeExtension<ColoresMarca> {
  final Brightness brillo;

  /// Fondo de página (`--shrimp-bg`).
  final Color fondo;

  /// Fondo alterno: píldoras grises, cajas de ayuda (`--shrimp-bg-alt`).
  final Color fondoAlt;

  /// Tarjetas, barras y diálogos (`--shrimp-surface`).
  final Color superficie;

  /// Bloque dentro de una tarjeta (`--shrimp-card-bg`, `.vr-sub`).
  final Color superficieAnidada;

  /// Borde fino de tarjetas y separadores (`--shrimp-border`).
  final Color borde;

  /// Borde de botón fantasma y segmentos (`--shrimp-border-strong`).
  final Color bordeFuerte;

  /// Campos de formulario (`--shrimp-input-*`, `--shrimp-label-ink`, `--shrimp-help-ink`).
  final Color campoFondo;
  final Color campoBorde;
  final Color campoFoco;
  final Color campoEtiqueta;
  final Color campoAyuda;
  final Color campoMarcador;

  /// Texto principal, secundario y terciario (`--shrimp-ink`, `-soft`, `-faint`).
  final Color tinta;
  final Color tintaSuave;
  final Color tintaTenue;

  /// Títulos de tarjeta y de pantalla (navy en claro, como `--sp-navy`).
  final Color titulo;

  /// Marca como FONDO de botón, con texto blanco encima (`--shrimp-brand`).
  final Color marca;

  /// Marca como TEXTO o icono (`--shrimp-brand` en claro, `--dk-brand-text` en oscuro).
  final Color marcaTexto;

  /// Fondo suave de marca (`--shrimp-brand-soft`).
  final Color marcaSuave;

  /// Teal de acento como fondo (`--shrimp-teal`).
  final Color acento;

  /// Teal como texto o icono (`--shrimp-teal` / `--dk-teal-text`).
  final Color acentoTexto;

  /// Teal suave (`--shrimp-teal-soft`).
  final Color acentoSuave;

  /// Semánticos: el color «de fondo» (botón, punto, barra), el de texto
  /// legible sobre la superficie y el suave para fondos.
  final Color exito;
  final Color exitoTexto;
  final Color exitoSuave;
  final Color advertencia;
  final Color advertenciaTexto;
  final Color advertenciaSuave;
  final Color peligro;
  final Color peligroTexto;
  final Color peligroSuave;

  /// Avisos (`.pf-alert`).
  final EstiloAviso avisoInfo;
  final EstiloAviso avisoOk;
  final EstiloAviso avisoAdvertencia;
  final EstiloAviso avisoPeligro;
  final EstiloAviso avisoMarca;

  /// Sombra de las tarjetas (`--shrimp-shadow`; en oscuro no hay).
  final List<BoxShadow> sombra;

  const ColoresMarca({
    required this.brillo,
    required this.fondo,
    required this.fondoAlt,
    required this.superficie,
    required this.superficieAnidada,
    required this.borde,
    required this.bordeFuerte,
    required this.campoFondo,
    required this.campoBorde,
    required this.campoFoco,
    required this.campoEtiqueta,
    required this.campoAyuda,
    required this.campoMarcador,
    required this.tinta,
    required this.tintaSuave,
    required this.tintaTenue,
    required this.titulo,
    required this.marca,
    required this.marcaTexto,
    required this.marcaSuave,
    required this.acento,
    required this.acentoTexto,
    required this.acentoSuave,
    required this.exito,
    required this.exitoTexto,
    required this.exitoSuave,
    required this.advertencia,
    required this.advertenciaTexto,
    required this.advertenciaSuave,
    required this.peligro,
    required this.peligroTexto,
    required this.peligroSuave,
    required this.avisoInfo,
    required this.avisoOk,
    required this.avisoAdvertencia,
    required this.avisoPeligro,
    required this.avisoMarca,
    required this.sombra,
  });

  bool get esOscuro => brillo == Brightness.dark;

  // ---- Banda de marca (el degradado navy del portal, `.sf-banner`): igual
  // en los dos modos, como en el portal.

  /// Degradado de las cabeceras (`.sf-banner`: navy → azul medio).
  static const degradado = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF0B2F47), Color(0xFF123E5C), Color(0xFF2E7DA6)],
    stops: [0, 0.55, 1],
  );

  /// Fondo navy oscuro de franjas fijas (modo supervisor) (`--sl-navy`).
  static const banda = Color(0xFF0B2F47);

  /// Texto sobre la banda o el degradado.
  static const sobreBanda = Colors.white;

  /// Texto secundario sobre la banda (`.sf-banner p`).
  static const sobreBandaSuave = Color(0xFFC6DDE8);

  /// Acento legible sobre la banda (`.sf-kicker`).
  static const sobreBandaAcento = Color(0xFFCFEAEF);

  /// Aviso amarillo sobre la banda (solo lectura, caducidad).
  static const sobreBandaAviso = Color(0xFFFFE08A);

  /// Placa clara detrás del logo en modo oscuro (`.navbar-brand.logo`).
  static const placaLogo = Color(0xF0F4F8FB);

  static const claro = ColoresMarca(
    brillo: Brightness.light,
    fondo: Color(0xFFF6F8FA),
    fondoAlt: Color(0xFFEEF1F4),
    superficie: Color(0xFFFFFFFF),
    superficieAnidada: Color(0xFFF6F8FA),
    borde: Color(0xFFE4E8EC),
    bordeFuerte: Color(0xFFD3D9DF),
    campoFondo: Color(0xFFFFFFFF),
    campoBorde: Color(0xFFC3CDD9),
    campoFoco: Color(0xFF0E8EA0),
    campoEtiqueta: Color(0xFF33424E),
    campoAyuda: Color(0xFF5F6B78),
    campoMarcador: Color(0xFF66727F),
    tinta: Color(0xFF0D1B26),
    tintaSuave: Color(0xFF55636D),
    tintaTenue: Color(0xFF7C8794),
    titulo: Color(0xFF123E5C),
    marca: Color(0xFF123E5C),
    marcaTexto: Color(0xFF123E5C),
    marcaSuave: Color(0xFFE7EEF3),
    acento: Color(0xFF0E8EA0),
    acentoTexto: Color(0xFF0E8EA0),
    acentoSuave: Color(0xFFE2F3F5),
    exito: Color(0xFF1F8F5F),
    exitoTexto: Color(0xFF1F8F5F),
    exitoSuave: Color(0xFFE4F4EC),
    advertencia: Color(0xFFB7791F),
    advertenciaTexto: Color(0xFF8A5A00),
    advertenciaSuave: Color(0xFFFBF1DE),
    peligro: Color(0xFFC0392B),
    peligroTexto: Color(0xFFC0392B),
    peligroSuave: Color(0xFFFBE9E7),
    avisoInfo: EstiloAviso(fondo: Color(0xFFE2F3F5), borde: Color(0xFFC5E7EC), texto: Color(0xFF0B5F6B)),
    avisoOk: EstiloAviso(fondo: Color(0xFFE4F4EC), borde: Color(0xFFB6E0C6), texto: Color(0xFF17603A)),
    avisoAdvertencia: EstiloAviso(fondo: Color(0xFFFBF1DE), borde: Color(0xFFF0D9A8), texto: Color(0xFF8A5A00)),
    avisoPeligro: EstiloAviso(fondo: Color(0xFFFBE9E7), borde: Color(0xFFF1C6C0), texto: Color(0xFF8F2A20)),
    avisoMarca: EstiloAviso(fondo: Color(0xFFE7EEF3), borde: Color(0xFFD3E0EA), texto: Color(0xFF123E5C)),
    sombra: [
      BoxShadow(color: Color(0x0D0D1B26), offset: Offset(0, 2), blurRadius: 6),
      BoxShadow(color: Color(0x120D1B26), offset: Offset(0, 12), blurRadius: 28),
    ],
  );

  static const oscuro = ColoresMarca(
    brillo: Brightness.dark,
    fondo: Color(0xFF0C141B),
    fondoAlt: Color(0xFF1C2935),
    superficie: Color(0xFF16212B),
    superficieAnidada: Color(0xFF1C2935),
    borde: Color(0xFF2A3B4A),
    bordeFuerte: Color(0xFF385066),
    campoFondo: Color(0xFF101A23),
    campoBorde: Color(0xFF3A5166),
    campoFoco: Color(0xFF5FD0DC),
    campoEtiqueta: Color(0xFFCFDBE5),
    campoAyuda: Color(0xFF9FB0BE),
    campoMarcador: Color(0xFF8FA1B1),
    tinta: Color(0xFFE7EEF4),
    tintaSuave: Color(0xFFB2C1CD),
    tintaTenue: Color(0xFF8FA1B1),
    titulo: Color(0xFFE7EEF4),
    marca: Color(0xFF2A6F98),
    marcaTexto: Color(0xFF8CC8EC),
    marcaSuave: Color(0xFF17324A),
    acento: Color(0xFF0E7F8F),
    acentoTexto: Color(0xFF5FD0DC),
    acentoSuave: Color(0xFF123A40),
    exito: Color(0xFF1F8F5F),
    exitoTexto: Color(0xFF5FD39B),
    exitoSuave: Color(0xFF143528),
    advertencia: Color(0xFFB7791F),
    advertenciaTexto: Color(0xFFF0C060),
    advertenciaSuave: Color(0xFF3A2E12),
    peligro: Color(0xFFC0392B),
    peligroTexto: Color(0xFFFF8A7D),
    peligroSuave: Color(0xFF3E1C1A),
    avisoInfo: EstiloAviso(fondo: Color(0xFF123A40), borde: Color(0xFF355E64), texto: Color(0xFFBEDCE0)),
    avisoOk: EstiloAviso(fondo: Color(0xFF143528), borde: Color(0xFF1F5A40), texto: Color(0xFFBEE0D1)),
    avisoAdvertencia: EstiloAviso(fondo: Color(0xFF3A2E12), borde: Color(0xFF6B582E), texto: Color(0xFFE0D4BE)),
    avisoPeligro: EstiloAviso(fondo: Color(0xFF3E1C1A), borde: Color(0xFF6A362F), texto: Color(0xFFE1847B)),
    avisoMarca: EstiloAviso(fondo: Color(0xFF17324A), borde: Color(0xFF3C4F5D), texto: Color(0xFF8CC8EC)),
    sombra: [],
  );

  /// Estilo de un aviso (`.pf-alert`).
  EstiloAviso aviso(TipoAviso tipo) => switch (tipo) {
        TipoAviso.info => avisoInfo,
        TipoAviso.ok => avisoOk,
        TipoAviso.advertencia => avisoAdvertencia,
        TipoAviso.peligro => avisoPeligro,
        TipoAviso.marca => avisoMarca,
      };

  /// Fondo y texto de una píldora de estado (`.vr-pill--*`). En oscuro el
  /// texto pasa a la variante clara (`--dk-*-text`), como en el portal.
  (Color fondo, Color texto) pildora(TipoPildora tipo) => switch (tipo) {
        TipoPildora.neutro => (fondoAlt, tintaSuave),
        TipoPildora.info => (acentoSuave, marcaTexto),
        TipoPildora.advertencia => (advertenciaSuave, esOscuro ? advertenciaTexto : advertencia),
        TipoPildora.hecho => (marcaSuave, marcaTexto),
        TipoPildora.ok => (exitoSuave, exitoTexto),
        TipoPildora.peligro => (peligroSuave, peligroTexto),
        TipoPildora.apagado => (fondoAlt, tintaTenue),
      };

  /// Píldora según el estado que manda el servidor (verificaciones, compras,
  /// despachos, maquila).
  (Color fondo, Color texto) insignia(String estado) => pildora(tipoDeEstado(estado));

  static TipoPildora tipoDeEstado(String estado) => switch (estado) {
        'received' || 'draft' || 'sin_cita' => TipoPildora.neutro,
        'assigned' || 'programado' => TipoPildora.info,
        'in_field' || 'pending_verification' || 'pending_acceptance' || 'waiting' || 'open' || 'en_ruta' =>
          TipoPildora.advertencia,
        'done' || 'packed' || 'signed' => TipoPildora.hecho,
        'approved' ||
        'approved_obs' ||
        'confirmed' ||
        'published' ||
        'sent' ||
        'accepted' ||
        'closed' ||
        'sold' ||
        'llegado' =>
          TipoPildora.ok,
        'rejected' || 'broken' || 'disputed' => TipoPildora.peligro,
        'cancelled' || 'cancel' || 'withdrawn' || 'archived' => TipoPildora.apagado,
        _ => TipoPildora.neutro,
      };

  @override
  ColoresMarca copyWith() => this;

  @override
  ColoresMarca lerp(ColoresMarca? other, double t) {
    if (other == null) return this;
    Color c(Color a, Color b) => Color.lerp(a, b, t)!;
    return ColoresMarca(
      brillo: t < 0.5 ? brillo : other.brillo,
      fondo: c(fondo, other.fondo),
      fondoAlt: c(fondoAlt, other.fondoAlt),
      superficie: c(superficie, other.superficie),
      superficieAnidada: c(superficieAnidada, other.superficieAnidada),
      borde: c(borde, other.borde),
      bordeFuerte: c(bordeFuerte, other.bordeFuerte),
      campoFondo: c(campoFondo, other.campoFondo),
      campoBorde: c(campoBorde, other.campoBorde),
      campoFoco: c(campoFoco, other.campoFoco),
      campoEtiqueta: c(campoEtiqueta, other.campoEtiqueta),
      campoAyuda: c(campoAyuda, other.campoAyuda),
      campoMarcador: c(campoMarcador, other.campoMarcador),
      tinta: c(tinta, other.tinta),
      tintaSuave: c(tintaSuave, other.tintaSuave),
      tintaTenue: c(tintaTenue, other.tintaTenue),
      titulo: c(titulo, other.titulo),
      marca: c(marca, other.marca),
      marcaTexto: c(marcaTexto, other.marcaTexto),
      marcaSuave: c(marcaSuave, other.marcaSuave),
      acento: c(acento, other.acento),
      acentoTexto: c(acentoTexto, other.acentoTexto),
      acentoSuave: c(acentoSuave, other.acentoSuave),
      exito: c(exito, other.exito),
      exitoTexto: c(exitoTexto, other.exitoTexto),
      exitoSuave: c(exitoSuave, other.exitoSuave),
      advertencia: c(advertencia, other.advertencia),
      advertenciaTexto: c(advertenciaTexto, other.advertenciaTexto),
      advertenciaSuave: c(advertenciaSuave, other.advertenciaSuave),
      peligro: c(peligro, other.peligro),
      peligroTexto: c(peligroTexto, other.peligroTexto),
      peligroSuave: c(peligroSuave, other.peligroSuave),
      avisoInfo: EstiloAviso.lerp(avisoInfo, other.avisoInfo, t),
      avisoOk: EstiloAviso.lerp(avisoOk, other.avisoOk, t),
      avisoAdvertencia: EstiloAviso.lerp(avisoAdvertencia, other.avisoAdvertencia, t),
      avisoPeligro: EstiloAviso.lerp(avisoPeligro, other.avisoPeligro, t),
      avisoMarca: EstiloAviso.lerp(avisoMarca, other.avisoMarca, t),
      sombra: BoxShadow.lerpList(sombra, other.sombra, t) ?? const [],
    );
  }
}

/// `context.colores.tintaSuave`, `context.colores.aviso(TipoAviso.ok)`...
extension ColoresDelTema on BuildContext {
  ColoresMarca get colores => Theme.of(this).extension<ColoresMarca>() ?? ColoresMarca.claro;
}

/// Radios del portal (`--shrimp-radius-*`).
abstract final class Radios {
  /// Botones (`--shrimp-radius-sm`).
  static const boton = 10.0;

  /// Campos de formulario (`--shrimp-input-radius`), avisos y cajas.
  static const campo = 12.0;

  /// Tarjetas y diálogos (`--shrimp-radius`).
  static const tarjeta = 16.0;

  /// Banners y hojas inferiores (`--shrimp-radius-lg`).
  static const grande = 22.0;
}

const _fuente = 'Inter';

/// Tema claro (nombre de antes).
ThemeData temaCamaronMarket() => temaClaro();

ThemeData temaClaro() => _tema(ColoresMarca.claro);

ThemeData temaOscuro() => _tema(ColoresMarca.oscuro);

ThemeData _tema(ColoresMarca c) {
  final oscuro = c.esOscuro;
  const sobreClaro = Color(0xFF0B2F47);
  // En oscuro, `primary` es la variante de TEXTO de la marca (la usan los
  // botones de texto, el foco, las casillas...); los botones rellenos llevan
  // su propio fondo (`c.marca`) con texto blanco, como `.s-btn-primary`.
  final esquema = ColorScheme(
    brightness: c.brillo,
    primary: c.marcaTexto,
    onPrimary: oscuro ? sobreClaro : Colors.white,
    primaryContainer: c.marcaSuave,
    onPrimaryContainer: c.marcaTexto,
    secondary: c.acentoTexto,
    onSecondary: oscuro ? sobreClaro : Colors.white,
    secondaryContainer: c.acentoSuave,
    onSecondaryContainer: c.avisoInfo.texto,
    tertiary: c.exitoTexto,
    onTertiary: oscuro ? sobreClaro : Colors.white,
    tertiaryContainer: c.exitoSuave,
    onTertiaryContainer: c.avisoOk.texto,
    error: c.peligroTexto,
    onError: oscuro ? const Color(0xFF3E1C1A) : Colors.white,
    errorContainer: c.peligroSuave,
    onErrorContainer: c.avisoPeligro.texto,
    surface: c.superficie,
    onSurface: c.tinta,
    onSurfaceVariant: c.tintaSuave,
    surfaceDim: c.fondo,
    surfaceBright: c.superficie,
    surfaceContainerLowest: oscuro ? c.fondo : c.superficie,
    surfaceContainerLow: oscuro ? c.superficie : c.fondo,
    surfaceContainer: oscuro ? c.superficie : c.fondo,
    surfaceContainerHigh: oscuro ? c.fondoAlt : c.superficie,
    surfaceContainerHighest: oscuro ? const Color(0xFF233241) : c.fondoAlt,
    outline: c.bordeFuerte,
    outlineVariant: c.borde,
    shadow: Colors.black,
    scrim: Colors.black,
    inverseSurface: oscuro ? const Color(0xFF233241) : c.marca,
    onInverseSurface: oscuro ? c.tinta : Colors.white,
    inversePrimary: oscuro ? c.marca : const Color(0xFF8CC8EC),
    surfaceTint: Colors.transparent,
  );

  final base = ThemeData(
    useMaterial3: true,
    brightness: c.brillo,
    fontFamily: _fuente,
    colorScheme: esquema,
    scaffoldBackgroundColor: c.fondo,
    canvasColor: c.superficie,
    dividerColor: c.borde,
    extensions: [c],
  );

  TextStyle t(double tam, FontWeight peso, Color color, {double? espaciado, double? alto}) =>
      TextStyle(fontFamily: _fuente, fontSize: tam, fontWeight: peso, color: color, letterSpacing: espaciado, height: alto);

  final textos = base.textTheme.apply(bodyColor: c.tinta, displayColor: c.titulo, fontFamily: _fuente).copyWith(
        // Títulos en 800 con el espaciado negativo del portal (.vr-ph h1, .pf-sec-h h2).
        headlineSmall: t(22, FontWeight.w800, c.titulo, espaciado: -0.44, alto: 1.2),
        titleLarge: t(20, FontWeight.w800, c.titulo, espaciado: -0.3, alto: 1.2),
        titleMedium: t(16, FontWeight.w700, c.titulo, espaciado: -0.1),
        // Rótulos de sección en mayúsculas (.s-filter-sec-label).
        titleSmall: t(13, FontWeight.w700, c.tintaSuave, espaciado: 0.6),
        bodyLarge: t(16, FontWeight.w400, c.tinta, alto: 1.4),
        bodyMedium: t(15, FontWeight.w400, c.tinta, alto: 1.4),
        bodySmall: t(13, FontWeight.w400, c.tintaSuave, alto: 1.35),
        labelLarge: t(15, FontWeight.w600, c.tinta),
        labelMedium: t(13, FontWeight.w600, c.tintaSuave),
        labelSmall: t(12, FontWeight.w600, c.tintaTenue),
      );

  final radioBoton = RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radios.boton));
  final textoBoton = t(15.5, FontWeight.w600, Colors.white);
  OutlineInputBorder bordeCampo(Color color, [double ancho = 1]) =>
      OutlineInputBorder(borderRadius: BorderRadius.circular(Radios.campo), borderSide: BorderSide(color: color, width: ancho));
  final estiloBarras = (oscuro ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark).copyWith(
    statusBarColor: Colors.transparent,
    systemNavigationBarColor: c.superficie,
    systemNavigationBarIconBrightness: oscuro ? Brightness.light : Brightness.dark,
  );

  return base.copyWith(
    textTheme: textos,
    iconTheme: IconThemeData(color: c.tintaSuave),
    appBarTheme: AppBarTheme(
      backgroundColor: c.superficie,
      foregroundColor: c.titulo,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0.5,
      shadowColor: oscuro ? Colors.black : const Color(0x1A000000),
      centerTitle: false,
      systemOverlayStyle: estiloBarras,
      iconTheme: IconThemeData(color: c.titulo),
      actionsIconTheme: IconThemeData(color: c.tintaSuave),
      titleTextStyle: t(18, FontWeight.w800, c.titulo, espaciado: -0.2),
    ),
    cardTheme: CardThemeData(
      color: c.superficie,
      elevation: 0,
      surfaceTintColor: Colors.transparent,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radios.tarjeta), side: BorderSide(color: c.borde)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: c.campoFondo,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
      border: bordeCampo(c.campoBorde),
      enabledBorder: bordeCampo(c.campoBorde),
      disabledBorder: bordeCampo(c.borde),
      focusedBorder: bordeCampo(c.campoFoco, 2),
      errorBorder: bordeCampo(esquema.error),
      focusedErrorBorder: bordeCampo(esquema.error, 2),
      labelStyle: t(15, FontWeight.w500, c.campoEtiqueta),
      floatingLabelStyle: WidgetStateTextStyle.resolveWith((s) => t(
          15,
          FontWeight.w600,
          s.contains(WidgetState.error)
              ? esquema.error
              : s.contains(WidgetState.focused)
                  ? c.campoFoco
                  : c.campoEtiqueta)),
      hintStyle: t(15, FontWeight.w400, c.campoMarcador),
      helperStyle: t(12.5, FontWeight.w400, c.campoAyuda),
      errorStyle: t(12.5, FontWeight.w500, esquema.error),
      prefixIconColor: c.tintaTenue,
      suffixIconColor: c.tintaTenue,
    ),
    // Primario (.s-btn-primary): fondo de marca, texto blanco.
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: c.marca,
        foregroundColor: Colors.white,
        disabledBackgroundColor: oscuro ? const Color(0xFF2F4252) : c.fondoAlt,
        disabledForegroundColor: oscuro ? c.tintaSuave : c.tintaTenue,
        minimumSize: const Size.fromHeight(52),
        shape: radioBoton,
        textStyle: textoBoton,
      ),
    ),
    // Fantasma (.s-btn-ghost): superficie, borde fuerte y tinta.
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        backgroundColor: c.superficie,
        foregroundColor: c.tinta,
        disabledForegroundColor: c.tintaTenue,
        minimumSize: const Size.fromHeight(52),
        shape: radioBoton,
        textStyle: textoBoton,
      ).copyWith(
        side: WidgetStateProperty.resolveWith(
            (s) => BorderSide(color: s.contains(WidgetState.disabled) ? c.borde : c.bordeFuerte)),
      ),
    ),
    // Todo lo que se pulsa mide al menos 48 px de alto: en campo se usa con
    // los dedos mojados o con guantes.
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: c.marcaTexto,
        minimumSize: const Size(48, 48),
        shape: radioBoton,
        textStyle: t(15, FontWeight.w700, c.marcaTexto),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: c.marca,
        foregroundColor: Colors.white,
        minimumSize: const Size(48, 48),
        shape: radioBoton,
        textStyle: textoBoton,
      ),
    ),
    segmentedButtonTheme: SegmentedButtonThemeData(
      style: ButtonStyle(
        visualDensity: VisualDensity.standard,
        minimumSize: const WidgetStatePropertyAll(Size(0, 48)),
        textStyle: WidgetStatePropertyAll(t(14, FontWeight.w600, c.tintaSuave)),
        backgroundColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? c.marcaSuave : c.superficie),
        foregroundColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? c.marcaTexto : c.tintaSuave),
        iconColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.selected) ? c.marcaTexto : c.tintaSuave),
        side: WidgetStatePropertyAll(BorderSide(color: c.bordeFuerte)),
      ),
    ),
    // Chips (.s-cat-chip): píldora con borde; el elegido, en el color de marca.
    chipTheme: ChipThemeData(
      backgroundColor: c.superficie,
      selectedColor: c.marca,
      disabledColor: c.fondoAlt,
      checkmarkColor: Colors.white,
      side: BorderSide(color: c.borde),
      shape: const StadiumBorder(),
      labelStyle: t(13.5, FontWeight.w600, c.tintaSuave),
      secondaryLabelStyle: t(13.5, FontWeight.w600, Colors.white),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: oscuro ? const Color(0xF71C2935) : c.marca,
      contentTextStyle: t(14.5, FontWeight.w500, oscuro ? c.tinta : Colors.white),
      actionTextColor: oscuro ? c.acentoTexto : const Color(0xFF8FE3EC),
      closeIconColor: oscuro ? c.tintaSuave : Colors.white70,
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(Radios.campo),
        side: oscuro ? BorderSide(color: c.borde) : BorderSide.none,
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: c.superficie,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(Radios.tarjeta), side: oscuro ? BorderSide(color: c.borde) : BorderSide.none),
      titleTextStyle: t(19, FontWeight.w800, c.titulo, espaciado: -0.2),
      contentTextStyle: t(15, FontWeight.w400, c.tintaSuave, alto: 1.45),
      iconColor: c.marcaTexto,
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: c.superficie,
      surfaceTintColor: Colors.transparent,
      modalBackgroundColor: c.superficie,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(Radios.grande))),
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: c.superficie,
      surfaceTintColor: Colors.transparent,
      elevation: 6,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radios.campo), side: BorderSide(color: c.borde)),
      textStyle: t(15, FontWeight.w500, c.tinta),
      labelTextStyle: WidgetStateProperty.resolveWith(
          (s) => t(15, FontWeight.w500, s.contains(WidgetState.disabled) ? c.tintaTenue : c.tinta)),
    ),
    menuTheme: MenuThemeData(
      style: MenuStyle(
        backgroundColor: WidgetStatePropertyAll(c.superficie),
        surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
        side: WidgetStatePropertyAll(BorderSide(color: c.borde)),
      ),
    ),
    // Barra inferior (.s-bottomnav): superficie, el activo en marca.
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: c.superficie,
      surfaceTintColor: Colors.transparent,
      indicatorColor: c.marcaSuave,
      elevation: 0,
      iconTheme: WidgetStateProperty.resolveWith(
          (s) => IconThemeData(color: s.contains(WidgetState.selected) ? c.marcaTexto : c.tintaTenue)),
      labelTextStyle: WidgetStateProperty.resolveWith(
          (s) => t(12, FontWeight.w600, s.contains(WidgetState.selected) ? c.marcaTexto : c.tintaTenue)),
    ),
    listTileTheme: ListTileThemeData(
      iconColor: c.tintaSuave,
      textColor: c.tinta,
      titleTextStyle: t(15.5, FontWeight.w600, c.tinta),
      subtitleTextStyle: t(13, FontWeight.w400, c.tintaSuave),
      selectedColor: c.marcaTexto,
    ),
    dividerTheme: DividerThemeData(color: c.borde, thickness: 1),
    floatingActionButtonTheme: FloatingActionButtonThemeData(
      backgroundColor: c.marca,
      foregroundColor: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(Radios.tarjeta)),
    ),
    badgeTheme: BadgeThemeData(backgroundColor: c.peligro, textColor: Colors.white),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: c.acentoTexto,
      linearTrackColor: c.fondoAlt,
      circularTrackColor: Colors.transparent,
    ),
    radioTheme: RadioThemeData(
      fillColor: WidgetStateProperty.resolveWith((s) => s.contains(WidgetState.disabled)
          ? c.bordeFuerte
          : s.contains(WidgetState.selected)
              ? c.marcaTexto
              : (oscuro ? const Color(0xFF56708A) : const Color(0xFF8796A6))),
    ),
    checkboxTheme: CheckboxThemeData(
      side: BorderSide(color: oscuro ? const Color(0xFF56708A) : const Color(0xFF8796A6), width: 1.5),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? Colors.white : (oscuro ? const Color(0xFFCFD9E1) : c.tintaTenue)),
      trackColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? (oscuro ? c.acento : c.marca) : (oscuro ? const Color(0xFF233241) : c.fondoAlt)),
      trackOutlineColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected) ? (oscuro ? c.acentoTexto : c.marca) : c.bordeFuerte),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(color: oscuro ? const Color(0xFF233241) : c.tinta, borderRadius: BorderRadius.circular(8)),
      textStyle: t(13, FontWeight.w500, oscuro ? c.tinta : Colors.white),
    ),
    textSelectionTheme: TextSelectionThemeData(
      cursorColor: c.campoFoco,
      selectionColor: c.campoFoco.withValues(alpha: 0.3),
      selectionHandleColor: c.campoFoco,
    ),
    expansionTileTheme: ExpansionTileThemeData(
      iconColor: c.tintaSuave,
      collapsedIconColor: c.tintaSuave,
      textColor: c.tinta,
      collapsedTextColor: c.tinta,
    ),
  );
}

/// Botones con el estilo del portal que no son el de por defecto.
abstract final class EstilosBoton {
  /// Acción destructiva (`.s-btn-danger`): borde y texto en rojo sobre la superficie.
  static ButtonStyle peligro(BuildContext context) {
    final c = context.colores;
    return OutlinedButton.styleFrom(
      foregroundColor: c.peligroTexto,
      backgroundColor: c.superficie,
      side: BorderSide(color: c.peligroTexto),
    );
  }

  /// Botón relleno de peligro (confirmar algo que no se deshace).
  static ButtonStyle peligroRelleno(BuildContext context) =>
      FilledButton.styleFrom(backgroundColor: context.colores.peligro, foregroundColor: Colors.white);

  /// Botón relleno en un color semántico (aprobar en verde, por ejemplo).
  static ButtonStyle relleno(Color fondo) => FilledButton.styleFrom(backgroundColor: fondo, foregroundColor: Colors.white);
}

/// Píldora de estado, como `.vr-pill` en el portal (con su punto).
class Insignia extends StatelessWidget {
  final String estado;
  final String texto;

  /// Si se da, manda sobre el estado.
  final TipoPildora? tipo;
  const Insignia({super.key, required this.estado, required this.texto, this.tipo});

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final (fondo, color) = tipo == null ? c.insignia(estado) : c.pildora(tipo!);
    return Container(
      padding: const EdgeInsets.fromLTRB(9, 4, 11, 4),
      decoration: BoxDecoration(color: fondo, borderRadius: BorderRadius.circular(999)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 7, height: 7, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
        const SizedBox(width: 6),
        Flexible(
          child: Text(texto,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: color)),
        ),
      ]),
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

/// Tarjeta de sección (`.pf-sec`): superficie, borde fino, radio 16 y la
/// sombra suave del portal (en oscuro, sin sombra).
class Tarjeta extends StatelessWidget {
  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;

  /// Fondo distinto del de la superficie (p. ej. una tarjeta resaltada).
  final Color? color;

  /// Borde distinto del fino de siempre.
  final Color? colorBorde;
  const Tarjeta(
      {super.key, required this.child, this.padding = const EdgeInsets.all(16), this.onTap, this.color, this.colorBorde});

  @override
  Widget build(BuildContext context) {
    final c = context.colores;
    final radio = BorderRadius.circular(Radios.tarjeta);
    return DecoratedBox(
      decoration: BoxDecoration(borderRadius: radio, boxShadow: c.sombra),
      child: Material(
        color: color ?? c.superficie,
        shape: RoundedRectangleBorder(borderRadius: radio, side: BorderSide(color: colorBorde ?? c.borde)),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Padding(padding: padding, child: child),
        ),
      ),
    );
  }
}

/// Caja de aviso con icono (`.pf-alert`): info, ok, advertencia, peligro o marca.
class CajaAviso extends StatelessWidget {
  final TipoAviso tipo;
  final IconData icono;
  final String? texto;
  final Widget? child;
  final double tamTexto;
  final FontWeight peso;

  /// Sin radio ni borde: franja a todo el ancho (debajo de la barra).
  final bool franja;
  const CajaAviso({
    super.key,
    required this.tipo,
    required this.icono,
    this.texto,
    this.child,
    this.tamTexto = 13.5,
    this.peso = FontWeight.w500,
    this.franja = false,
  }) : assert(texto != null || child != null);

  @override
  Widget build(BuildContext context) {
    final e = context.colores.aviso(tipo);
    return Container(
      width: double.infinity,
      padding: franja ? const EdgeInsets.symmetric(horizontal: 16, vertical: 9) : const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: e.fondo,
        borderRadius: franja ? null : BorderRadius.circular(Radios.campo),
        border: franja ? Border(bottom: BorderSide(color: e.borde)) : Border.all(color: e.borde),
      ),
      child: Row(crossAxisAlignment: franja ? CrossAxisAlignment.center : CrossAxisAlignment.start, children: [
        Icon(icono, size: 18, color: e.texto),
        const SizedBox(width: 10),
        Expanded(
          child: DefaultTextStyle.merge(
            style: TextStyle(fontSize: tamTexto, color: e.texto, height: 1.4, fontWeight: peso),
            child: child ?? Text(texto!),
          ),
        ),
      ]),
    );
  }
}

/// Icono en su caja de color suave (`.vr-ico`). Sin colores, los de marca.
class CajaIcono extends StatelessWidget {
  final IconData icono;
  final Color? fondo;
  final Color? color;
  final double lado;
  final double tamIcono;
  const CajaIcono(this.icono, {super.key, this.fondo, this.color, this.lado = 40, this.tamIcono = 22});

  @override
  Widget build(BuildContext context) => Container(
        width: lado,
        height: lado,
        decoration: BoxDecoration(
            color: fondo ?? context.colores.marcaSuave, borderRadius: BorderRadius.circular(lado * 0.29)),
        child: Icon(icono, color: color ?? context.colores.marcaTexto, size: tamIcono),
      );
}

/// Logo de CamaronMarket. Las letras son navy sobre transparente: en modo
/// oscuro lleva una placa clara detrás, como el logo del portal.
class LogoMarca extends StatelessWidget {
  final double alto;
  const LogoMarca({super.key, this.alto = 30});

  @override
  Widget build(BuildContext context) {
    final imagen = Image.asset('assets/logo.png', height: alto, semanticLabel: 'CamaronMarket');
    if (!context.colores.esOscuro) return imagen;
    return Container(
      padding: EdgeInsets.symmetric(horizontal: alto * 0.3, vertical: alto * 0.1),
      decoration: BoxDecoration(color: ColoresMarca.placaLogo, borderRadius: BorderRadius.circular(alto * 0.35)),
      child: imagen,
    );
  }
}

/// Colores de antes (paleta clara fija). Las pantallas ya no los usan: no
/// cambian con el modo oscuro.
@Deprecated('Usa context.colores (ColoresMarca) o Theme.of(context).colorScheme: cambian con el modo oscuro.')
abstract final class Marca {
  static const navy = Color(0xFF123E5C);
  static const navyOscuro = ColoresMarca.banda;
  static const teal = Color(0xFF0E8EA0);
  static const brand = Color(0xFF123E5C);
  static const tinta = Color(0xFF0D1B26);
  static const tintaSuave = Color(0xFF55636D);
  static const gris = Color(0xFF7C8794);
  static const linea = Color(0xFFE4E8EC);
  static const fondo = Color(0xFFF6F8FA);
  static const fondoSuave = Color(0xFFEEF1F4);
  static const tealClaro = Color(0xFFE2F3F5);
  static const verde = Color(0xFF1F8F5F);
  static const rojo = Color(0xFFC0392B);
  static const ambar = Color(0xFF8A5A00);
  static const degradado = ColoresMarca.degradado;
  static (Color fondo, Color texto) insignia(String estado) => ColoresMarca.claro.insignia(estado);
}
