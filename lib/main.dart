import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'datos/sincronizador.dart';
import 'pantallas/pantalla_bandeja.dart';
import 'pantallas/pantalla_clave.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const AppVerificador());
}

class AppVerificador extends StatelessWidget {
  const AppVerificador({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => Sincronizador()..iniciar(),
      child: MaterialApp(
        title: 'CamaronMarket Verificador',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF0B6E8F)),
          useMaterial3: true,
          // Pantallas de campo: controles grandes, fáciles con el sol y guantes.
          visualDensity: VisualDensity.comfortable,
          inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder(), isDense: false),
        ),
        home: const _Raiz(),
      ),
    );
  }
}

class _Raiz extends StatelessWidget {
  const _Raiz();

  @override
  Widget build(BuildContext context) {
    final sinc = context.watch<Sincronizador>();
    return sinc.configurado ? const PantallaBandeja() : const PantallaClave();
  }
}
