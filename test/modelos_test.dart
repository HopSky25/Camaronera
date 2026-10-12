// Parseo de los modelos a partir del JSON real de la API (formas tomadas de
// shrimp_api/controllers/serializers.py). Odoo manda `false` en los campos
// de texto vacíos y 0.0 en los numéricos sin dato: nada de eso debe romper.
import 'package:flutter_test/flutter_test.dart';
import 'package:verificador_campo/modelos/perfil.dart';
import 'package:verificador_campo/modelos/verificacion.dart';
import 'package:verificador_campo/pantallas/comunes/formato.dart' as f;

import 'datos_api.dart';

void main() {
  group('Yo (GET /me)', () {
    test('cuenta con varios perfiles', () {
      final yo = Yo.desde(meCamaronera());
      expect(yo.id, 'a1b2c3d4-0000-4000-8000-000000000001');
      expect(yo.nombre, 'Camaronera Las Mareas');
      expect(yo.tipo, 'camaronera');
      expect(yo.correo, 'socio@finca.ec');
      expect(yo.esTecnicoDeCampo, isFalse);
      expect(yo.empresa, isNull);
      expect(yo.perfilActivo, 'camaronera');
      expect(yo.perfiles.map((p) => p.rol), ['camaronera', 'empacadora', 'maquilador']);
      expect(yo.aprobados.map((p) => p.rol), ['camaronera', 'empacadora']);
      expect(yo.perfiles.first.activo, isTrue);
      expect(yo.perfiles.last.aprobado, isFalse);
      expect(yo.esTecnico, isFalse);
    });

    test('técnico de campo: contacto de la verificadora', () {
      final yo = Yo.desde(meTecnico());
      expect(yo.esTecnico, isTrue);
      expect(yo.empresa?.nombre, 'Verificadora del Golfo');
      expect(yo.perfilActivo, 'verificador');
    });

    test('tolera false y null donde debería haber texto', () {
      final yo = Yo.desde({'id': 'x', 'name': 'N', 'type': false, 'email': null, 'roles': [
        {'role': 'camaronera', 'label': false, 'state': 'approved', 'active': true},
      ]});
      expect(yo.tipo, isNull);
      expect(yo.correo, isNull);
      expect(yo.perfiles.single.etiqueta, 'Camaronera');
    });
  });

  group('Verificacion (GET /verifications)', () {
    test('elemento de lista', () {
      final v = Verificacion.desde(verificacionLista());
      expect(v.id, '6f1c0000-0000-4000-8000-00000000000a');
      expect(v.referencia, 'VER/2026/0042');
      expect(v.estado, 'assigned');
      expect(v.miRol, 'technician');
      expect(v.producto.nombre, 'Camarón 21/25 piscina 3');
      expect(v.comprador.nombre, 'Empacadora Pacífico');
      expect(v.vendedor.nombre, 'Camaronera Las Mareas');
      expect(v.tecnico?.nombre, 'Juan Pérez');
      expect(v.tecnicoAsignado, isTrue);
      expect(v.modo, 'platform');
      expect(v.despachoId, '9a1d0000-0000-4000-8000-000000000001');
      expect(v.despachoEta, DateTime.parse('2026-10-01T14:30:00Z').toLocal());
      expect(v.estadoAceptacion, 'na');
      expect(v.plazoAceptacion, isNull);
      expect(v.tieneDetalle, isFalse);
      expect(v.alcance, 'shrimp');
      expect(v.esLarva, isFalse);
      expect(v.puedeIniciarCampo, isTrue);
      expect(v.cerrada, isFalse);
      expect(v.estadoEtiqueta, 'Asignada');
    });

    test('transaction false, technician null y dispatch null no rompen', () {
      final json = verificacionLista()
        ..['transaction'] = false
        ..['technician'] = null
        ..['technician_assigned'] = false
        ..['dispatch'] = null
        ..['dispatch_eta'] = null;
      final v = Verificacion.desde(json);
      expect(v.transaccionId, isNull);
      expect(v.tecnico, isNull);
      expect(v.tecnicoAsignado, isFalse);
      expect(v.despachoId, isNull);
      expect(v.despachoEta, isNull);
    });

    test('detalle: posturas y decisión pendiente', () {
      final v = Verificacion.desde(verificacionDetalle());
      expect(v.tieneDetalle, isTrue);
      expect(v.piscina, 'Piscina 3');
      expect(v.instalacion, 'Finca El Morro');
      expect(v.loteCodigo, 'LM-2026-17');
      expect(v.estadoAceptacion, 'waiting');
      expect(v.decisionDe('buyer'), 'pending');
      expect(v.decisionDe('seller'), 'accepted');
      expect(v.puedoDecidir, isTrue, reason: 'my_role=buyer y el comprador sigue pendiente');
      expect(v.aceptacionEtiqueta, 'Esperando a las partes');
      expect(Verificacion.tieneId(verificacionDetalle()), isTrue);
      expect(Verificacion.tieneId({'id': false}), isFalse);
      expect(Verificacion.tieneId({}), isFalse);
    });

    test('pond sin registro: {"id": null, "name": etiqueta}', () {
      final json = verificacionDetalle()..['pond'] = {'id': null, 'name': 'Piscina vieja'};
      expect(Verificacion.desde(json).piscina, 'Piscina vieja');
    });
  });

  group('InformeCampo', () {
    test('desde el detalle del servidor: ceros y false no se precargan', () {
      final inf = InformeCampo.desdeServidor(verificacionDetalle());
      expect(inf.plantaNombre, 'Planta Pacífico');
      expect(inf.librasEnviadas, 1500.0);
      expect(inf.librasPlanta, 1480.5);
      expect(inf.librasBasura, isNull, reason: 'trash_lb 0.0 es "sin dato"');
      expect(inf.presentacion, 'entero');
      expect(inf.metabisulfitoPpm, 85.0);
      expect(inf.metabisulfitoNotas, isNull, reason: 'notes null');
      expect(inf.sabor, 'good');
      expect(inf.gramosFinca, 18.2);
      expect(inf.gramosPlanta2, isNull);
      expect(inf.incidencias, isNull);
      expect(inf.lineas.length, 2);
      expect(inf.lineas.first.clase, 'a');
      expect(inf.lineas.first.talla, '21/25');
      expect(inf.lineas.first.libras, 1200.0);
      expect(inf.lineas.last.talla, '', reason: 'size_code false → vacío');
      expect(inf.conteos.length, 1);
      expect(inf.conteos.first.valor, 23.0);
      expect(inf.conteos.first.nota, isNull);
    });

    test('desde un detalle sin informe', () {
      final inf = InformeCampo.desdeServidor({'id': 'x', 'weights': null, 'classification': null, 'counts': null});
      expect(inf.librasEnviadas, isNull);
      expect(inf.lineas, isEmpty);
      expect(inf.conteos, isEmpty);
    });

    test('ida y vuelta por la base local', () {
      final inf = InformeCampo.desdeServidor(verificacionDetalle())
        ..latitud = -2.19
        ..longitud = -79.88
        ..incidencias = 'Camión llegó tarde';
      final copia = InformeCampo.desdeJsonLocal(inf.aJsonLocal());
      expect(copia.aJsonLocal(), inf.aJsonLocal());
      expect(copia.latitud, -2.19);
      expect(copia.lineas.length, 2);
    });

    test('cuerpo del PATCH: solo lo que tiene valor', () {
      final inf = InformeCampo(librasEnviadas: 1500, librasPlanta: 1480.5, presentacion: 'entero', incidencias: '   ',
          lineas: [LineaClasificacion(clase: 'a', talla: '21/25', libras: 1200)], conteos: [Conteo(valor: 23, nota: '')]);
      final cuerpo = inf.aCuerpoApi();
      expect(cuerpo, {
        'weight_sent_lb': 1500.0,
        'weight_plant_lb': 1480.5,
        'presentation': 'entero',
        'lines': [
          {'quality_class': 'a', 'size_code': '21/25', 'weight_lb': 1200.0}
        ],
        'counts': [
          {'value': 23.0}
        ],
      });
      expect(cuerpo.containsKey('incident_notes'), isFalse);
      expect(cuerpo.containsKey('trash_lb'), isFalse);
    });

    test('qué falta para el veredicto (camarón)', () {
      final completo = InformeCampo.desdeServidor(verificacionDetalle());
      expect(completo.faltantesParaVeredicto(larva: false), isEmpty);
      final vacio = InformeCampo();
      expect(vacio.faltantesParaVeredicto(larva: false),
          ['peso en planta', 'presentación (entero o cola)', 'metabisulfito (ppm)', 'clasificación por tallas', 'resultado de sabor']);
      final sinSabor = InformeCampo.desdeServidor(verificacionDetalle())..sabor = null;
      expect(sinSabor.faltantesParaVeredicto(larva: false), ['resultado de sabor']);
    });

    test('qué falta para el veredicto (larva) y lectura de larvae', () {
      final json = verificacionDetalle()
        ..['scope'] = 'larvae'
        ..['larvae'] = {'qty_verified': 1000000.0, 'survival_rate': 92.5, 'avg_size_mg': 3.4, 'health_status': 'good', 'health_notes': null,
          'qty_diff_pct': 0.0, 'survival_diff_pp': 0.0};
      final v = Verificacion.desde(json);
      expect(v.esLarva, isTrue);
      final inf = InformeCampo.desdeServidor(json);
      expect(inf.larvaCantidad, 1000000.0);
      expect(inf.larvaSupervivencia, 92.5);
      expect(inf.larvaSanidad, 'good');
      expect(inf.faltantesParaVeredicto(larva: true), isEmpty);
      expect(InformeCampo().faltantesParaVeredicto(larva: true),
          ['cantidad verificada', 'supervivencia medida', 'tamaño promedio', 'estado sanitario']);
      final copia = InformeCampo.desdeJsonLocal(inf.aJsonLocal());
      expect(copia.larvaTamanoMg, 3.4);
    });

    test('iniciar campo solo con técnico asignado', () {
      expect(Verificacion.desde(verificacionLista()..['state'] = 'received').puedeIniciarCampo, isFalse);
      expect(Verificacion.desde(verificacionLista()..['state'] = 'received').esperaTecnico, isTrue);
      expect(Verificacion.desde(verificacionLista()..['state'] = 'assigned').puedeIniciarCampo, isTrue);
      expect(Verificacion.desde(verificacionLista()..['state'] = 'in_field').puedeIniciarCampo, isFalse);
    });
  });

  group('formato', () {
    test('fechas ISO de la API a hora local', () {
      final d = f.aFecha('2026-10-01T14:30:00Z');
      expect(d, DateTime.utc(2026, 10, 1, 14, 30).toLocal());
      expect(f.aFecha('2026-10-01'), DateTime(2026, 10, 1));
      expect(f.aFecha(null), isNull);
      expect(f.aFecha(false), isNull);
      expect(f.fecha(false), '—');
    });

    test('dinero, cantidades y referencias', () {
      expect(f.dinero({'amount': 2.75, 'currency': 'USD'}), r'$ 2.75');
      expect(f.dinero(null), '—');
      expect(f.cantidad(1500.0, {'id': 'u', 'name': 'lb'}), '1,500 lb');
      expect(f.nombreDe({'id': 'x', 'name': 'Finca'}), 'Finca');
      expect(f.nombreDe(null), '—');
      expect(f.libras(false), '—');
      expect(f.porcentaje(87.456), '87.5 %');
    });

    test('fechaEntre acota al rango del selector', () {
      final min = DateTime(2026, 1, 1);
      final max = DateTime(2026, 12, 31);
      expect(f.fechaEntre(DateTime(2025, 6, 1), min, max), min);
      expect(f.fechaEntre(DateTime(2027, 6, 1), min, max), max);
      expect(f.fechaEntre(DateTime(2026, 6, 1), min, max), DateTime(2026, 6, 1));
    });
  });

  group('Perfil', () {
    test('nombres y descripciones', () {
      expect(nombreDePerfil('verificador'), 'Verificadora');
      expect(nombreDePerfil(perfilTecnico), 'Técnico de campo');
      expect(perfilVende('laboratorio'), isTrue);
      expect(perfilVende('empacadora'), isFalse);
    });
  });
}
