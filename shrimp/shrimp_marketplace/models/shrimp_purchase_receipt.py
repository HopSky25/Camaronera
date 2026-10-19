"""Datos del comprobante de compra (PDF) y de la pantalla «¡Compra realizada!».

Solo LEE: no crea cobros, no contabiliza nada y no genera tokens. Reúne en un
sitio lo que las dos plantillas necesitan para no repetir la lógica en QWeb:

* Los importes, separados según la facturación en dos pasos:
  - el SERVICIO de la plataforma que paga el comprador (shrimp.charge cuyo
    pagador es el comprador: hoy, el honorario de la verificación en campo).
    Los impuestos salen de la factura de servicio contabilizada; si todavía
    no lo está, se muestra el importe sin impuestos y se dice así (nunca se
    calcula un IVA aquí);
  - el PRODUCTO (precio congelado × cantidad), que factura el vendedor desde
    su sistema. La comisión de la venta la paga el vendedor y no es del
    comprador: no sale en su comprobante.
* El logotipo embebido (data URI) para wkhtmltopdf, que no debe depender de
  poder pedir /web/image al servidor.
* La URL pública de trazabilidad (/t/<token>) SOLO si la compra ya tiene token
  (shrimp_api): no se crea ninguno al imprimir.
"""
import base64
import io
import logging

from odoo import models
from odoo.tools.misc import file_path

_logger = logging.getLogger(__name__)

# Cobros que siguen vigentes (los acreditados o anulados ya no se pagan).
_CHARGE_LIVE_STATES = ("pending", "invoiced", "error", "sri_rejected")


class ShrimpTransactionReceipt(models.Model):
    _inherit = "shrimp.transaction"

    # ------------------------------------------------------------------
    # Partes
    # ------------------------------------------------------------------
    def shrimp_receipt_party(self, side):
        """Datos de una parte para el comprobante: empresa, RUC/cédula,
        dirección, rol con el que actuó y contacto."""
        self.ensure_one()
        tx = self.sudo()
        partner = tx.buyer_partner_id if side == "buyer" else tx.seller_partner_id
        role = tx.buyer_role if side == "buyer" else tx.seller_role
        empresa = partner.commercial_partner_id or partner
        lugar = ", ".join(x for x in (
            empresa.city, empresa.state_id.name, empresa.country_id.name) if x)
        return {
            "partner": partner,
            "name": empresa.name or partner.name or "—",
            "vat": empresa.vat or partner.vat or False,
            "street": ", ".join(x for x in (empresa.street, empresa.street2) if x),
            "place": lugar,
            "role": ("Naupliero" if role == "semillero" else tx.shrimp_role_label(role)) if role else "",
            "email": empresa.email or partner.email or False,
            "phone": empresa.phone or partner.phone or False,
        }

    # ------------------------------------------------------------------
    # Importes (facturación en dos pasos)
    # ------------------------------------------------------------------
    def shrimp_receipt_service_charges(self):
        """Cobros de la plataforma que paga el COMPRADOR en esta compra."""
        self.ensure_one()
        tx = self.sudo()
        comprador = tx.buyer_partner_id.commercial_partner_id
        return tx.charge_ids.filtered(
            lambda c: c.state in _CHARGE_LIVE_STATES
            and c.payer_partner_id.commercial_partner_id == comprador)

    def shrimp_is_verified_purchase(self):
        self.ensure_one()
        return bool("verification_ids" in self._fields and self.sudo().verification_ids)

    def shrimp_receipt_amounts(self):
        """Importes del comprobante. Nada se inventa: el servicio con los
        impuestos de su factura contabilizada (o sin impuestos si aún no la
        hay) y el producto con el precio congelado de la compra."""
        self.ensure_one()
        tx = self.sudo()
        tipos = dict(self.env["shrimp.charge"]._fields["charge_type"]._description_selection(self.env))
        estados = dict(self.env["shrimp.charge"]._fields["state"]._description_selection(self.env))
        lineas = []
        for c in self.shrimp_receipt_service_charges().sorted("id"):
            factura = c.invoice_id if c.invoice_id and c.invoice_id.state == "posted" else False
            lineas.append({
                "charge": c,
                "label": tipos.get(c.charge_type) or c.description or "Servicio",
                "ref": c.name,
                "date": c.date,
                "untaxed": c.amount or 0.0,
                "tax": factura.amount_tax if factura else None,
                "total": factura.amount_total if factura else None,
                "invoice": factura.name if factura else False,
                "state": c.state,
                "state_label": estados.get(c.state, c.state),
            })
        servicio_sin = round(sum(l["untaxed"] for l in lineas), 2)
        con_factura = bool(lineas) and all(l["invoice"] for l in lineas)
        servicio_imp = round(sum(l["tax"] or 0.0 for l in lineas), 2) if con_factura else None
        servicio_total = round(sum(l["total"] or 0.0 for l in lineas), 2) if con_factura else servicio_sin
        producto = round(tx.amount_total or 0.0, 2)
        return {
            "currency": tx.env.company.currency_id,
            "service_lines": lineas,
            "service_untaxed": servicio_sin,
            "service_tax": servicio_imp,
            "service_total": servicio_total,
            "service_taxes_known": con_factura,
            "product_qty": tx.transaction_qty or 0.0,
            "product_unit": tx.price_unit or 0.0,
            "product_total": producto,
            "grand_total": round(servicio_total + producto, 2),
            "verified": self.shrimp_is_verified_purchase(),
        }

    def shrimp_receipt_verification(self):
        """Resumen de la verificación en campo (si la hubo)."""
        self.ensure_one()
        if not self.shrimp_is_verified_purchase():
            return False
        v = self.sudo().verification_ids[:1]
        estados = dict(v._fields["state"]._description_selection(v.env))
        return {
            "record": v,
            "name": v.name,
            "label": v.verification_label() if hasattr(v, "verification_label") else "",
            "declared": v.verification_mode == "declared",
            "state_label": estados.get(v.state, v.state),
            "date": v.verified_date,
        }

    # ------------------------------------------------------------------
    # Marca, enlace público y QR
    # ------------------------------------------------------------------
    def shrimp_receipt_logo_uri(self):
        """Logotipo embebido (PNG recortado, 640 px) para el PDF."""
        try:
            ruta = file_path("shrimp_marketplace/static/src/img/receipt_logo.png")
            with open(ruta, "rb") as fh:
                return "data:image/png;base64,%s" % base64.b64encode(fh.read()).decode()
        except (OSError, ValueError):
            _logger.warning("No se encontró el logotipo del comprobante.")
            return False

    def shrimp_receipt_public_url(self):
        """/t/<token> si la compra YA tiene token público (shrimp_api); si
        no, nada: imprimir el comprobante no crea ni rota tokens."""
        self.ensure_one()
        tx = self.sudo()
        token = tx.trace_token if "trace_token" in tx._fields else False
        if not token:
            return False
        # D-29: sin página pública (borrador, en verificación o retirada),
        # el comprobante no lleva un enlace que no abre nada.
        if hasattr(tx, "shrimp_trace_public_visible") and not tx.shrimp_trace_public_visible():
            return False
        return "%s/t/%s" % (tx.get_base_url(), token)

    def shrimp_receipt_qr_uri(self):
        url = self.shrimp_receipt_public_url()
        if not url:
            return False
        try:
            import qrcode
            img = qrcode.make(url, box_size=6, border=1)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64,%s" % base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001 — sin qrcode el comprobante sale igual
            return False
