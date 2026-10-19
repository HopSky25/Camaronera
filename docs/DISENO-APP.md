# Diseño de la app: tokens del portal

La app usa los mismos colores, tipografía y componentes que el portal web de
CamaronMarket (módulos `shrimp_*` de Odoo). Todo vive en `lib/tema.dart`:

- `temaClaro()` y `temaOscuro()`: `ThemeData` completos (ColorScheme, barra,
  tarjetas, botones, campos, chips, avisos flotantes, diálogos, menús, barra
  inferior, listas, separadores...).
- `ColoresMarca` (`ThemeExtension`): los colores que no caben en
  `ColorScheme`. Se piden con `context.colores` y cambian con el modo:
  `context.colores.tintaSuave`, `context.colores.aviso(TipoAviso.ok)`,
  `context.colores.insignia('approved')`.
- Componentes: `Tarjeta` (`.pf-sec`), `CajaAviso` (`.pf-alert`), `Insignia`
  (`.vr-pill`), `CajaIcono` (`.vr-ico`), `Rotulo`, `LogoMarca`,
  `EstilosBoton.peligro` (`.s-btn-danger`).

No escribas colores fijos (`Color(0x…)`, `Colors.white`) en las pantallas: se
ven mal en oscuro. Solo valen sobre la banda de marca (el degradado navy), que
es igual en los dos modos (`ColoresMarca.degradado`, `banda`, `sobreBanda*`).
La clase `Marca` de antes queda como obsoleta (paleta clara fija).

## De dónde sale cada cosa

| Qué | Hoja del portal |
| --- | --- |
| Paleta clara, radios, sombras, espaciado, botones, insignias | `shrimp_user_registry/static/src/css/shrimp_base.css` (`:root`) |
| Paleta oscura | `shrimp_marketplace/static/src/css/shrimp_dark.css` (`--dk-*` y `--shrimp-*` en `html[data-bs-theme="dark"]`) |
| Avisos en oscuro | `shrimp_marketplace/static/src/css/shrimp_dark_auto.css` (`.pf-a-*`) |
| Tarjetas de sección y avisos | `shrimp_marketplace/static/src/css/shrimp_product_form.css` (`.pf-sec`, `.pf-alert`) |
| Píldoras de estado, cajas de icono | `shrimp_verification/static/src/css/verification.css` (`.vr-pill`, `.vr-ico`, `.vr-a-ok`) |
| Logo en oscuro | `shrimp_dark.css` (placa clara detrás de `.navbar-brand.logo`) |
| Modo oscuro | `shrimp_marketplace/static/src/js/shrimp_dark_mode.js`, `views/dark_mode_templates.xml` |

## Paleta

| Token (`ColoresMarca`) | CSS | Claro | Oscuro |
| --- | --- | --- | --- |
| `fondo` | `--shrimp-bg` / `--dk-bg` | `#F6F8FA` | `#0C141B` |
| `fondoAlt` | `--shrimp-bg-alt` / `--dk-surface-2` | `#EEF1F4` | `#1C2935` |
| `superficie` | `--shrimp-surface` / `--dk-surface` | `#FFFFFF` | `#16212B` |
| `borde` | `--shrimp-border` / `--dk-border` | `#E4E8EC` | `#2A3B4A` |
| `bordeFuerte` | `--shrimp-border-strong` | `#D3D9DF` | `#385066` |
| `tinta` | `--shrimp-ink` / `--dk-ink` | `#0D1B26` | `#E7EEF4` |
| `tintaSuave` | `--shrimp-ink-soft` | `#55636D` | `#B2C1CD` |
| `tintaTenue` | `--shrimp-ink-faint` | `#7C8794` | `#8FA1B1` |
| `titulo` | `--sp-navy` / `--dk-ink` | `#123E5C` | `#E7EEF4` |
| `marca` (fondo de botón) | `--shrimp-brand` | `#123E5C` | `#2A6F98` |
| `marcaTexto` | `--shrimp-brand` / `--dk-brand-text` | `#123E5C` | `#8CC8EC` |
| `marcaSuave` | `--shrimp-brand-soft` | `#E7EEF3` | `#17324A` |
| `acento` / `acentoTexto` | `--shrimp-teal` / `--dk-teal-text` | `#0E8EA0` | `#0E7F8F` / `#5FD0DC` |
| `acentoSuave` (info) | `--shrimp-teal-soft` | `#E2F3F5` | `#123A40` |
| `exito` / `exitoTexto` / `exitoSuave` | `--shrimp-success*` / `--dk-success-text` | `#1F8F5F` / `#1F8F5F` / `#E4F4EC` | `#1F8F5F` / `#5FD39B` / `#143528` |
| `advertencia` / `advertenciaTexto` / `advertenciaSuave` | `--shrimp-warning*` / `.pf-a-warn` / `--dk-warning-text` | `#B7791F` / `#8A5A00` / `#FBF1DE` | `#B7791F` / `#F0C060` / `#3A2E12` |
| `peligro` / `peligroTexto` / `peligroSuave` | `--shrimp-danger*` / `--dk-danger-text` | `#C0392B` / `#C0392B` / `#FBE9E7` | `#C0392B` / `#FF8A7D` / `#3E1C1A` |
| `campoFondo` / `campoBorde` / `campoFoco` | `--shrimp-input-*` | `#FFFFFF` / `#C3CDD9` / `#0E8EA0` | `#101A23` / `#3A5166` / `#5FD0DC` |

La app sigue la plataforma principal (marketplace, marca navy, acento teal),
que es la que ve cualquier perfil en `/my`. Los colores «de fondo»
(`marca`, `exito`, `peligro`) llevan texto blanco encima; los `*Texto` son
para texto e iconos sobre la superficie (en oscuro, la variante clara del
portal). En el `ColorScheme` oscuro, `primary` es `marcaTexto`; los botones
rellenos usan `marca` con texto blanco, como `.s-btn-primary`.

Banda de marca (igual en los dos modos, como `.sf-banner`): degradado
`#0B2F47 → #123E5C → #2E7DA6`, texto blanco y secundario `#C6DDE8`.

## Componentes

- **Tarjeta (`.pf-sec`)**: superficie, borde 1 px `borde`, radio 16, sombra
  `0 2 6 rgba(13,27,38,.05), 0 12 28 rgba(13,27,38,.07)`; en oscuro sin sombra.
- **Aviso (`.pf-alert`)**: radio 12, borde 1 px, icono + texto 13,5.
  `info` `#E2F3F5`/`#C5E7EC`/`#0B5F6B`; `ok` `#E4F4EC`/`#B6E0C6`/`#17603A`;
  `advertencia` `#FBF1DE`/`#F0D9A8`/`#8A5A00`; `peligro` `#FBE9E7`/`#F1C6C0`/`#8F2A20`
  (fondo/borde/texto). En oscuro: `#123A40`/`#355E64`/`#BEDCE0`,
  `#143528`/`#1F5A40`/`#BEE0D1`, `#3A2E12`/`#6B582E`/`#E0D4BE`,
  `#3E1C1A`/`#6A362F`/`#E1847B`.
- **Píldora (`.vr-pill`)**: píldora (999), 12,5 px en 700, punto de 7 px del
  color del texto. `neutro` (received), `info` (assigned), `advertencia`
  (in_field), `hecho` (done), `ok` (approved), `peligro` (rejected),
  `apagado` (cancelled). `ColoresMarca.tipoDeEstado` traduce los estados del
  servidor.
- **Botones** (radio 10, `--shrimp-radius-sm`; 15,5 px en 600):
  primario `FilledButton` = `marca` + blanco; fantasma `OutlinedButton` =
  superficie + borde `bordeFuerte` + `tinta` (`.s-btn-ghost`); peligro
  `EstilosBoton.peligro` = superficie + borde y texto rojo (`.s-btn-danger`).
  Alto mínimo 52 (botones de bloque) y 48 (todo lo que se pulsa): en campo se
  usa con guantes.
- **Campos**: radio 12, relleno `campoFondo`, borde `campoBorde`, foco 2 px
  `campoFoco`, etiqueta `campoEtiqueta` (`--shrimp-label-ink`).
- **Chips (`.s-cat-chip`)**: píldora con borde; el elegido en `marca` con texto blanco.

Radios: 10 botones, 12 campos y avisos, 16 tarjetas y diálogos, 22 hojas
inferiores. Espaciado en escala de 4 (4, 8, 12, 16, 24, 32).

## Tipografía

Inter (fuente variable, licencia SIL OFL), la misma que sirve el portal
(`inter-var-latin.woff2`). Va empaquetada en `assets/fonts/Inter-Variable.ttf`:
no depende de internet, y `fontWeight` mueve el eje de peso de la fuente
variable. Cuerpo 15, pequeño 13,5, etiqueta 12,5 (`--fs-*`). Títulos en 800
con espaciado negativo (`.vr-ph h1`, `.pf-sec-h h2`).

## Modo día / noche

Como en el portal (botón luna/sol que guarda la elección y, sin elección,
sigue `prefers-color-scheme`): la preferencia es «Como el teléfono» (por
defecto), «Claro» u «Oscuro». Se guarda en el teléfono (`BaseLocal`, clave
`tema` de `config`) y no se borra al cerrar sesión. Se cambia desde el menú
de la cuenta («Tema»), desde el menú del supervisor y, en la pantalla de
entrar, con el botón de la esquina (`lib/datos/preferencia_tema.dart`,
`lib/pantallas/comunes/elegir_tema.dart`).

El logo tiene letras navy sobre transparente: en oscuro `LogoMarca` le pone
la placa clara del portal (`rgba(244,248,251,.94)`, radio proporcional).
