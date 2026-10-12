import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'datos/repositorio.dart';
import 'datos/sesion.dart';
import 'datos/sincronizador.dart';
import 'modelos/perfil.dart';
import 'pantallas/empacadora/panel_empacadora.dart';
import 'pantallas/inicio/pantalla_elegir_perfil.dart';
import 'pantallas/inicio/pantalla_inicio_sesion.dart';
import 'pantallas/maquilador/panel_maquilador.dart';
import 'pantallas/pantalla_bandeja.dart';
import 'pantallas/vendedor/panel_vendedor.dart';
import 'tema.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
  ));
  runApp(const AppCamaronMarket());
}

/// La app de CamaronMarket para todos los perfiles: una sola app, un panel
/// por perfil. El técnico de campo conserva su flujo sin internet.
class AppCamaronMarket extends StatelessWidget {
  const AppCamaronMarket({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => Sesion()..iniciar()),
        ChangeNotifierProvider(create: (c) => Sincronizador(sesion: c.read<Sesion>())..iniciar()),
        Provider(create: (c) => Repositorio(sesion: c.read<Sesion>())),
      ],
      child: MaterialApp(
        title: 'CamaronMarket',
        debugShowCheckedModeBanner: false,
        theme: temaCamaronMarket(),
        home: const _Raiz(),
      ),
    );
  }
}

class _Raiz extends StatefulWidget {
  const _Raiz();
  @override
  State<_Raiz> createState() => _RaizState();
}

class _RaizState extends State<_Raiz> {
  String? _clave;

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<Sesion>();
    // Si la sesión caduca o se cambia de perfil estando en una pantalla
    // interior, se vuelve a la raíz para que no quede nada encima.
    final clave = '${sesion.conectado}/${sesion.perfil}';
    if (_clave != null && _clave != clave) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) Navigator.of(context).popUntil((r) => r.isFirst);
      });
    }
    _clave = clave;
    if (sesion.cargando) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (!sesion.conectado) return const PantallaInicioSesion();
    if (sesion.necesitaElegirPerfil) return const PantallaElegirPerfil();
    final perfil = sesion.perfil!;
    if (perfil == perfilTecnico) return const PantallaBandeja(titulo: 'Mis verificaciones');
    if (perfil == 'verificador') return const PantallaBandeja(titulo: 'Bandeja de la verificadora');
    if (perfil == 'empacadora') return const PanelEmpacadora();
    if (perfil == 'maquilador') return const PanelMaquilador();
    // semillero, laboratorio y camaronera venden: mismo panel.
    return const PanelVendedor();
  }
}
