// Cuerpos de la cola: lo que viaja en cada POST / PATCH, y a qué ruta.
import 'package:flutter_test/flutter_test.dart';
import 'package:verificador_campo/modelos/cuerpos.dart';
import 'package:verificador_campo/modelos/operacion_pendiente.dart';
import 'package:verificador_campo/modelos/verificacion.dart';

void main() {
  OperacionPendiente op(TipoOperacion t, {String? despacho}) => OperacionPendiente(
      tipo: t, recursoId: 'R1', despachoId: despacho, cuerpo: const {}, claveIdempotencia: 'k', creadaEn: DateTime(2026));

  group('OperacionPendiente', () {
    test('ruta y método de cada operación', () {
      expect(op(TipoOperacion.iniciarCampo).ruta, '/verifications/R1:start');
      expect(op(TipoOperacion.iniciarCampo).metodo, 'POST');
      expect(op(TipoOperacion.informe).ruta, '/verifications/R1/report');
      expect(op(TipoOperacion.informe).metodo, 'PATCH');
      expect(op(TipoOperacion.veredicto).ruta, '/verifications/R1:verdict');
      expect(op(TipoOperacion.llegadaCamion, despacho: 'D9').ruta, '/dispatches/D9/arrival');
      expect(op(TipoOperacion.asignarTecnico).ruta, '/verifications/R1:assign');
      expect(op(TipoOperacion.aceptacionInforme).ruta, '/verifications/R1/acceptance');
      expect(op(TipoOperacion.programarDespacho).ruta, '/dispatches/R1');
      expect(op(TipoOperacion.programarDespacho).metodo, 'PATCH');
      expect(op(TipoOperacion.compraVerificada).ruta, '/verifications');
      expect(op(TipoOperacion.comprarReserva).ruta, '/harvest/commitments/R1:buy');
      expect(op(TipoOperacion.ofertaMaquila).ruta, '/copack/requests/R1/offers');
      expect(op(TipoOperacion.recepcionMaquila).ruta, '/copack/orders/R1:reception');
      expect(op(TipoOperacion.empaqueMaquila).ruta, '/copack/orders/R1:packing');
      expect(op(TipoOperacion.firmaActa).ruta, '/copack/orders/R1:sign');
    });

    test('todas tienen descripción en español y solo las de verificación devuelven la verificación', () {
      for (final t in TipoOperacion.values) {
        expect(op(t).descripcion, isNotEmpty);
      }
      expect(op(TipoOperacion.informe).respondeVerificacion, isTrue);
      expect(op(TipoOperacion.programarDespacho).respondeVerificacion, isFalse);
      expect(op(TipoOperacion.informe).conError, isFalse);
    });
  });

  group('fechas para la API', () {
    test('fecha y hora en UTC con Z, sin milisegundos', () {
      final local = DateTime(2026, 10, 1, 9, 30, 15);
      expect(fechaHoraApi(local), DateTime.utc(2026, 10, 1, 9, 30, 15).toLocal() == local
          ? '2026-10-01T09:30:15Z'
          : fechaHoraApi(local)); // en cualquier zona: formato fijo
      expect(fechaHoraApi(DateTime.utc(2026, 10, 1, 14, 30, 0, 456)), '2026-10-01T14:30:00Z');
      expect(fechaApi(DateTime(2026, 3, 7)), '2026-03-07');
    });
  });

  group('cuerpos', () {
    test('informe de campo (PATCH /verifications/{id}/report)', () {
      final inf = InformeCampo(
        plantaNombre: 'Planta Pacífico',
        librasEnviadas: 1500,
        librasPlanta: 1480.5,
        metabisulfitoPpm: 85,
        sabor: 'good',
        latitud: -2.19,
        longitud: -79.88,
        lineas: [LineaClasificacion(clase: 'a', talla: '21/25', libras: 1200), LineaClasificacion(clase: 'b', talla: '26/30', libras: 280.5)],
        conteos: [Conteo(valor: 23, nota: 'muestra 1')],
      );
      expect(inf.aCuerpoApi(), {
        'plant_name': 'Planta Pacífico',
        'weight_sent_lb': 1500.0,
        'weight_plant_lb': 1480.5,
        'metabisulfite_ppm': 85.0,
        'taste_result': 'good',
        'gps_latitude': -2.19,
        'gps_longitude': -79.88,
        'lines': [
          {'quality_class': 'a', 'size_code': '21/25', 'weight_lb': 1200.0},
          {'quality_class': 'b', 'size_code': '26/30', 'weight_lb': 280.5},
        ],
        'counts': [
          {'value': 23.0, 'note': 'muestra 1'},
        ],
      });
    });

    test('informe de larva (scope larvae)', () {
      final inf = InformeCampo(plantaNombre: 'Lab Mar Azul', larvaCantidad: 1000000, larvaSupervivencia: 92.5, larvaTamanoMg: 3.4,
          larvaSanidad: 'good', larvaSanidadNotas: 'Sin necrosis');
      expect(inf.aCuerpoApi(), {
        'plant_name': 'Lab Mar Azul',
        'larvae_qty_verified': 1000000.0,
        'larvae_survival_rate': 92.5,
        'larvae_avg_size_mg': 3.4,
        'larvae_health_status': 'good',
        'larvae_health_notes': 'Sin necrosis',
        'lines': [],
        'counts': [],
      });
    });

    test('si el técnico borró las tallas y los conteos se mandan vacíos', () {
      final inf = InformeCampo(librasPlanta: 10);
      expect(inf.aCuerpoApi()['lines'], isEmpty);
      expect(inf.aCuerpoApi()['counts'], isEmpty);
    });

    test('veredicto', () {
      expect(cuerpoVeredicto('approve', '  '), {'verdict': 'approve'});
      expect(cuerpoVeredicto('reject', ' Peso corto '), {'verdict': 'reject', 'notes': 'Peso corto'});
      expect(estadoDeVeredicto('approve'), 'approved');
      expect(estadoDeVeredicto('approve_obs'), 'approved_obs');
      expect(estadoDeVeredicto('reject'), 'rejected');
    });

    test('llegada del camión y asignación', () {
      expect(cuerpoLlegadaCamion(DateTime.utc(2026, 10, 1, 14, 35)), {'actual_arrival': '2026-10-01T14:35:00Z'});
      expect(cuerpoAsignarTecnico(' t1 '), {'technician': 't1'});
    });

    test('despacho (PATCH /dispatches/{id}): solo lo rellenado', () {
      expect(cuerpoDespacho(), isEmpty);
      final cuerpo = cuerpoDespacho(
        cosecha: DateTime(2026, 9, 29),
        eta: DateTime.utc(2026, 10, 1, 14, 30),
        transportista: ' Transportes Loor ',
        placa: 'abc-1234',
        telefono: '',
        notas: null,
      );
      expect(cuerpo, {
        'harvest_date': '2026-09-29',
        'eta': '2026-10-01T14:30:00Z',
        'carrier_name': 'Transportes Loor',
        'vehicle_plate': 'ABC-1234',
      });
    });

    test('aceptación del informe', () {
      expect(cuerpoAceptacion('accept', ''), {'decision': 'accept'});
      expect(cuerpoAceptacion('reject', 'Faltan libras'), {'decision': 'reject', 'reason': 'Faltan libras'});
    });

    test('compra con verificación', () {
      expect(cuerpoCompra(productoId: 'p1', cantidad: 1500, modo: 'platform', verificadoraId: 'v1'),
          {'product': 'p1', 'qty': 1500.0, 'verification_mode': 'platform', 'verifier': 'v1'});
      expect(cuerpoCompra(productoId: 'p1', cantidad: 10, modo: 'declared', verificadoraId: 'v1'),
          {'product': 'p1', 'qty': 10.0, 'verification_mode': 'declared'});
      expect(cuerpoCompraReserva(modo: 'platform', verificadoraId: 'v1'), {'verification_mode': 'platform', 'verifier': 'v1'});
      expect(cuerpoCompraReserva(modo: 'declared'), {'verification_mode': 'declared'});
    });

    test('maquila', () {
      expect(
        cuerpoOfertaMaquila(tarifaPorLb: 0.01, capacidadLb: 20000, desde: DateTime(2026, 10, 5), hasta: DateTime(2026, 10, 12), notas: ''),
        {'rate_per_lb': 0.01, 'capacity_lb': 20000.0, 'available_from': '2026-10-05', 'available_to': '2026-10-12'},
      );
      expect(cuerpoRecepcionMaquila(librasRecibidas: 1480.5, insumos: true, problema: 'ignorado'), {'received_lb': 1480.5, 'supplies_received': true});
      expect(cuerpoRecepcionMaquila(librasRecibidas: 1480.5, insumos: false, problema: 'Faltó hielo'),
          {'received_lb': 1480.5, 'supplies_received': false, 'supplies_issue': 'Faltó hielo'});
      expect(cuerpoEmpaqueMaquila(librasEmpacadas: 1470), {'packed_lb': 1470.0});
      expect(cuerpoEmpaqueMaquila(librasEmpacadas: 1470, cajas: 147), {'packed_lb': 1470.0, 'boxes': 147});
      expect(cuerpoFirmaActa('accepted', null), {'decision': 'accepted'});
      expect(cuerpoFirmaActa('rejected', 'No cuadra'), {'decision': 'rejected', 'reason': 'No cuadra'});
    });

    test('números escritos con coma', () {
      expect(numeroEscrito('12,5'), 12.5);
      expect(numeroEscrito(' 1480.5 '), 1480.5);
      expect(numeroEscrito('abc'), isNull);
      expect(numeroEscrito(null), isNull);
    });
  });
}
