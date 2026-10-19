import Foundation

/// One Python process the app keeps alive: the keeper or the brain.
///
/// The app's own child, not launchd's: macOS asks for the microphone on
/// behalf of the app that started the process, so heare.app — not a
/// "python3" nobody recognises — is what the person allows.
final class Child {
    let name: String
    private let arguments: [String]
    private var process: Process?
    private var wanted = false
    private var startedAt = Date()
    private var delay: TimeInterval = 1

    // A child that lived this long was not crashing at start: the next
    // fall begins the backoff from the beginning again.
    private static let healthy: TimeInterval = 30
    private static let longestDelay: TimeInterval = 60
    private static let logLimit: UInt64 = 5 * 1024 * 1024

    init(name: String, arguments: [String]) {
        self.name = name
        self.arguments = arguments
    }

    var running: Bool { process?.isRunning ?? false }
    var pid: pid_t? { running ? process?.processIdentifier : nil }

    static var python: URL {
        Bundle.main.resourceURL!.appendingPathComponent("python/bin/python3")
    }

    func start() {
        wanted = true
        launch()
    }

    /// Ask it to go; make it go if it has not after `grace` seconds.
    func stop(grace: TimeInterval = 8) {
        wanted = false
        guard let process, process.isRunning else { return }
        process.terminate()
        let pid = process.processIdentifier
        DispatchQueue.global().asyncAfter(deadline: .now() + grace) {
            if process.isRunning { kill(pid, SIGKILL) }
        }
    }

    private func launch() {
        guard wanted, process == nil else { return }
        let process = Process()
        process.executableURL = Child.python
        process.arguments = arguments
        process.environment = Child.environment
        process.standardInput = FileHandle.nullDevice
        if let log = openLog() {
            process.standardOutput = log
            process.standardError = log
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async { self?.fell(finished) }
        }
        do {
            try process.run()
        } catch {
            NSLog("heare: %@ did not start: %@", name, error.localizedDescription)
            again()
            return
        }
        self.process = process
        startedAt = Date()
    }

    private func fell(_ finished: Process) {
        guard finished === process else { return }
        process = nil
        guard wanted else { return }
        NSLog("heare: %@ fell (code %d)", name, finished.terminationStatus)
        if Date().timeIntervalSince(startedAt) > Child.healthy { delay = 1 }
        again()
    }

    private func again() {
        let wait = delay
        delay = min(delay * 2, Child.longestDelay)
        DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in self?.launch() }
    }

    private func openLog() -> FileHandle? {
        let folder = Config.heare.appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let path = folder.appendingPathComponent("app-\(name).log").path
        // One old log is kept: enough to see why the last run fell.
        if let size = (try? FileManager.default.attributesOfItem(atPath: path))?[.size] as? UInt64,
           size > Child.logLimit {
            try? FileManager.default.removeItem(atPath: path + ".1")
            try? FileManager.default.moveItem(atPath: path, toPath: path + ".1")
        }
        // O_APPEND: the keeper's parts inherit this descriptor too, and every
        // writer must land at the end rather than over each other.
        let fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        guard fd >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        let stamp = ISO8601DateFormatter().string(from: Date())
        handle.write("\n== \(stamp) python \(arguments.joined(separator: " "))\n".data(using: .utf8)!)
        return handle
    }

    /// What every child runs with. The parts the keeper starts inherit it.
    static let environment: [String: String] = {
        var env = ProcessInfo.processInfo.environment
        // The bundled Python is the whole world: nothing from a Python the
        // person may have installed leaks in.
        env.removeValue(forKey: "PYTHONPATH")
        env.removeValue(forKey: "PYTHONHOME")
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        // Bytecode goes beside the data, never into the bundle.
        env["PYTHONPYCACHEPREFIX"] = Config.heare.appendingPathComponent("cache/pycache").path
        // The app shows the installer in its own window; the keeper must
        // not also open it in a browser.
        env["HEARE_NO_BROWSER"] = "1"
        // The brain and the keeper watch this: if the app is killed and
        // cannot stop them, they stop themselves instead of holding the
        // ports against the next app (heare.lifeline).
        env["HEARE_PARENT_PID"] = String(getpid())
        // An app opened from Finder gets launchd's bare PATH; hands look for
        // an agent CLI where installers usually put one.
        let home = Config.home.path
        let extra = ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin",
                     "\(home)/.opencode/bin", "\(home)/.bun/bin"]
        let path = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        let have = Set(path.split(separator: ":").map(String.init))
        env["PATH"] = ([path] + extra.filter { !have.contains($0) }).joined(separator: ":")
        return env
    }()
}
