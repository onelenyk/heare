import java.net.URI
import java.security.MessageDigest

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.compose.compiler)
}

// The words: the same catalog as the brain's, the page's and the Mac's
// (scripts/po_strings.py). Built, never committed.
val repo = rootDir.resolve("../..")
val words = layout.buildDirectory.dir("generated/words")
val makeWords by tasks.registering(Exec::class) {
    val po = repo.resolve("packages/heare-core/heare/core/locale/uk/LC_MESSAGES/heare.po")
    inputs.file(po.resolveSibling("heare.mo"))
    outputs.dir(words)
    commandLine("python3", repo.resolve("scripts/po_strings.py"), po, words.get().file("words/uk.json").asFile)
}

// The wake word, heard on the phone: sherpa-onnx and its keyword model from
// k2-fsa's releases, the ones bringalive used. Fetched once into the build,
// checked by sha256, never committed (the library alone is 50 MB).
val vendor = rootDir.resolve("build/vendor")

fun fetched(url: String, sha256: String, name: String): File {
    val file = vendor.resolve(name)
    fun sum(f: File) = MessageDigest.getInstance("SHA-256")
        .digest(f.readBytes()).joinToString("") { "%02x".format(it) }
    if (!file.exists() || sum(file) != sha256) {
        vendor.mkdirs()
        logger.lifecycle("fetching $url")
        URI(url).toURL().openStream().use { input -> file.outputStream().use { input.copyTo(it) } }
        check(sum(file) == sha256) { "$name does not match its sha256; not using it" }
    }
    return file
}

val sherpa = fetched(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar",
    "633c24321e06b1fe79feafa03ea16cbc0f8a286641e2da3559bac91bdb13bd96", "sherpa-onnx-1.13.8.aar")
val kwsModel = fetched(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/" +
        "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
    // Not the "-mobile" one: its encoder aborts this onnxruntime on the first frame.
    "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a", "kws-gigaspeech.tar.bz2")

val kws = layout.buildDirectory.dir("generated/kws")
val unpackKws by tasks.registering(Copy::class) {
    from(tarTree(resources.bzip2(kwsModel))) {
        include("**/tokens.txt", "**/*.int8.onnx")
        eachFile {
            path = "kws/" + when {
                name.startsWith("encoder") -> "encoder.onnx"
                name.startsWith("decoder") -> "decoder.onnx"
                name.startsWith("joiner") -> "joiner.onnx"
                else -> name
            }
        }
        includeEmptyDirs = false
    }
    into(kws)
}

android {
    namespace = "dev.onelenyk.heare.phone"
    compileSdk = 36
    defaultConfig {
        applicationId = "dev.onelenyk.heare"
        minSdk = 30
        targetSdk = 36
        versionCode = 920
        versionName = "0.9.20"
        // Pixel phones and watches are arm64; the other three cost 100 MB.
        ndk { abiFilters += "arm64-v8a" }
    }
    buildFeatures { compose = true; buildConfig = true }
    // Signed only where the person's key is: CI unpacks it from a secret, and
    // a build without one stays unsigned rather than pretending to be a release.
    val keystore = System.getenv("HEARE_KEYSTORE")?.takeIf { it.isNotBlank() }?.let(::file)
    signingConfigs {
        if (keystore != null) {
            create("heare") {
                storeFile = keystore
                storePassword = System.getenv("HEARE_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("HEARE_KEY_ALIAS")
                keyPassword = System.getenv("HEARE_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        getByName("release") { signingConfig = signingConfigs.findByName("heare") }
    }
    sourceSets["main"].assets.srcDir(words)
    sourceSets["main"].assets.srcDir(kws)
    // The model is read as it is, not unpacked from a compressed APK.
    androidResources { noCompress += "onnx" }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

tasks.named("preBuild") { dependsOn(makeWords, unpackKws) }

dependencies {
    implementation(project(":shared"))
    implementation(libs.ktor.okhttp)
    implementation(libs.ktor.core)
    implementation(libs.coroutines.android)
    implementation(libs.core.ktx)
    implementation(libs.activity.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.serialization.json)
    implementation(libs.vad.webrtc)
    implementation(files(sherpa))
}
