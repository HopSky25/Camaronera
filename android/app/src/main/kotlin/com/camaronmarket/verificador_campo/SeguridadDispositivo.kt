package com.camaronmarket.verificador_campo

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import android.security.keystore.UserNotAuthenticatedException
import android.util.Base64
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Seguridad del teléfono para la app (canal "com.camaronmarket/seguridad").
 *
 * Claves en el Android Keystore, NO exportables, que solo se usan tras
 * autenticar a la persona (huella fuerte o PIN / patrón del teléfono):
 *  - camaronmarket_firma_es256_a / _b: par ECDSA P-256. Firma las operaciones
 *    sensibles (SHA256withECDSA → firma DER/ASN.1). La pública sale como
 *    SubjectPublicKeyInfo DER (PublicKey.getEncoded()). Hay dos ranuras para
 *    "Registrar de nuevo este teléfono" (contrato 1.1): la clave nueva se crea
 *    en la otra ranura, la VIEJA firma el POST /me/devices y solo tras el 201
 *    se activa la nueva y se borra la vieja (activarClaveNueva).
 *  - camaronmarket_cifrado_aes: AES-256-GCM. Cifra la clave de API trz_…
 *    guardada en el teléfono: sin la persona no se puede leer.
 *
 * Android 11+ (API 30): cada uso exige su propia autenticación ligada a la
 * operación (BiometricPrompt + CryptoObject, huella fuerte o PIN).
 * Android 7-10 (API 24-29): Android no admite PIN con CryptoObject; la clave
 * queda autorizada durante VENTANA_SEGUNDOS tras autenticar (huella fuerte
 * con BiometricPrompt, o PIN con la pantalla de confirmación del sistema), y
 * la operación se hace justo después.
 *
 * Errores devueltos a Dart (PlatformException.code): cancelado, sin_bloqueo,
 * sin_biometria, bloqueado_intentos, clave_invalidada, sin_claves,
 * no_autenticado, datos_danados, ocupado, error.
 */
class SeguridadDispositivo(private val actividad: FragmentActivity) : MethodChannel.MethodCallHandler {
    companion object {
        const val CANAL = "com.camaronmarket/seguridad"
        private const val ALIAS_FIRMA = "camaronmarket_firma_es256_"
        private const val PREFS = "camaronmarket_seguridad"
        private const val PREF_RANURA = "ranura_firma"
        private const val ALIAS_CIFRADO = "camaronmarket_cifrado_aes"
        private const val VENTANA_SEGUNDOS = 10
        private const val PEDIDO_PIN = 7301
        private const val LARGO_IV = 12
    }

    private val almacen: KeyStore by lazy { KeyStore.getInstance("AndroidKeyStore").apply { load(null) } }
    private val api30 = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
    private val prefs by lazy { actividad.getSharedPreferences(PREFS, Context.MODE_PRIVATE) }

    /** Ranura de la clave de firma vigente ("a" o "b") y la otra. */
    private val ranura: String get() = prefs.getString(PREF_RANURA, "a") ?: "a"
    private val aliasFirma: String get() = ALIAS_FIRMA + ranura
    private val aliasOtra: String get() = ALIAS_FIRMA + (if (ranura == "a") "b" else "a")

    /** Solo una autenticación a la vez. */
    private var ocupado = false

    /** Continuación tras la pantalla de PIN del sistema (Android 7-10). */
    private var trasPin: ((Boolean) -> Unit)? = null

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        try {
            when (call.method) {
                "estado" -> result.success(estado())
                "prepararClaves" -> result.success(prepararClaves())
                "clavePublica" -> result.success(clavePublica(aliasFirma))
                "prepararClaveNueva" -> {
                    borrar(aliasOtra)
                    generarParFirma(aliasOtra)
                    result.success(clavePublica(aliasOtra))
                }
                "activarClaveNueva" -> {
                    if (!almacen.containsAlias(aliasOtra)) throw SinClaves()
                    val vieja = aliasFirma
                    prefs.edit().putString(PREF_RANURA, if (ranura == "a") "b" else "a").commit()
                    borrar(vieja)
                    result.success(null)
                }
                "descartarClaveNueva" -> {
                    borrar(aliasOtra)
                    result.success(null)
                }
                "firmar" -> firmar(call, result)
                "cifrar" -> cifrar(call, result)
                "descifrar" -> descifrar(call, result)
                "borrarClaveFirma" -> {
                    borrar(ALIAS_FIRMA + "a")
                    borrar(ALIAS_FIRMA + "b")
                    result.success(null)
                }
                "borrarClaves" -> {
                    borrar(ALIAS_FIRMA + "a")
                    borrar(ALIAS_FIRMA + "b")
                    borrar(ALIAS_CIFRADO)
                    result.success(null)
                }
                "abrirAjustesSeguridad" -> {
                    abrirAjustes()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        } catch (e: Exception) {
            val (codigo, mensaje) = traducir(e)
            result.error(codigo, mensaje, null)
        }
    }

    // ---------------------------------------------------------------- estado

    private fun estado(): Map<String, Any> {
        val km = actividad.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val biometria = when (BiometricManager.from(actividad).canAuthenticate(BIOMETRIC_STRONG)) {
            BiometricManager.BIOMETRIC_SUCCESS -> "disponible"
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED -> "sin_inscribir"
            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE -> "sin_hardware"
            else -> "no_disponible"
        }
        return mapOf(
            "bloqueo" to km.isDeviceSecure,
            "biometria" to biometria,
            "sdk" to Build.VERSION.SDK_INT,
            "fabricante" to (Build.MANUFACTURER ?: ""),
            "modelo" to (Build.MODEL ?: ""),
            "tieneClaveFirma" to almacen.containsAlias(aliasFirma),
            "tieneClaveCifrado" to almacen.containsAlias(ALIAS_CIFRADO),
        )
    }

    private fun abrirAjustes() {
        val intento = Intent(Settings.ACTION_SECURITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            actividad.startActivity(intento)
        } catch (_: Exception) {
            actividad.startActivity(Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    // ---------------------------------------------------------------- claves

    private fun conAutenticacion(b: KeyGenParameterSpec.Builder): KeyGenParameterSpec.Builder {
        b.setUserAuthenticationRequired(true)
        if (api30) {
            // Cada uso, su autenticación: huella fuerte o credencial del teléfono.
            b.setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL)
        } else {
            @Suppress("DEPRECATION")
            b.setUserAuthenticationValidityDurationSeconds(VENTANA_SEGUNDOS)
        }
        // Si alguien añade una huella nueva, las claves dejan de valer.
        b.setInvalidatedByBiometricEnrollment(true)
        return b
    }

    private fun generarParFirma(alias: String) {
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        gen.initialize(
            conAutenticacion(
                KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
            ).build()
        )
        gen.generateKeyPair()
    }

    /** Crea lo que falte y devuelve la pública vigente (SPKI DER base64). */
    private fun prepararClaves(): String {
        if (!almacen.containsAlias(aliasFirma)) generarParFirma(aliasFirma)
        if (!almacen.containsAlias(ALIAS_CIFRADO)) {
            val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            gen.init(
                conAutenticacion(
                    KeyGenParameterSpec.Builder(ALIAS_CIFRADO, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setKeySize(256)
                ).build()
            )
            gen.generateKey()
        }
        return clavePublica(aliasFirma) ?: throw IllegalStateException("No se pudo crear la clave de firma")
    }

    private fun clavePublica(alias: String): String? {
        val cert = almacen.getCertificate(alias) ?: return null
        return b64(cert.publicKey.encoded) // X.509 SubjectPublicKeyInfo DER
    }

    private fun borrar(alias: String) {
        if (almacen.containsAlias(alias)) almacen.deleteEntry(alias)
    }

    private fun clavePrivada(): PrivateKey =
        almacen.getKey(aliasFirma, null) as? PrivateKey ?: throw SinClaves()

    private fun claveAes(): SecretKey =
        almacen.getKey(ALIAS_CIFRADO, null) as? SecretKey ?: throw SinClaves()

    private class SinClaves : Exception("No hay claves en este teléfono")

    // ------------------------------------------------------------ operaciones

    private fun firmar(call: MethodCall, result: MethodChannel.Result) {
        val datos = call.argument<ByteArray>("datos") ?: throw IllegalArgumentException("Faltan los datos")
        conAutenticacion(call, result,
            preparar = { BiometricPrompt.CryptoObject(Signature.getInstance("SHA256withECDSA").apply { initSign(clavePrivada()) }) },
            terminar = { co ->
                val s = co.signature!!
                s.update(datos)
                b64(s.sign()) // DER (ASN.1 SEQUENCE { r, s })
            })
    }

    private fun cifrar(call: MethodCall, result: MethodChannel.Result) {
        val texto = call.argument<String>("texto") ?: throw IllegalArgumentException("Falta el texto")
        conAutenticacion(call, result,
            preparar = { BiometricPrompt.CryptoObject(Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, claveAes()) }) },
            terminar = { co ->
                val c = co.cipher!!
                val cifrado = c.doFinal(texto.toByteArray(Charsets.UTF_8))
                b64(c.iv + cifrado)
            })
    }

    private fun descifrar(call: MethodCall, result: MethodChannel.Result) {
        val datos = Base64.decode(call.argument<String>("datos") ?: throw IllegalArgumentException("Faltan los datos"), Base64.NO_WRAP)
        if (datos.size <= LARGO_IV) throw AEADBadTagException("Datos cifrados incompletos")
        val iv = datos.copyOfRange(0, LARGO_IV)
        val cifrado = datos.copyOfRange(LARGO_IV, datos.size)
        conAutenticacion(call, result,
            preparar = {
                BiometricPrompt.CryptoObject(Cipher.getInstance("AES/GCM/NoPadding").apply {
                    init(Cipher.DECRYPT_MODE, claveAes(), GCMParameterSpec(128, iv))
                })
            },
            terminar = { co -> String(co.cipher!!.doFinal(cifrado), Charsets.UTF_8) })
    }

    // ---------------------------------------------------------- autenticación

    /**
     * Pide la autenticación y hace la operación criptográfica.
     * API 30+: se prepara el objeto (no necesita autenticación), se ata al
     * BiometricPrompt y se termina con el objeto ya autorizado.
     * API 24-29: se autentica primero (huella fuerte o PIN del sistema) y se
     * prepara + termina dentro de la ventana de VENTANA_SEGUNDOS.
     */
    private fun conAutenticacion(
        call: MethodCall,
        result: MethodChannel.Result,
        preparar: () -> BiometricPrompt.CryptoObject,
        terminar: (BiometricPrompt.CryptoObject) -> Any,
    ) {
        if (ocupado) {
            result.error("ocupado", "Ya hay una confirmación en curso.", null)
            return
        }
        val titulo = call.argument<String>("titulo") ?: "Confirma que eres tú"
        val subtitulo = call.argument<String>("subtitulo")
        val conPin = call.argument<Boolean>("conPin") == true
        var respondido = false
        fun responder(bloque: () -> Any?) {
            if (respondido) return
            respondido = true
            ocupado = false
            try {
                result.success(bloque())
            } catch (e: Exception) {
                val (codigo, mensaje) = traducir(e)
                result.error(codigo, mensaje, null)
            }
        }
        fun fallar(codigo: String, mensaje: String) {
            if (respondido) return
            respondido = true
            ocupado = false
            result.error(codigo, mensaje, null)
        }

        ocupado = true
        try {
            if (api30) {
                val co = preparar() // puede lanzar KeyPermanentlyInvalidatedException
                val autenticadores = if (conPin) DEVICE_CREDENTIAL else (BIOMETRIC_STRONG or DEVICE_CREDENTIAL)
                mostrarPrompt(titulo, subtitulo, autenticadores, co,
                    exito = { r -> responder { terminar(r.cryptoObject ?: co) } },
                    error = { c, m -> fallar(c, m) })
            } else {
                val km = actividad.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
                val hayHuella = BiometricManager.from(actividad).canAuthenticate(BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
                val trasAutenticar = { responder { terminar(preparar()) } }
                val pedirPin = {
                    val intento = km.createConfirmDeviceCredentialIntent(titulo, subtitulo)
                    if (intento == null) {
                        fallar("sin_bloqueo", "El teléfono no tiene bloqueo de pantalla.")
                    } else {
                        trasPin = { ok -> if (ok) trasAutenticar() else fallar("cancelado", "No se confirmó.") }
                        @Suppress("DEPRECATION")
                        actividad.startActivityForResult(intento, PEDIDO_PIN)
                    }
                }
                if (conPin || !hayHuella) {
                    pedirPin()
                } else {
                    mostrarPrompt(titulo, subtitulo, BIOMETRIC_STRONG, null,
                        exito = { _ -> trasAutenticar() },
                        error = { c, m -> if (c == "usar_pin") pedirPin() else fallar(c, m) })
                }
            }
        } catch (e: Exception) {
            val (codigo, mensaje) = traducir(e)
            fallar(codigo, mensaje)
        }
    }

    private fun mostrarPrompt(
        titulo: String,
        subtitulo: String?,
        autenticadores: Int,
        co: BiometricPrompt.CryptoObject?,
        exito: (BiometricPrompt.AuthenticationResult) -> Unit,
        error: (String, String) -> Unit,
    ) {
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(titulo)
            .apply { if (!subtitulo.isNullOrBlank()) setSubtitle(subtitulo) }
            .setAllowedAuthenticators(autenticadores)
            .setConfirmationRequired(false)
            .apply { if (autenticadores and DEVICE_CREDENTIAL == 0) setNegativeButtonText("Usar PIN del teléfono") }
            .build()
        val prompt = BiometricPrompt(actividad, ContextCompat.getMainExecutor(actividad),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(r: BiometricPrompt.AuthenticationResult) = exito(r)

                override fun onAuthenticationError(codigo: Int, mensaje: CharSequence) {
                    val c = when (codigo) {
                        BiometricPrompt.ERROR_NEGATIVE_BUTTON -> "usar_pin"
                        BiometricPrompt.ERROR_USER_CANCELED, BiometricPrompt.ERROR_CANCELED,
                        BiometricPrompt.ERROR_TIMEOUT -> "cancelado"
                        BiometricPrompt.ERROR_LOCKOUT, BiometricPrompt.ERROR_LOCKOUT_PERMANENT -> "bloqueado_intentos"
                        BiometricPrompt.ERROR_NO_BIOMETRICS, BiometricPrompt.ERROR_HW_NOT_PRESENT,
                        BiometricPrompt.ERROR_HW_UNAVAILABLE -> "sin_biometria"
                        BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL -> "sin_bloqueo"
                        else -> "error"
                    }
                    error(c, mensaje.toString())
                }
                // onAuthenticationFailed: huella no reconocida; el diálogo sigue abierto.
            })
        if (co != null) prompt.authenticate(info, co) else prompt.authenticate(info)
    }

    /** Vuelta de la pantalla de PIN del sistema; true si era nuestra. */
    fun alResultadoActividad(pedido: Int, resultado: Int): Boolean {
        if (pedido != PEDIDO_PIN) return false
        val seguir = trasPin
        trasPin = null
        seguir?.invoke(resultado == Activity.RESULT_OK)
        return true
    }

    // ------------------------------------------------------------- utilidades

    private fun traducir(e: Exception): Pair<String, String> = when (e) {
        is KeyPermanentlyInvalidatedException -> "clave_invalidada" to "Cambió el bloqueo o las huellas del teléfono."
        is UserNotAuthenticatedException -> "no_autenticado" to "No se confirmó a tiempo. Vuelve a intentarlo."
        is AEADBadTagException -> "datos_danados" to "Los datos guardados no se pueden descifrar."
        is SinClaves -> "sin_claves" to (e.message ?: "")
        else -> "error" to (e.message ?: e.javaClass.simpleName)
    }

    private fun b64(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)
}
