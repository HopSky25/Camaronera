"""Comprobantes SRI del socio dueño de la clave.

Excepción documentada a la regla "sin sudo": el portal no tiene (ni debe
tener) ACL sobre ec.sri.document, que es del módulo de contabilidad. Aquí se
lee en sudo con un dominio FIJO: comprobantes autorizados o anulados cuyo
receptor es la empresa del socio. Nada del cliente entra en ese dominio salvo
filtros validados.
"""

import base64

from odoo.http import request

from odoo.addons.shrimp_api.controllers.framework import (
    ApiError, ApiResponse, F, api_route, iso_date, iso_dt, money, not_found, obj, paginate)

TAG = "comprobantes SRI"
READ = ("invoices:read",)
VISIBLE_STATES = ("authorized", "cancelled")
DOC_TYPES = ["01", "03", "04", "05", "06", "07"]
INVOICE_SCHEMA = obj(access_key="string", number="string", document_type="string",
                     document_type_label="string", state="string", date="date",
                     authorization_number="string", authorization_date="string",
                     totals={"type": ["object", "null"]}, updated_at="datetime")


def _domain(ctx):
    receptor = ctx.partner.commercial_partner_id
    return [("partner_id.commercial_partner_id", "=", receptor.id),
            ("state", "in", VISIBLE_STATES), ("access_key", "!=", False)]


def _serialize(doc):
    doc = doc.sudo()
    move = doc.move_id
    key = doc.access_key or ""
    number = "%s-%s-%s" % (key[24:27], key[27:30], key[30:39]) if len(key) >= 39 else None
    return {
        "access_key": doc.access_key,
        "number": number,
        "document_type": doc.document_type,
        "document_type_label": dict(doc._fields["document_type"].selection).get(doc.document_type),
        "state": doc.state,
        "date": iso_date(doc.date),
        "authorization_number": doc.authorization_number or None,
        "authorization_date": doc.authorization_date or None,
        "issuer": {"name": doc.company_id.name, "vat": doc.company_id.vat or None},
        "totals": {
            "untaxed": money(move.amount_untaxed, move.currency_id),
            "tax": money(move.amount_tax, move.currency_id),
            "total": money(move.amount_total, move.currency_id),
        } if move else None,
        "cancelled_on": iso_date(doc.cancellation_date),
        "updated_at": iso_dt(doc.write_date),
    }


def _get(ctx, access_key):
    if not access_key or not access_key.isdigit() or len(access_key) != 49:
        raise not_found("El comprobante")
    doc = ctx.env["ec.sri.document"].sudo().search(
        _domain(ctx) + [("access_key", "=", access_key)], limit=1)
    if not doc:
        raise not_found("El comprobante")
    return doc


@api_route("GET", "/invoices", scope=READ, tags=[TAG], paginated=True,
           summary="Comprobantes electrónicos autorizados donde soy el receptor",
           params={"document_type": F("enum", enum=DOC_TYPES),
                   "state": F("enum", enum=list(VISIBLE_STATES))},
           response=INVOICE_SCHEMA)
def invoices_list(ctx):
    domain = _domain(ctx)
    dtype = ctx.enum_param("document_type", DOC_TYPES)
    if dtype:
        domain.append(("document_type", "=", dtype))
    state = ctx.enum_param("state", list(VISIBLE_STATES))
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "ec.sri.document", domain, _serialize, sudo=True)


@api_route("GET", "/invoices/{access_key}", scope=READ, tags=[TAG],
           summary="Detalle de un comprobante por su clave de acceso (49 dígitos)",
           response=INVOICE_SCHEMA)
def invoice_get(ctx, access_key):
    return _serialize(_get(ctx, access_key))


@api_route("GET", "/invoices/{access_key}/xml", scope=READ, tags=[TAG], binary="application/xml",
           summary="XML AUTORIZADO por el SRI (nunca el borrador ni el firmado sin autorizar)")
def invoice_xml(ctx, access_key):
    doc = _get(ctx, access_key).with_context(bin_size=False)
    if not doc.authorized_xml:
        raise ApiError(404, "not-found", "Este comprobante no tiene XML autorizado.")
    content = base64.b64decode(doc.authorized_xml)
    return ApiResponse(raw=content, content_type="application/xml; charset=utf-8", headers=[
        ("Content-Disposition", 'attachment; filename="%s-autorizado.xml"' % doc.access_key)])


@api_route("GET", "/invoices/{access_key}/ride.pdf", scope=READ, tags=[TAG], binary="application/pdf",
           summary="RIDE (representación impresa) del comprobante")
def invoice_ride(ctx, access_key):
    doc = _get(ctx, access_key)
    report = request.env.ref("l10n_ec_sri_community.action_report_ride").sudo()
    pdf, _fmt = report._render_qweb_pdf(report.report_name, res_ids=[doc.id])
    return ApiResponse(raw=pdf, content_type="application/pdf", headers=[
        ("Content-Disposition", 'attachment; filename="RIDE-%s.pdf"' % doc.access_key)])
