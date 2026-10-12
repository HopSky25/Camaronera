package com.camaronmarket.verificador_campo

import android.content.Intent
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * FlutterFragmentActivity (no FlutterActivity): BiometricPrompt necesita una
 * FragmentActivity para mostrar el diálogo de huella / PIN.
 */
class MainActivity : FlutterFragmentActivity() {
    private var seguridad: SeguridadDispositivo? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val s = SeguridadDispositivo(this)
        seguridad = s
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, SeguridadDispositivo.CANAL).setMethodCallHandler(s)
    }

    /** Vuelta de la pantalla del PIN / patrón del sistema (Android 7 a 10). */
    @Deprecated("Se mantiene por compatibilidad con createConfirmDeviceCredentialIntent")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (seguridad?.alResultadoActividad(requestCode, resultCode) == true) return
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
    }
}
