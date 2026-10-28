import Foundation

/// The few settings the shell reads from ~/.heare/settings/config.toml.
///
/// Not a TOML parser: the Python side writes this file (Settings.set), and
/// it writes top-level `name = value` lines with JSON-quoted strings. The
/// shell needs four of them and must not drift from what Python reads, so
/// anything it cannot read falls back to the same defaults as config.py.
struct Config {
    var role = ""
    var setupDone = false
    var apiPort = 9790
    var setupPort = 9791
    var url = ""
    /// What the person hears and sees: uk or en, as config.py says.
    var language = "en"
    var apiTokenFile: String

    /// $HOME first, as Python's Path.home() does: the shell and its
    /// children must agree on where ~/.heare is.
    static let home: URL = {
        if let home = ProcessInfo.processInfo.environment["HOME"], !home.isEmpty {
            return URL(fileURLWithPath: home)
        }
        return URL(fileURLWithPath: NSHomeDirectory())
    }()
    static let heare = home.appendingPathComponent(".heare")
    /// In its section since 25 October; at the top before the first child
    /// has moved it (heare.layout), and a link is left there after.
    static var file: URL { inSection("settings", "config.toml") }

    init() {
        apiTokenFile = Config.inSection("machine", "api_token").path
    }

    static func inSection(_ section: String, _ name: String) -> URL {
        let now = heare.appendingPathComponent(section).appendingPathComponent(name)
        return FileManager.default.fileExists(atPath: now.path) ? now : heare.appendingPathComponent(name)
    }

    static func read() -> Config {
        var config = Config()
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return config }
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            // Top-level keys end where the first table begins.
            if line.hasPrefix("[") { break }
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let name = line[..<eq].trimmingCharacters(in: .whitespaces)
            let value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            switch name {
            case "role": config.role = string(value) ?? config.role
            case "setup_done": config.setupDone = value == "true"
            case "api_port": config.apiPort = Int(value) ?? config.apiPort
            case "setup_port": config.setupPort = Int(value) ?? config.setupPort
            case "url": config.url = string(value) ?? config.url
            case "language": config.language = string(value) ?? config.language
            case "api_token_file":
                if let path = string(value) { config.apiTokenFile = (path as NSString).expandingTildeInPath }
            default: continue
            }
        }
        return config
    }

    private static func string(_ value: String) -> String? {
        guard value.count >= 2, let quote = value.first, quote == "\"" || quote == "'",
              let data = value.data(using: .utf8) else { return nil }
        if quote == "'" { return String(value.dropFirst().dropLast()) }
        return (try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])) as? String
    }

    /// The brain lives here unless this machine is only a body.
    var brainHere: Bool { role != "body" }

    var brainBase: URL { URL(string: "http://127.0.0.1:\(apiPort)")! }
    var setupBase: URL { URL(string: "http://127.0.0.1:\(setupPort)")! }

    /// The master token. The shell runs as the person, so it may read what
    /// the person may read; it never writes or passes this anywhere else.
    var masterToken: String? {
        (try? String(contentsOfFile: apiTokenFile, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
