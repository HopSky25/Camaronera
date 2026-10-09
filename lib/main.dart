import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'datos/sincronizador.dart';
import 'pantallas/pantalla_bandeja.dart';
import 'pantallas/pantalla_clave.dart';
import 'tema.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
  ));
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
        theme: temaCamaronMarket(),
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
