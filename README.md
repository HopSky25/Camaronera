# CamaronMarket · Verificador de campo

App Android (Flutter) para el técnico de la empresa verificadora. Trabaja **sin internet**:
todo lo que se captura queda en el teléfono y se envía al servidor cuando vuelve la señal.

## Cómo está armada

- `lib/modelos/` — la verificación tal como la devuelve la API, el informe de campo y las
  operaciones pendientes de envío.
- `lib/datos/base_local.dart` — base SQLite en el teléfono (bandeja, informes, cola, fotos).
- `lib/datos/api_cliente.dart` — cliente de `https://<servidor>/api/v1` con clave Bearer e
  `Idempotency-Key` en cada envío.
- `lib/datos/sincronizador.dart` — baja la bandeja del técnico (`GET /verifications?role=technician`)
  y sube la cola en orden: inicio de campo, informe, veredicto, llegada del camión.
- `lib/pantallas/` — clave de API, bandeja, detalle, informe de campo, veredicto, fotos, pendientes.

## Compilar

```powershell
flutter pub get
flutter build apk --release
```

El APK queda en `build\app\outputs\flutter-apk\app-release.apk`.

## Pendiente

- Subida de fotos: la API todavía no recibe archivos; se guardan en el teléfono.
- Firma de release propia (hoy usa la firma de depuración) antes de publicar en Play Store.
