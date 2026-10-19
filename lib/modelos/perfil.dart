/// Quién soy y con qué perfiles puedo trabajar. Es el JSON de `GET /me`
/// (serializers.py `me()`): `active_role`, `roles`, `name`, `type`, `email`,
/// `is_field_technician`, `company`...
library;

import 'package:flutter/material.dart';

import 'verificacion.dart';

/// Perfil interno de la app para el técnico de campo: no es un rol de la
/// plataforma, pero la app lo trata como una forma más de trabajar.
const perfilTecnico = 'tecnico';

class Perfil {
  final String rol;
  final String etiqueta;

  /// pending | approved | ...
  final String estado;
  final bool activo;
  const Perfil({required this.rol, required this.etiqueta, required this.estado, required this.activo});

  bool get aprobado => estado == 'approved';

  factory Perfil.desde(Map json) {
    final rol = jsonTexto(json['role']) ?? '';
    return Perfil(
      rol: rol,
      etiqueta: jsonTexto(json['label']) ?? nombreDePerfil(rol),
      estado: jsonTexto(json['state']) ?? '',
      activo: json['active'] == true,
    );
  }

  Map<String, dynamic> aJson() => {'role': rol, 'label': etiqueta, 'state': estado, 'active': activo};
}

String nombreDePerfil(String rol) => switch (rol) {
      'semillero' => 'Naupliero',
      'laboratorio' => 'Laboratorio',
      'camaronera' => 'Camaronera',
      'empacadora' => 'Empacadora',
      'maquilador' => 'Maquilador',
      'verificador' => 'Verificadora',
      perfilTecnico => 'Técnico de campo',
      _ => rol,
    };

IconData iconoDePerfil(String rol) => switch (rol) {
      'semillero' => Icons.egg_outlined,
      'laboratorio' => Icons.science_outlined,
      'camaronera' => Icons.water_outlined,
      'empacadora' => Icons.factory_outlined,
      'maquilador' => Icons.inventory_2_outlined,
      'verificador' => Icons.verified_outlined,
      perfilTecnico => Icons.engineering_outlined,
      _ => Icons.person_outline,
    };

/// Qué hace cada perfil, en una frase para la pantalla de elección.
String descripcionDePerfil(String rol) => switch (rol) {
      'semillero' => 'Vendes nauplio: lotes, ventas e informes',
      'laboratorio' => 'Vendes larva: lotes, ventas e informes',
      'camaronera' => 'Vendes camarón: lotes, ventas, informes y despacho',
      'empacadora' => 'Compras: informes, llegadas y ranking de proveedores',
      'maquilador' => 'Servicio de empaque: solicitudes, órdenes y tarifas',
      'verificador' => 'Bandeja de verificaciones y asignación de técnicos',
      perfilTecnico => 'Inspección en campo, con o sin señal',
      _ => '',
    };

/// Los perfiles que venden (semillero, laboratorio, camaronera) comparten panel.
bool perfilVende(String rol) => rol == 'semillero' || rol == 'laboratorio' || rol == 'camaronera';

class Yo {
  final String id;
  final String nombre;
  final String? tipo;
  final String? correo;
  final bool esTecnicoDeCampo;
  final Referencia? empresa;
  final String? perfilActivo;
  final List<Perfil> perfiles;

  /// Usuario interno con el grupo "Supervisor de la app" (Anexo B del
  /// contrato): su clave solo sirve para el directorio y "actuar como".
  final bool esSupervisor;
  final Map<String, dynamic> crudo;

  const Yo({
    required this.id,
    required this.nombre,
    required this.tipo,
    required this.correo,
    required this.esTecnicoDeCampo,
    required this.empresa,
    required this.perfilActivo,
    required this.perfiles,
    this.esSupervisor = false,
    required this.crudo,
  });

  factory Yo.desde(Map<String, dynamic> json) => Yo(
        id: jsonTexto(json['id']) ?? '',
        nombre: jsonTexto(json['name']) ?? '',
        tipo: jsonTexto(json['type']),
        correo: jsonTexto(json['email']),
        esTecnicoDeCampo: json['is_field_technician'] == true,
        empresa: json['company'] is Map ? Referencia.desde(json['company']) : null,
        perfilActivo: jsonTexto(json['active_role']),
        perfiles: (json['roles'] is List ? json['roles'] as List : const []).whereType<Map>().map(Perfil.desde).toList(),
        esSupervisor: json['supervisor'] == true,
        crudo: json,
      );

  /// Un técnico de campo es un contacto de la empresa verificadora: entra
  /// directo al flujo del técnico, sin elegir perfil.
  bool get esTecnico => esTecnicoDeCampo && empresa != null;

  List<Perfil> get aprobados => perfiles.where((p) => p.aprobado).toList();
}
