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

android {
    namespace = "dev.onelenyk.heare.phone"
    compileSdk = 36
    defaultConfig {
        applicationId = "dev.onelenyk.heare"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.9.4"
    }
    buildFeatures { compose = true; buildConfig = true }
    sourceSets["main"].assets.srcDir(words)
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

tasks.named("preBuild") { dependsOn(makeWords) }

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
}
