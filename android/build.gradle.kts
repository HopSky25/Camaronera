allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
// file_picker 8 compila contra android-34 y flutter_plugin_android_lifecycle
// exige 36 o más: se sube el compileSdk de los plugins que se quedaron atrás.
subprojects {
    afterEvaluate {
        val android = extensions.findByName("android")
        if (android is com.android.build.api.dsl.LibraryExtension) {
            val sdk = android.compileSdk
            if (sdk == null || sdk < 36) android.compileSdk = 36
        }
    }
}
subprojects {
    project.evaluationDependsOn(":app")
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
