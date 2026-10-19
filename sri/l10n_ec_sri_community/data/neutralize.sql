-- Una base neutralizada (copia de producción) no debe poder firmar comprobantes
-- reales: se vacía el certificado cifrado y se fuerza el ambiente de PRUEBAS.
UPDATE res_company
   SET ec_sri_cert_data = NULL,
       ec_sri_cert_password_enc = NULL,
       ec_sri_cert_salt = NULL,
       ec_sri_cert_password = NULL,
       ec_sri_cert_subject = NULL,
       ec_sri_cert_expiry = NULL,
       ec_sri_environment = '1';
DELETE FROM ir_attachment
 WHERE res_model = 'res.company' AND res_field = 'ec_sri_cert_file';
