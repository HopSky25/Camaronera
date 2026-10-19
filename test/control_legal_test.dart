import 'package:flutter_test/flutter_test.dart';
import 'package:verificador_campo/datos/api_cliente.dart';
import 'package:verificador_campo/datos/control_legal.dart';

ErrorApi _error403() => const ErrorApi(
      403,
      'Prohibido',
      'Hay versiones nuevas de documentos legales que se deben aceptar.',
      'legal-acceptance-required',
      datos: {
        'code': 'legal_acceptance_required',
        'pending_documents': ['Política de privacidad', 'Términos y condiciones'],
        'accept_url': 'https://demo.camaronmarket.com/my/legal/accept',
      },
    );

void main() {
  test('el 403 legal-acceptance-required se reconoce', () {
    final e = _error403();
    expect(e.pideAceptarDocumentos, isTrue);
    expect(e.mensaje, contains('documentos legales'));
  });

  test('un 403 lo activa y GET /me lo apaga al aceptar', () async {
    var me = <String, dynamic>{
      'legal': {'acceptance_required': true, 'pending_documents': ['Términos y condiciones'], 'accept_url': 'https://x/acc'},
    };
    final cl = ControlLegal(consultarMe: () => () async => me, esSupervisor: () => false);
    var avisos = 0;
    cl.addListener(() => avisos++);
    cl.alRecibir(_error403());
    expect(cl.pendiente, isTrue);
    expect(cl.documentos, ['Política de privacidad', 'Términos y condiciones']);
    expect(cl.urlAceptar, 'https://demo.camaronmarket.com/my/legal/accept');
    await cl.comprobar();
    expect(cl.pendiente, isTrue, reason: 'el servidor dice que aún falta uno');
    expect(cl.documentos, ['Términos y condiciones']);
    me = {
      'legal': {'acceptance_required': false, 'pending_documents': <String>[]},
    };
    await cl.comprobar();
    expect(cl.pendiente, isFalse);
    expect(avisos, greaterThanOrEqualTo(2));
  });

  test('en modo supervisor no se muestra', () {
    final cl = ControlLegal(consultarMe: () => null, esSupervisor: () => true);
    cl.alRecibir(_error403());
    expect(cl.pendiente, isFalse);
  });

  test('sin sesión no queda pendiente y limpiar lo apaga', () async {
    final cl = ControlLegal(consultarMe: () => null, esSupervisor: () => false);
    cl.alRecibir(_error403());
    expect(cl.pendiente, isTrue);
    cl.limpiar();
    expect(cl.pendiente, isFalse);
    cl.alRecibir(_error403());
    await cl.comprobar();
    expect(cl.pendiente, isFalse, reason: 'sin sesión no hay nada que aceptar');
  });

  test('sin señal se queda como estaba', () async {
    final cl = ControlLegal(consultarMe: () => () async => throw SinConexion(), esSupervisor: () => false);
    cl.alRecibir(_error403());
    await cl.comprobar();
    expect(cl.pendiente, isTrue);
  });
}
