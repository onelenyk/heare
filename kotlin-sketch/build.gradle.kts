plugins {
    kotlin("jvm") version "2.3.20"
    application
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.10.2")
}

application {
    mainClass = "heare.AppKt"
}

// Typing reaches the app when it is started with `./gradlew run`.
tasks.named<JavaExec>("run") {
    standardInput = System.`in`
}
