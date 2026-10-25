import AppKit

/// Putting a newer heare in place of this one.
///
/// The brain finds, downloads and checks the release, makes a backup, and
/// says `installing` in its state; it cannot swap the app it runs inside.
/// This does: unpack the dmg beside the app as `heare.app.new` while
/// everything still runs, then — once the brain and the keeper have
/// stopped — rename this app to `heare.app.old`, the new one into its
/// place, start it, and leave. The new app clears the `.old` away.
enum Updater {
    struct Ready {
        let fresh: URL
        let version: String
    }

    static var bundle: URL { Bundle.main.bundleURL }
    static var aside: URL { bundle.deletingPathExtension().appendingPathExtension("app.old") }
    static var staged: URL { bundle.deletingPathExtension().appendingPathExtension("app.new") }

    /// What a former update left beside this app.
    static func cleanUp() {
        for leftover in [aside, staged] where FileManager.default.fileExists(atPath: leftover.path) {
            try? FileManager.default.removeItem(at: leftover)
        }
    }

    /// Unpack and check, off the main thread; the reason when it cannot.
    static func prepare(dmg: String, version: String) -> Result<Ready, Failure> {
        let file = URL(fileURLWithPath: dmg).standardizedFileURL
        let allowed = Config.heare.appendingPathComponent("cache/update").standardizedFileURL.path
        guard file.path.hasPrefix(allowed + "/"), FileManager.default.fileExists(atPath: file.path) else {
            return .failure(Failure("оновлення не там, де мозок мав його покласти"))
        }
        guard bundle.pathExtension == "app",
              FileManager.default.isWritableFile(atPath: bundle.deletingLastPathComponent().path) else {
            return .failure(Failure("тека \(bundle.deletingLastPathComponent().path) не записувана — "
                                    + "постав нову версію з dmg вручну"))
        }
        let mount = FileManager.default.temporaryDirectory
            .appendingPathComponent("heare-update-\(UUID().uuidString)")
        defer {
            _ = run("/usr/bin/hdiutil", ["detach", mount.path, "-force", "-quiet"])
            try? FileManager.default.removeItem(at: mount)
        }
        guard run("/usr/bin/hdiutil", ["attach", file.path, "-nobrowse", "-readonly", "-noautoopen",
                                       "-mountpoint", mount.path, "-quiet"]) == 0 else {
            return .failure(Failure("не вдалося відкрити dmg"))
        }
        let inside = mount.appendingPathComponent("heare.app")
        try? FileManager.default.removeItem(at: staged)
        guard run("/usr/bin/ditto", [inside.path, staged.path]) == 0 else {
            return .failure(Failure("не вдалося скопіювати нову версію"))
        }
        let plist = NSDictionary(contentsOf: staged.appendingPathComponent("Contents/Info.plist"))
        guard plist?["CFBundleShortVersionString"] as? String == version else {
            try? FileManager.default.removeItem(at: staged)
            return .failure(Failure("у dmg не та версія, що в релізі"))
        }
        guard run("/usr/bin/codesign", ["--verify", "--deep", staged.path]) == 0 else {
            try? FileManager.default.removeItem(at: staged)
            return .failure(Failure("підпис нової версії не сходиться"))
        }
        return .success(Ready(fresh: staged, version: version))
    }

    /// The swap, once the children have stopped. False — and this app back
    /// in its place — when the new one could not be put in or started.
    /// Either way the app at this path is started again: the new one, or —
    /// when it could not be put in — this one, so heare does not just stop.
    static func swap(_ ready: Ready) -> Bool {
        let files = FileManager.default
        var swapped = false
        do {
            try? files.removeItem(at: aside)
            try files.moveItem(at: bundle, to: aside)
            do {
                try files.moveItem(at: ready.fresh, to: bundle)
                swapped = true
            } catch {
                try files.moveItem(at: aside, to: bundle)
            }
        } catch {
            swapped = false
        }
        if swapped && start() { return true }
        if swapped {
            try? files.removeItem(at: bundle)
            try? files.moveItem(at: aside, to: bundle)
        }
        start()
        return false
    }

    /// The app at this path, as a new instance. The environment goes along:
    /// an e2e sandbox stays a sandbox.
    @discardableResult
    private static func start() -> Bool {
        var arguments = ["-n", "-a", bundle.path]
        for (name, value) in ProcessInfo.processInfo.environment
        where name == "HOME" || name.hasPrefix("HEARE_") && name != "HEARE_PARENT_PID" {
            arguments += ["--env", "\(name)=\(value)"]
        }
        return run("/usr/bin/open", arguments) == 0
    }

    struct Failure: Error {
        let why: String
        init(_ why: String) { self.why = why }
    }

    @discardableResult
    private static func run(_ tool: String, _ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return -1 }
        process.waitUntilExit()
        return process.terminationStatus
    }
}
