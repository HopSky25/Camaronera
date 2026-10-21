# -*- coding: utf-8 -*-
"""Puesta en marcha del SRI con los datos reales de la empresa (l10n_ec_sri_community 19.0.1.4).

Lo que se puede deducir ya lo hace solo el post_init_hook al instalar (los
códigos del catálogo en cada IVA y la compañía en ambiente de PRUEBAS). Lo que
NO se puede deducir son datos de identidad fiscal, y por eso van aquí y no en
el hook: inventarlos sería peor que dejarlos en blanco, porque acabarían dentro
de un comprobante electrónico.

Qué hace (es idempotente: se puede correr las veces que haga falta):

1. Escribe en la compañía los datos que se le pasen: RUC, razón social, nombre
   comercial, dirección matriz, régimen, obligado a llevar contabilidad,
   contribuyente especial y agente de retención. Lo que no se pasa no se toca
   (nunca se borra un dato con un vacío).
2. Crea o actualiza el establecimiento (001 por defecto) y su punto de emisión
   (001) en el ambiente de la compañía, con las líneas 01 Factura y 04 Nota de
   crédito, cada una con su secuencia.
3. Si está instalado shrimp_l10n_ec: diario de facturas de servicio (ventas,
   «Usar documentos»), atado al punto; punto de emisión y forma de pago SRI de
   la plataforma.
4. NO enciende el SRI (``ec_sri_enabled``) salvo que se pase ``--activar``, y
   NO cambia el ambiente salvo que se pase ``--ambiente``: una compañía nueva
   queda en PRUEBAS (1). Nunca pasa sola a producción.
5. Imprime una lista de lo que falta para emitir.

Lo que NO hace, a propósito:

* Subir el certificado .p12. Se sube en Ajustes › Ecuador SRI › Certificado
  de firma (se guarda cifrado con la llave del servidor). El .p12 no debe
  pasar por scripts, variables de entorno ni la línea de comandos.
* Poner la llave EC_SRI_MASTER_KEY (o EC_SRI_MASTER_KEY_FILE). Es un secreto
  del SERVIDOR, fuera de la base y del repositorio; sin ella no se puede subir
  el certificado ni firmar.

Uso (``odoo-bin shell`` no deja pasar argumentos propios, así que los datos van
en variables de entorno SRI_* o, con la sintaxis de la línea de comandos, en
SRI_ARGS). Desde /home/ccarballo/odoo19:

    SRI_RUC=0990000000001 SRI_RAZON_SOCIAL="EMPRESA S.A.S." \\
    SRI_DIRECCION_MATRIZ="Av. Principal 123, Guayaquil" \\
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d BASE --no-http \\
        < custom_addons/Camaronera/sri/l10n_ec_sri_community/scripts/configurar_sri.py

    # lo mismo con argumentos; --simular no guarda nada (ensayo):
    SRI_ARGS='--ruc 0990000000001 --razon-social "EMPRESA S.A.S." --simular' \\
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d BASE --no-http \\
        < custom_addons/Camaronera/sri/l10n_ec_sri_community/scripts/configurar_sri.py

Variables (y su argumento equivalente en SRI_ARGS):

    SRI_COMPANIA                  --compania        id o nombre exacto (por defecto: la del RUC o la única de Ecuador)
    SRI_RUC                       --ruc             13 dígitos
    SRI_RAZON_SOCIAL              --razon-social    nombre de la compañía (razonSocial del XML)
    SRI_NOMBRE_COMERCIAL          --nombre-comercial
    SRI_DIRECCION_MATRIZ          --direccion-matriz
    SRI_REGIMEN                   --regimen         regular | rimpe | popular
    SRI_OBLIGADO_CONTABILIDAD     --obligado-contabilidad  si | no
    SRI_CONTRIBUYENTE_ESPECIAL    --contribuyente-especial resolución (vacío si no aplica)
    SRI_AGENTE_RETENCION          --agente-retencion       resolución (vacío si no aplica)
    SRI_ESTABLECIMIENTO           --establecimiento        001
    SRI_DIRECCION_ESTABLECIMIENTO --direccion-establecimiento (por defecto, la matriz)
    SRI_PUNTO                     --punto           001
    SRI_AMBIENTE                  --ambiente        1 (pruebas) | 2 (producción)
    SRI_FORMA_PAGO                --forma-pago      código SRI, 20 por defecto
                                  --activar / --desactivar / --simular
"""
import argparse
import os
import re
import shlex

REGIMENES = ('regular', 'rimpe', 'popular')
DOCUMENTOS = (('01', 'l10n_ec.ec_dt_01', 'Factura'), ('04', 'l10n_ec.ec_dt_04', 'Nota de crédito'))
VARIABLES = {
    'compania': 'SRI_COMPANIA', 'ruc': 'SRI_RUC', 'razon_social': 'SRI_RAZON_SOCIAL',
    'nombre_comercial': 'SRI_NOMBRE_COMERCIAL', 'direccion_matriz': 'SRI_DIRECCION_MATRIZ',
    'regimen': 'SRI_REGIMEN', 'obligado_contabilidad': 'SRI_OBLIGADO_CONTABILIDAD',
    'contribuyente_especial': 'SRI_CONTRIBUYENTE_ESPECIAL', 'agente_retencion': 'SRI_AGENTE_RETENCION',
    'establecimiento': 'SRI_ESTABLECIMIENTO', 'direccion_establecimiento': 'SRI_DIRECCION_ESTABLECIMIENTO',
    'punto': 'SRI_PUNTO', 'ambiente': 'SRI_AMBIENTE', 'forma_pago': 'SRI_FORMA_PAGO',
}


class ConfigError(Exception):
    """Dato inválido o compañía no encontrada: se informa y no se guarda nada."""


def _si_no(valor):
    if valor is None:
        return None
    texto = str(valor).strip().lower()
    if texto in ('si', 'sí', 's', '1', 'true', 'yes'):
        return True
    if texto in ('no', 'n', '0', 'false'):
        return False
    raise ConfigError('Valor sí/no inválido: %r' % valor)


def leer_datos(argv=None, environ=None):
    """Datos de SRI_ARGS (o ``argv``) y de las variables SRI_*; mandan los argumentos."""
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog='configurar_sri', add_help=False)
    for clave in VARIABLES:
        parser.add_argument('--' + clave.replace('_', '-'), dest=clave)
    for flag in ('activar', 'desactivar', 'simular'):
        parser.add_argument('--' + flag, action='store_true')
    if argv is None:
        argv = shlex.split(environ.get('SRI_ARGS', ''))
    args = parser.parse_args(argv)
    datos = {}
    for clave, variable in VARIABLES.items():
        valor = getattr(args, clave)
        if valor is None:
            valor = environ.get(variable)
        if valor is not None:
            datos[clave] = valor.strip()
    datos['activar'] = args.activar or _si_no(environ.get('SRI_ACTIVAR')) or False
    datos['desactivar'] = args.desactivar
    datos['simular'] = args.simular or _si_no(environ.get('SRI_SIMULAR')) or False
    return datos


def _validar(datos):
    if datos.get('activar') and datos.get('desactivar'):
        raise ConfigError('Use --activar o --desactivar, no los dos.')
    if datos.get('ruc') and not re.fullmatch(r'[0-9]{13}', datos['ruc']):
        raise ConfigError('El RUC debe tener 13 dígitos: %r' % datos['ruc'])
    if datos.get('regimen') and datos['regimen'] not in REGIMENES:
        raise ConfigError('Régimen inválido %r (use %s).' % (datos['regimen'], ', '.join(REGIMENES)))
    if datos.get('ambiente') and datos['ambiente'] not in ('1', '2'):
        raise ConfigError('Ambiente inválido %r: 1 (pruebas) o 2 (producción).' % datos['ambiente'])
    for clave in ('establecimiento', 'punto'):
        if datos.get(clave) and not re.fullmatch(r'[0-9]{3}', datos[clave]):
            raise ConfigError('El código de %s debe tener 3 dígitos: %r' % (clave, datos[clave]))
    _si_no(datos.get('obligado_contabilidad'))


def buscar_compania(env, datos):
    Company = env['res.company'].sudo()
    ref = datos.get('compania')
    if ref:
        company = Company.browse(int(ref)).exists() if ref.isdigit() else Company.search([('name', '=', ref)], limit=1)
        if not company:
            raise ConfigError('No existe la compañía %r.' % ref)
    elif datos.get('ruc') and Company.search_count([('vat', '=', datos['ruc'])]) == 1:
        company = Company.search([('vat', '=', datos['ruc'])])
    else:
        company = Company.search([('country_id.code', '=', 'EC')])
        if len(company) != 1:
            raise ConfigError('Indique la compañía con SRI_COMPANIA (id o nombre): hay %s compañías de Ecuador.'
                              % len(company))
    if company.country_id.code != 'EC':
        raise ConfigError('La compañía %s no está en Ecuador.' % company.name)
    return company


def _compania(company, datos, R):
    vals = {}
    campos = (('ruc', 'vat'), ('razon_social', 'name'), ('nombre_comercial', 'ec_sri_trade_name'),
              ('direccion_matriz', 'ec_sri_address'), ('regimen', 'ec_sri_regime'),
              ('contribuyente_especial', 'ec_sri_special'), ('agente_retencion', 'ec_sri_agent'))
    for clave, campo in campos:
        if clave in datos:
            # Contribuyente especial y agente de retención admiten vacío («no aplica»).
            valor = datos[clave] or False
            if not valor and clave not in ('contribuyente_especial', 'agente_retencion'):
                continue
            if (company[campo] or False) != valor:
                vals[campo] = valor
    obligado = _si_no(datos.get('obligado_contabilidad'))
    if obligado is not None and company.ec_sri_accounting != obligado:
        vals['ec_sri_accounting'] = obligado
    if datos.get('ambiente') and company.ec_sri_environment != datos['ambiente']:
        vals['ec_sri_environment'] = datos['ambiente']
    if datos.get('activar') and not company.ec_sri_enabled:
        vals['ec_sri_enabled'] = True
    if datos.get('desactivar') and company.ec_sri_enabled:
        vals['ec_sri_enabled'] = False
    if vals:
        company.write(vals)
    R.append('Compañía %s: %s' % (company.name, ', '.join(sorted(vals)) if vals else 'sin cambios'))
    if company.ec_sri_enabled and not datos.get('activar'):
        # El post_init_hook habilita en PRUEBAS las compañías de Ecuador; el script no lo apaga solo.
        R.append('Aviso: el SRI ya estaba ACTIVADO en la compañía; no se cambia (use --desactivar para apagarlo).')


def _establecimiento(env, company, datos, R):
    Establishment = env['ec.sri.establishment'].sudo().with_context(active_test=False)
    code = datos.get('establecimiento') or '001'
    direccion = datos.get('direccion_establecimiento') or company.ec_sri_address
    est = Establishment.search([('company_id', '=', company.id), ('code', '=', code)], limit=1)
    if not est:
        if not direccion:
            R.append('Establecimiento %s NO creado: falta la dirección (matriz o del establecimiento).' % code)
            return est
        est = Establishment.create({'name': 'Matriz' if code == '001' else 'Sucursal %s' % code, 'code': code,
                                    'company_id': company.id, 'address': direccion,
                                    'is_headquarters': code == '001'})
        R.append('Establecimiento %s creado.' % code)
        return est
    vals = {}
    if not est.active:
        vals['active'] = True
    if datos.get('direccion_establecimiento') and est.address != datos['direccion_establecimiento']:
        vals['address'] = datos['direccion_establecimiento']
    if vals:
        est.write(vals)
    R.append('Establecimiento %s: %s' % (code, 'actualizado' if vals else 'ya existía'))
    return est


def _punto(env, company, est, datos, R):
    Point = env['ec.sri.point'].sudo().with_context(active_test=False)
    code = datos.get('punto') or '001'
    environment = company.ec_sri_environment
    point = Point.search([('establishment_id', '=', est.id), ('emission', '=', code),
                          ('environment', '=', environment)], limit=1)
    if not point:
        point = Point.create({'name': 'Punto %s' % code, 'establishment_id': est.id, 'emission': code,
                              'environment': environment})
        R.append('Punto de emisión %s-%s (ambiente %s) creado.' % (est.code, code, environment))
    else:
        if not point.active:
            point.active = True
        R.append('Punto de emisión %s-%s (ambiente %s) ya existía.' % (est.code, code, environment))
    for doc_type, xmlid, nombre in DOCUMENTOS:
        line = point.line_ids.filtered(lambda l: l.document_type == doc_type)
        if line:
            R.append('  %s %s: ya habilitada (próximo secuencial %s).' % (doc_type, nombre, line.next_number))
            continue
        tipo = env.ref(xmlid, raise_if_not_found=False)
        if not tipo:
            R.append('  %s %s: NO creada (no existe %s).' % (doc_type, nombre, xmlid))
            continue
        line = env['ec.sri.point.line'].sudo().create({'point_id': point.id, 'l10n_latam_document_type_id': tipo.id})
        R.append('  %s %s: habilitada (próximo secuencial %s).' % (doc_type, nombre, line.next_number))
    return point


def _plataforma(env, company, point, datos, R):
    """Diario, punto y forma de pago de las facturas de servicio (shrimp_l10n_ec)."""
    if 'shrimp_service_journal_id' not in company._fields:
        R.append('shrimp_l10n_ec no está instalado: se omite el diario de la plataforma.')
        return
    Journal = env['account.journal'].sudo()
    journal = company.shrimp_service_journal_id or Journal.search([
        ('company_id', '=', company.id), ('type', '=', 'sale'), ('l10n_latam_use_documents', '=', True)],
        order='sequence, id', limit=1)
    if not journal:
        codigo, n = 'FSRV', 1
        while Journal.with_context(active_test=False).search_count([('company_id', '=', company.id),
                                                                     ('code', '=', codigo)]):
            n += 1
            codigo = 'FSR%d' % n
        journal = Journal.create({'name': 'Facturas de servicio', 'code': codigo, 'type': 'sale',
                                  'company_id': company.id, 'l10n_latam_use_documents': True})
        R.append('Diario %s creado.' % journal.display_name)
    vals = {}
    if not journal.l10n_latam_use_documents:
        vals['l10n_latam_use_documents'] = True
    # Un punto de otro ambiente (p. ej. el de pruebas al pasar a producción) se reemplaza.
    if not journal.ec_sri_point_id or journal.ec_sri_point_id.environment != point.environment:
        vals['ec_sri_point_id'] = point.id
    if vals:
        journal.write(vals)
    R.append('Diario de servicios: %s (%s).' % (journal.display_name, ', '.join(sorted(vals)) or 'sin cambios'))
    cvals = {}
    if company.shrimp_service_journal_id != journal:
        cvals['shrimp_service_journal_id'] = journal.id
    actual = company.shrimp_service_sri_point_id
    if not actual or actual.environment != point.environment:
        cvals['shrimp_service_sri_point_id'] = point.id
    codigo_pago = datos.get('forma_pago') or (False if company.shrimp_service_payment_id else '20')
    if codigo_pago:
        pago = env['l10n_ec.sri.payment'].sudo().search([('code', '=', codigo_pago)], limit=1)
        if not pago:
            R.append('Forma de pago SRI %s no encontrada.' % codigo_pago)
        elif company.shrimp_service_payment_id != pago:
            cvals['shrimp_service_payment_id'] = pago.id
    if cvals:
        company.write(cvals)
    R.append('Plataforma: %s' % (', '.join(sorted(cvals)) if cvals else 'sin cambios'))


def configurar(env, datos):
    """Aplica ``datos`` (ver leer_datos). Devuelve (compañía, líneas del informe)."""
    _validar(datos)
    company = buscar_compania(env, datos)
    R = []
    _compania(company, datos, R)
    est = _establecimiento(env, company, datos, R)
    if est:
        point = _punto(env, company, est, datos, R)
        _plataforma(env, company, point, datos, R)
    return company, R


def pendientes(env, company):
    """Lista de lo que falta para emitir (vacía = listo)."""
    from odoo.addons.l10n_ec_sri_community.services import crypto, validators
    company = company.sudo()
    faltan = []
    if not re.fullmatch(r'[0-9]{13}', company.vat or ''):
        faltan.append('El RUC de 13 dígitos de la compañía (--ruc).')
    elif not validators.ec_identification_ok(company.vat):
        faltan.append('Revisar el RUC %s: no supera el dígito verificador.' % company.vat)
    if not company.ec_sri_address:
        faltan.append('La dirección matriz (--direccion-matriz).')
    if company.tax_calculation_rounding_method != 'round_per_line':
        faltan.append('Contabilidad › Ajustes › Impuestos: «Redondear por línea» (el SRI calcula por línea).')
    points = env['ec.sri.point'].sudo().search([('company_id', '=', company.id),
                                                 ('environment', '=', company.ec_sri_environment)])
    docs = set(points.mapped('line_ids.document_type'))
    for doc_type, _x, nombre in DOCUMENTOS:
        if doc_type not in docs:
            faltan.append('Un punto de emisión del ambiente actual con %s %s.' % (doc_type, nombre))
    if not company.ec_sri_cert_data:
        faltan.append('El certificado .p12: súbalo en Ajustes › Ecuador SRI › Certificado de firma '
                      '(con su contraseña; se guarda cifrado).')
    elif company.ec_sri_cert_expiry and company.ec_sri_cert_expiry < validators.today_ec():
        faltan.append('El certificado venció el %s: suba uno vigente.' % company.ec_sri_cert_expiry)
    try:
        llave = crypto._master_key()
    except ValueError:
        llave = None
    if not llave:
        faltan.append('La llave %s (o %s) en el entorno del SERVIDOR Odoo: sin ella no se puede subir el '
                      'certificado ni firmar. Nunca en la base ni en el repositorio.'
                      % (crypto.MASTER_ENV, crypto.MASTER_FILE_ENV))
    if 'shrimp_service_journal_id' in company._fields and company.ec_sri_enabled:
        for problema in company._shrimp_sri_config_problems():
            if problema not in faltan and 'certificado' not in problema:
                faltan.append(problema)
    if company.ec_sri_environment == '2' and not company.ec_sri_production_ready:
        faltan.append('Producción: marque «Pruebas de homologación completadas» en Ajustes tras la homologación.')
    if not company.ec_sri_enabled:
        faltan.append('El SRI está APAGADO en la compañía (a propósito): vuelva a correr con --activar '
                      'cuando todo lo anterior esté listo.')
    return faltan


def main(env, argv=None, environ=None):
    try:
        datos = leer_datos(argv, environ)
        company, R = configurar(env, datos)
    except ConfigError as exc:
        env.cr.rollback()
        print('ERROR: %s\nNo se guardó nada.' % exc)
        return False
    ambiente = dict(company._fields['ec_sri_environment'].selection).get(company.ec_sri_environment)
    print('\n'.join(R))
    print('\nCompañía %s · RUC %s · ambiente %s · SRI %s'
          % (company.name, company.vat or '-', ambiente, 'ACTIVADO' if company.ec_sri_enabled else 'apagado'))
    faltan = pendientes(env, company)
    if faltan:
        print('\nPendiente:')
        for item in faltan:
            print('  [ ] ' + item)
    else:
        print('\nListo para emitir en ambiente %s.' % ambiente)
    if datos['simular']:
        env.cr.rollback()
        print('\n--simular: no se guardó nada.')
    else:
        env.cr.commit()
    return True


if __name__ == '__main__' and 'env' in globals():
    main(env)  # noqa: F821 - lo define odoo-bin shell
