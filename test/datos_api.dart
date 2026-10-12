// Ejemplos de JSON con la forma exacta que devuelve shrimp_api
// (controllers/serializers.py: me(), verification(), dispatch()...).
// Se devuelven mapas nuevos en cada llamada para poder modificarlos.

Map<String, dynamic> meCamaronera() => {
      'id': 'a1b2c3d4-0000-4000-8000-000000000001',
      'name': 'Camaronera Las Mareas',
      'type': 'camaronera',
      'city': 'Machala',
      'province': 'El Oro',
      'country': 'EC',
      'ratings': {
        'as_seller': {'avg': 4.6, 'count': 12},
        'as_buyer': {'avg': 0.0, 'count': 0},
      },
      'email': 'socio@finca.ec',
      'phone': null,
      'website': null,
      'street': null,
      'zip': null,
      'is_field_technician': false,
      'company': null,
      'preferences': {'publish_verified_history': true},
      'api_key': {'prefix': 'ab12', 'name': 'App móvil – Pixel 7', 'scopes': ['verifications:read'], 'expires_at': '2027-10-11T10:00:00Z'},
      'active_role': 'camaronera',
      'roles': [
        {'role': 'camaronera', 'label': 'Camaronera', 'state': 'approved', 'active': true},
        {'role': 'empacadora', 'label': 'Empacadora', 'state': 'approved', 'active': false},
        {'role': 'maquilador', 'label': 'Maquilador', 'state': 'pending', 'active': false},
      ],
    };

Map<String, dynamic> meUnPerfil() => meCamaronera()
  ..['roles'] = [
    {'role': 'camaronera', 'label': 'Camaronera', 'state': 'approved', 'active': true},
  ];

Map<String, dynamic> meTecnico() => {
      'id': 'a1b2c3d4-0000-4000-8000-0000000000aa',
      'name': 'Juan Pérez',
      'type': 'verificador',
      'city': null,
      'province': null,
      'country': 'EC',
      'ratings': {'as_seller': {'avg': 0.0, 'count': 0}, 'as_buyer': {'avg': 0.0, 'count': 0}},
      'email': 'juan@verificadora.ec',
      'is_field_technician': true,
      'company': {'id': 'a1b2c3d4-0000-4000-8000-0000000000bb', 'name': 'Verificadora del Golfo'},
      'preferences': {},
      'api_key': {'prefix': 'cd34', 'name': 'App móvil – Samsung A54', 'scopes': [], 'expires_at': '2027-10-11T10:00:00Z'},
      'active_role': 'verificador',
      'roles': [],
    };

Map<String, dynamic> verificacionLista() => {
      'id': '6f1c0000-0000-4000-8000-00000000000a',
      'reference': 'VER/2026/0042',
      'my_role': 'technician',
      'state': 'assigned',
      'scope': 'shrimp',
      'transaction': '7b2e0000-0000-4000-8000-000000000003',
      'product': {'id': 'p1', 'name': 'Camarón 21/25 piscina 3'},
      'buyer': {'id': 'b1', 'name': 'Empacadora Pacífico'},
      'seller': {'id': 's1', 'name': 'Camaronera Las Mareas'},
      'verifier': {'id': 'v1', 'name': 'Verificadora del Golfo'},
      'verification_mode': 'platform',
      'verification_label': 'Verificadora del Golfo',
      'technician_assigned': true,
      'technician': {'id': 't1', 'name': 'Juan Pérez'},
      'dispatch': '9a1d0000-0000-4000-8000-000000000001',
      'dispatch_eta': '2026-10-01T14:30:00Z',
      'fee': {'amount': 150.0, 'currency': 'USD'},
      'acceptance_state': 'na',
      'acceptance_deadline': null,
      'verified_at': null,
      'line_count': 0,
      'count_count': 0,
      'updated_at': '2026-09-30T18:00:00Z',
    };

Map<String, dynamic> verificacionDetalle() => verificacionLista()
  ..['my_role'] = 'buyer'
  ..['state'] = 'done'
  ..['acceptance_state'] = 'waiting'
  ..['acceptance_deadline'] = '2026-10-03T14:30:00Z'
  ..addAll({
    'batch_code': 'LM-2026-17',
    'pond': {'id': 'pond1', 'name': 'Piscina 3'},
    'facility': {'id': 'fac1', 'name': 'Finca El Morro'},
    'plant_name': 'Planta Pacífico',
    'harvest_date': '2026-09-29',
    'process_date': '2026-09-30',
    'field_started_at': '2026-09-30T08:15:00Z',
    'weights': {'sent_lb': 1500.0, 'plant_lb': 1480.5, 'trash_lb': 0.0, 'overweight_lb': 0.0, 'overweight_factor': 0.0, 'net_lb': 1480.5},
    'presentation': 'entero',
    'presentation_matches_product': true,
    'metabisulfite': {'ppm': 85.0, 'limit_ppm': 100.0, 'result': 'ok', 'notes': null},
    'classification': {
      'total_lb': 1480.5,
      'class_a_lb': 1200.0,
      'class_b_lb': 280.5,
      'class_c_lb': 0.0,
      'yield_pct': 98.7,
      'yield_class_a_pct': 81.05,
      'yield_class_b_pct': 18.95,
      'yield_class_c_pct': 0.0,
      'lines': [
        {'id': 'l1', 'quality_class': 'a', 'size_code': '21/25', 'weight_lb': 1200.0, 'percent_of_total': 81.05},
        {'id': 'l2', 'quality_class': 'b', 'size_code': false, 'weight_lb': 280.5, 'percent_of_total': 18.95},
      ],
    },
    'taste': {'result': 'good', 'notes': null, 'criteria_ok': ['Sin olor']},
    'grams': {'farm': 18.2, 'plant_1': 18.0, 'plant_2': 0.0, 'variation': -1.1},
    'counts': [
      {'id': 'c1', 'value': 23.0, 'note': null},
    ],
    'larvae': null,
    'incident_notes': null,
    'verdict_notes': null,
    'declared': null,
    'photo_count': 3,
    'acceptances': [
      {'role': 'buyer', 'partner': {'id': 'b1', 'name': 'Empacadora Pacífico'}, 'decision': 'pending', 'reason': null, 'decided_at': null, 'automatic': false, 'counter_price': null, 'can_revert': false},
      {'role': 'seller', 'partner': {'id': 's1', 'name': 'Camaronera Las Mareas'}, 'decision': 'accepted', 'reason': null, 'decided_at': '2026-10-01T09:00:00Z', 'automatic': false, 'counter_price': null, 'can_revert': false},
    ],
    'warnings': [],
  });

Map<String, dynamic> despacho() => {
      'id': '9a1d0000-0000-4000-8000-000000000001',
      'my_role': 'seller',
      'state': 'programado',
      'transaction': '7b2e0000-0000-4000-8000-000000000003',
      'product': {'id': 'p1', 'name': 'Camarón 21/25 piscina 3'},
      'seller': {'id': 's1', 'name': 'Camaronera Las Mareas'},
      'buyer': {'id': 'b1', 'name': 'Empacadora Pacífico'},
      'verifier': {'id': 'v1', 'name': 'Verificadora del Golfo'},
      'verification': '6f1c0000-0000-4000-8000-00000000000a',
      'harvest_date': '2026-09-29',
      'farm_departure': '2026-10-01T10:00:00Z',
      'eta': '2026-10-01T14:30:00Z',
      'eta_first': '2026-10-01T14:00:00Z',
      'eta_changes': 1,
      'carrier_name': 'Transportes Loor',
      'vehicle_plate': 'ABC-1234',
      'carrier_phone': null,
      'notes': null,
      'actual_arrival': null,
      'delay_minutes': null,
      'on_time': null,
      'updated_at': '2026-09-30T18:00:00Z',
    };

/// Error RFC 9457 tal como lo emite la API.
Map<String, dynamic> problema(int status, String tipo, String detalle, {String? titulo}) => {
      'type': 'https://demo.camaronmarket.com/api/v1/docs#problem-$tipo',
      'title': titulo ?? 'Error',
      'status': status,
      'detail': detalle,
    };
