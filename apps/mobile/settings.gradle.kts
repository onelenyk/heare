// heare on the phone and the watch: parts of heare, one shared core.
// iOS joins later as another user of :shared (docs/product/mobile.md).
rootProject.name = "heare-mobile"

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

include(":shared", ":phone")
