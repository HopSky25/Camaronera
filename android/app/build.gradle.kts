import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Llave de subida (Google Play). key.properties y el .jks viven FUERA del
// repositorio. Se busca, en orden: la variable de entorno
// CAMARONMARKET_KEY_PROPERTIES, android/key.properties (ignorado por git) y
// C:/dev/llaves/key.properties. Si no hay ninguno (p. ej. otro equipo), el
// release se firma con la llave de depuración para no romper la compilación.
val propiedadesLlave: Properties? = listOfNotNull(
    System.getenv("CAMARONMARKET_KEY_PROPERTIES")?.let { file(it) },
    rootProject.file("key.properties"),
    file("C:/dev/llaves/key.properties"),
).firstOrNull { it.isFile }?.let { archivo ->
    Properties().apply { FileInputStream(archivo).use { load(it) } }
}

android {
    namespace = "com.camaronmarket.verificador_campo"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // flutter_local_notifications (avisos) lo exige.
        isCoreLibraryDesugaringEnabled = true
    }

    defaultConfig {
        // Id publicado (assetlinks.json de C6 lo usa): no cambiarlo.
        applicationId = "com.camaronmarket.verificador_campo"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (propiedadesLlave != null) {
            create("subida") {
                keyAlias = propiedadesLlave.getProperty("keyAlias")
                keyPassword = propiedadesLlave.getProperty("keyPassword")
                storeFile = file(propiedadesLlave.getProperty("storeFile"))
                storePassword = propiedadesLlave.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = if (propiedadesLlave != null) signingConfigs.getByName("subida") else signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

dependencies {
    // BiometricPrompt con CryptoObject: firma ES256 y cifrado AES ligados a la
    // huella o al PIN del teléfono (SeguridadDispositivo.kt).
    implementation("androidx.biometric:biometric:1.1.0")
    // FlutterFragmentActivity + tema AppCompat (el diálogo de huella en
    // Android 7-8 lo necesita).
    implementation("androidx.appcompat:appcompat:1.7.0")
    // Desugaring de java.time para flutter_local_notifications.
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}
