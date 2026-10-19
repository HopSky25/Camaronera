import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../datos/sincronizador.dart';
import '../modelos/verificacion.dart';
import '../seguridad/firmador.dart';
import '../tema.dart';
import 'comunes/widgets.dart';
import 'pantalla_veredicto_no_presentado.dart';

/// Qué pasa si se rechaza por el informe: el vendedor puede pedir una
/// segunda opinión dentro del plazo (salvo que esta ya sea la segunda
/// opinión o que el plazo esté en 0); la compra se cancela cuando el
/// rechazo queda firme.
String textoRechazoPorInforme(Verificacion v) {
  final horas = v.horasImpugnacion;
  if (v.esSegundaOpinion) {
    return 'El producto no cumplió. Esta ya es una segunda opinión: el rechazo queda firme y la compra se cancela.';
  }
  if (horas == 0) return 'El producto no cumplió. El rechazo queda firme y la compra se cancela.';
  final plazo = horas == null ? 'dentro del plazo' : 'en las próximas ${horas == 1 ? 'hora' : '$horas horas'}';
  return 'El producto no cumplió. El vendedor puede pedir una segunda opinión $plazo; '
      'si no la pide, la compra se cancela.';
}

/// Veredicto del técnico responsable (o de la empresa): aprobar, aprobar
/// con observaciones o rechazar por el informe de campo. El técnico de
/// apoyo no llega aquí (no firma). El rechazo por «Producto no presentado»
/// tiene su propia pantalla.
class PantallaVeredicto extends StatefulWidget {
  final String id;
  const PantallaVeredicto({super.key, required this.id});
  @override
  State<PantallaVeredicto> createState() => _PantallaVeredictoState();
}

class _PantallaVeredictoState extends State<PantallaVeredicto> {
  final _form = GlobalKey<FormState>();
  final _campoNotas = GlobalKey<FormFieldState<String>>();
  String _veredicto = 'approve';
  final _notas = TextEditingController();

  @override
  void dispose() {
    _notas.dispose();
    super.dispose();
  }

  void _aviso(String texto, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(texto), backgroundColor: error ? context.colores.peligro : null));
  }

  Future<void> _confirmar() async {
    final form = _form.currentState;
    if (form == null) return;
    if (form.validateGranularly().isNotEmpty) {
      FocusManager.instance.primaryFocus?.unfocus();
      _aviso('Con observaciones o rechazo, escribe la conclusión.', error: true);
      final campo = _campoNotas.currentContext;
      if (campo != null) await Scrollable.ensureVisible(campo, duration: const Duration(milliseconds: 350), alignment: 0.2);
      return;
    }
    final sinc = context.read<Sincronizador>();
    final v = sinc.bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      _aviso('Esta verificación ya no está en tu bandeja.');
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Confirmar veredicto'),
        content: Text(_veredicto == 'reject'
            ? '${textoRechazoPorInforme(v)}\n\nEl honorario lo paga el vendedor. No se puede deshacer desde la app.'
            : 'Se cerrará la verificación con este veredicto y comprador y vendedor revisarán el informe.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Volver')),
          FilledButton(
            style: _veredicto == 'reject' ? EstilosBoton.peligro(c) : null,
            onPressed: () => Navigator.pop(c, true),
            child: Text(_veredicto == 'reject' ? 'Rechazar' : 'Confirmar'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final faltan = await sinc.darVeredicto(v, _veredicto, _notas.text);
      if (faltan.isNotEmpty) {
        if (!mounted) return;
        await showDialog<void>(
          context: context,
          builder: (c) => AlertDialog(
            title: const Text('Falta completar el informe'),
            content: Text('El servidor no admite la aprobación sin estos datos:\n\n• ${faltan.join('\n• ')}\n\n'
                'Complétalos en "Informe de campo" y vuelve a intentarlo.'),
            actions: [FilledButton(onPressed: () => Navigator.pop(c), child: const Text('Entendido'))],
          ),
        );
        return;
      }
    } catch (e) {
      _aviso(e is ErrorSeguridad ? e.mensaje : 'No se pudo guardar el veredicto. Inténtalo otra vez.', error: true);
      return;
    }
    if (!mounted) return;
    _aviso('Veredicto guardado; se enviará con internet');
    Navigator.popUntil(context, (r) => r.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final v = context.watch<Sincronizador>().bandeja.where((e) => e.id == widget.id).firstOrNull;
    if (v == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Veredicto')),
        body: const VacioLista(texto: 'Esta verificación ya no está en tu bandeja.'),
      );
    }
    if (!v.puedoDictaminar) {
      return Scaffold(
        appBar: AppBar(title: const Text('Veredicto')),
        body: Padding(padding: const EdgeInsets.all(16), child: AvisoApoyo(v: v)),
      );
    }
    return Scaffold(
      appBar: AppBar(title: const Text('Veredicto')),
      body: Form(
        key: _form,
        autovalidateMode: AutovalidateMode.onUserInteraction,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 32),
          children: [
            const Rotulo('Resultado de la inspección'),
            Tarjeta(
              padding: EdgeInsets.zero,
              child: RadioGroup<String>(
                groupValue: _veredicto,
                onChanged: (e) {
                  setState(() => _veredicto = e ?? 'approve');
                  // Cambia si la conclusión es obligatoria: se vuelve a mirar el campo.
                  if (_notas.text.isNotEmpty) _campoNotas.currentState?.validate();
                },
                child: Column(children: [
                  const RadioListTile(
                      value: 'approve',
                      title: Text('Aprobar'),
                      subtitle: Text('Cumple lo publicado. Comprador y vendedor revisan el informe.')),
                  const RadioListTile(
                      value: 'approve_obs',
                      title: Text('Aprobar con observaciones'),
                      subtitle: Text('Cumple, pero hay diferencias que dejar por escrito')),
                  RadioListTile(value: 'reject', title: const Text('Rechazar'), subtitle: Text(textoRechazoPorInforme(v))),
                ]),
              ),
            ),
            const Rotulo('Conclusión del verificador'),
            TextFormField(
              key: _campoNotas,
              controller: _notas,
              maxLines: 4,
              decoration: InputDecoration(
                labelText: _veredicto == 'approve' ? 'Observaciones (opcional)' : 'Conclusión (obligatoria)',
                helperText: 'Con observaciones o rechazo es lo que ve la parte afectada.',
                helperMaxLines: 2,
              ),
              validator: (t) => _veredicto != 'approve' && (t ?? '').trim().isEmpty ? 'Escribe por qué' : null,
            ),
            const SizedBox(height: 24),
            BotonAccion(
              estilo: _veredicto == 'reject' ? EstilosBoton.peligro(context) : null,
              onPressed: _confirmar,
              icono: Icons.gavel_rounded,
              texto: 'Firmar y emitir veredicto',
              textoOcupado: 'Guardando…',
            ),
            if (admiteNoPresentado(v)) ...[
              const SizedBox(height: 12),
              TextButton.icon(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => PantallaNoPresentado(id: v.id))),
                icon: const Icon(Icons.visibility_off_outlined),
                label: const Text('¿El producto no se presentó en la planta?'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Aviso para el técnico de apoyo: carga datos, pero no firma.
class AvisoApoyo extends StatelessWidget {
  final Verificacion v;
  const AvisoApoyo({super.key, required this.v});

  @override
  Widget build(BuildContext context) => CajaAviso(
        tipo: TipoAviso.info,
        icono: Icons.lock_outline,
        texto: 'Vas de apoyo: puedes iniciar el trabajo de campo, cargar el informe y tomar fotos, pero el veredicto lo firma '
            '${v.tecnico?.nombre ?? 'el técnico responsable'} o la empresa.',
      );
}
