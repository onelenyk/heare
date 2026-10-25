import AppKit

/// Taking heare off this Mac: the keys, the start at login, the data and the
/// app itself. Everything goes to the Trash, never deleted outright — a
/// person who changes their mind takes it back from there.
///
/// It runs once the brain and the keeper have stopped: they hold the files.
enum Uninstall {
    enum Keep: String { case memory = "keep-memory", nothing = "all" }

    /// What "keep the memory" keeps: what a reinstall picks up again.
    static let kept: Set<String> = ["memory", "settings", "backups"]

    /// What it did, one line each, for the terminal or the log.
    @discardableResult
    static func run(keep: Keep) -> [String] {
        var said: [String] = []
        said.append(forgetKeys() ? "ключі прибрано" : "ключі прибрати не вдалося")
        if Login.on {
            Login.set(false)
            said.append("старт із маком вимкнено")
        }
        let home = Config.heare
        if FileManager.default.fileExists(atPath: home.path) {
            switch keep {
            case .nothing:
                said.append(trash(home))
            case .memory:
                let inside = (try? FileManager.default.contentsOfDirectory(
                    at: home, includingPropertiesForKeys: nil)) ?? []
                for item in inside where !kept.contains(item.lastPathComponent) {
                    said.append(trash(item))
                }
                said.append("памʼять лишилась у \(home.path)")
            }
        }
        let app = Bundle.main.bundleURL
        if app.pathExtension == "app" { said.append(trash(app)) }
        let work = Config.home.appendingPathComponent("heare-work")
        if FileManager.default.fileExists(atPath: work.path) {
            said.append("файли агента лишились у \(work.path)")
        }
        return said
    }

    /// The keys live where the Python side keeps them; only it knows which
    /// store that is, and in the e2e sandbox it is not the Keychain.
    private static func forgetKeys() -> Bool {
        let process = Process()
        process.executableURL = Child.python
        process.arguments = ["-m", "heare.keys", "forget-all"]
        process.environment = Child.environment
        do {
            try process.run()
        } catch {
            return false
        }
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    /// The Trash, or the folder HEARE_TRASH names: the e2e run keeps what it
    /// throws away in its own sandbox, not in the person's Trash.
    private static func trash(_ url: URL) -> String {
        let files = FileManager.default
        do {
            if let folder = ProcessInfo.processInfo.environment["HEARE_TRASH"], !folder.isEmpty {
                let into = URL(fileURLWithPath: folder)
                try files.createDirectory(at: into, withIntermediateDirectories: true)
                var target = into.appendingPathComponent(url.lastPathComponent)
                var n = 1
                while files.fileExists(atPath: target.path) {
                    n += 1
                    target = into.appendingPathComponent("\(url.lastPathComponent) \(n)")
                }
                try files.moveItem(at: url, to: target)
            } else {
                try files.trashItem(at: url, resultingItemURL: nil)
            }
            return "у Кошику: \(url.path)"
        } catch {
            return "не вдалося прибрати \(url.path): \(error.localizedDescription)"
        }
    }

    /// The question before it, in the app's own window.
    static func ask() -> Keep? {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Видалити heare?"
        alert.informativeText = """
            heare зупиниться, ключі моделей зникнуть із Keychain, а застосунок піде в Кошик.

            «Залишити памʼять» — розмови, факти, налаштування й копії лишаться в ~/.heare: \
            якщо поставиш heare знову, вона їх підхопить.
            «Стерти все» — і памʼять теж у Кошик.

            Файли, які робив агент (~/heare-work), лишаються.
            """
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Залишити памʼять")
        alert.addButton(withTitle: "Стерти все")
        alert.addButton(withTitle: "Скасувати")
        switch alert.runModal() {
        case .alertFirstButtonReturn: return .memory
        case .alertSecondButtonReturn: return .nothing
        default: return nil
        }
    }
}
