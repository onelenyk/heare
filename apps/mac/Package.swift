// swift-tools-version:5.9
// The menubar shell around heare: no Xcode project, `swift build` and
// build.sh make the bundle.
import PackageDescription

let package = Package(
    name: "heare",
    platforms: [.macOS(.v13)],  // SMAppService, for starting at login
    targets: [
        .executableTarget(name: "heare", path: "Sources/heare"),
    ]
)
