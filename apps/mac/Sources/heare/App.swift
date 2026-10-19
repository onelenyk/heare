import AppKit

/// The menubar app: it starts the keeper and the brain, keeps them up, and
/// shows what they say. It holds no logic of heare's own — what a part is,
/// whether it may run, which keys work: all of that is the keeper's answer.
final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var config = Config.read()
    private let keeper = Child(name: "keeper", arguments: ["-m", "heare.keeper"])
    private let brain = Child(name: "brain", arguments: ["-m", "heare.brain"])
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private var timer: Timer?
    private lazy var installer: InstallerWindow = {
        let installer = InstallerWindow()
        installer.onDone = { [weak self] in self?.openHeare() }
        return installer
    }()
    private lazy var page = HeareWindow()

    // What the keeper and the brain last said; the menu is drawn from these.
    private var machine: [String: Any]?
    private var state: [String: Any]?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = editMenu()
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "heare")
        menu.delegate = self
        item.menu = menu
        draw()

        keeper.start()
        if config.brainHere { brain.start() }
        if !config.setupDone { installer.show(config) }

        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.look() }
        look()
    }

    // MARK: - what is going on

    private func look() {
        let ports = (config.apiPort, config.setupPort)
        config = Config.read()
        // A port is bound once: the children keep the one they started with.
        (config.apiPort, config.setupPort) = ports

        // The role is chosen on the installer page, while the app runs: a
        // body has its brain elsewhere, so this one goes.
        if config.brainHere { brain.start() } else if brain.running { brain.stop() }

        Local.call(config.setupBase.appendingPathComponent("machine")) { [weak self] answer in
            self?.machine = answer.status == 200 ? answer.json as? [String: Any] : nil
            self?.draw()
        }
        guard config.brainHere, let token = config.masterToken else {
            state = nil
            draw()
            return
        }
        Local.call(config.brainBase.appendingPathComponent("api/state"),
                   headers: ["X-Heare-Token": token]) { [weak self] answer in
            self?.state = answer.status == 200 ? answer.json as? [String: Any] : nil
            self?.draw()
        }
    }

    private static let phases = ["idle": "чекає", "hearing": "слухає",
                                 "thinking": "думає", "speaking": "говорить"]

    private func status() -> String {
        if !config.brainHere {
            return config.url.isEmpty ? "Тіло · мозок деінде" : "Тіло · мозок: \(config.url)"
        }
        guard let state else { return "Мозок прокидається…" }
        let model = state["model"] as? [String: Any]
        let name = (model?["name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "heare"
        let phase = state["phase"] as? String ?? ""
        return "\(name) · \(Self.phases[phase] ?? phase)"
    }

    // MARK: - the menu

    func menuWillOpen(_ menu: NSMenu) { look() }

    private func draw() {
        menu.removeAllItems()
        let line = NSMenuItem(title: status(), action: nil, keyEquivalent: "")
        line.isEnabled = false
        menu.addItem(line)
        menu.addItem(.separator())
        menu.addItem(action("Відкрити heare", #selector(openHeare), key: "o"))

        let parts = NSMenuItem(title: "Частини", action: nil, keyEquivalent: "")
        let list = NSMenu()
        let known = machine?["parts"] as? [[String: Any]] ?? []
        if known.isEmpty {
            let none = NSMenuItem(title: "наглядач ще не відповів", action: nil, keyEquivalent: "")
            none.isEnabled = false
            list.addItem(none)
        }
        for part in known {
            guard let name = part["name"] as? String else { continue }
            let label = part["label"] as? String ?? name
            let why = part["why_not"] as? String
            let entry = NSMenuItem(title: why.map { "\(label) — \($0)" } ?? label,
                                   action: why == nil ? #selector(toggle(_:)) : nil, keyEquivalent: "")
            entry.target = self
            entry.representedObject = part
            entry.state = part["on"] as? Bool == true ? .on : .off
            entry.toolTip = part["what"] as? String
            entry.isEnabled = why == nil
            list.addItem(entry)
        }
        parts.submenu = list
        menu.addItem(parts)
        menu.addItem(action("Налаштування…", #selector(openSettings), key: ","))
        menu.addItem(.separator())
        menu.addItem(action("Вийти", #selector(quit), key: "q"))
    }

    private func action(_ title: String, _ selector: Selector, key: String) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        entry.target = self
        return entry
    }

    @objc private func toggle(_ sender: NSMenuItem) {
        guard let part = sender.representedObject as? [String: Any],
              let name = part["name"] as? String else { return }
        let on = part["on"] as? Bool != true
        Local.call(config.setupBase.appendingPathComponent("part"), method: "POST",
                   body: ["name": name, "on": on]) { [weak self] answer in
            if answer.status == 200, let fresh = answer.json as? [String: Any] { self?.machine = fresh }
            self?.draw()
        }
    }

    @objc func openHeare() {
        // A body's page is its brain's, on another machine: that browser
        // pairs there, with a code from that brain.
        if !config.brainHere, let url = URL(string: config.url), !config.url.isEmpty {
            NSWorkspace.shared.open(url)
            return
        }
        page.show(config)
    }

    @objc private func openSettings() { installer.show(config) }

    @objc private func quit() { NSApp.terminate(nil) }

    // MARK: - leaving

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        timer?.invalidate()
        // The keeper takes its parts along on SIGTERM and gives each up to
        // five seconds; it gets a little more than that before SIGKILL.
        let grace: TimeInterval = 8
        keeper.stop(grace: grace)
        brain.stop(grace: grace)
        DispatchQueue.global().async { [keeper, brain] in
            let end = Date().addingTimeInterval(grace + 1)
            while (keeper.running || brain.running) && Date() < end { usleep(100_000) }
            // While termination waits, the main run loop runs only in the
            // modal-panel mode, where the main dispatch queue is not drained.
            self.performSelector(onMainThread: #selector(self.leave), with: nil, waitUntilDone: false,
                                 modes: [RunLoop.Mode.common.rawValue, RunLoop.Mode.modalPanel.rawValue])
        }
        return .terminateLater
    }

    @objc private func leave() { NSApp.reply(toApplicationShouldTerminate: true) }

    /// An app without a Dock icon has no menu bar, and without an Edit menu
    /// ⌘V does nothing in a web view — a key could not be pasted.
    private func editMenu() -> NSMenu {
        let main = NSMenu()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        let holder = NSMenuItem()
        holder.submenu = edit
        main.addItem(holder)
        return main
    }
}
