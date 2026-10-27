import AppKit

/// The menubar app: it starts the keeper and the brain, keeps them up, and
/// shows what they say. It holds no logic of heare's own — what a part is,
/// whether it may run, which keys work: all of that is the keeper's answer.
final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    /// What a second launch says to the heare already running.
    static let showPage = Notification.Name((Bundle.main.bundleIdentifier ?? "heare") + ".show-page")

    private var config = Config.read()
    private let keeper = Child(name: "keeper", arguments: ["-m", "heare.keeper"])
    private let brain = Child(name: "brain", arguments: ["-m", "heare.brain"])
    private var item: NSStatusItem!
    private var face: MenuBarFace!
    private let phases = PhaseStream()
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
    // Chosen in «Видалити heare…»; done once the children have stopped.
    private var removing: Uninstall.Keep?
    // A newer heare, unpacked beside this one; swapped in once they have.
    private var updating = false
    private var replacing: Updater.Ready?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Updater.cleanUp()
        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(openHeare), name: Self.showPage, object: nil)
        NSApp.mainMenu = mainMenu()
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        face = MenuBarFace(button: item.button)
        phases.start(config: { [weak self] in self?.config ?? Config.read() }) { [weak self] phase in
            self?.face.show(phase: phase)
        }
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
            // A body's brain is elsewhere: nothing here is off.
            face.show(config.brainHere ? .off : .idle)
            draw()
            return
        }
        Local.call(config.brainBase.appendingPathComponent("api/state"),
                   headers: ["X-Heare-Token": token]) { [weak self] answer in
            self?.state = answer.status == 200 ? answer.json as? [String: Any] : nil
            // The events say a phase the moment it changes; this keeps the
            // face right when they are not coming (the brain away: «off»).
            if let state = self?.state {
                self?.face.show(phase: state["phase"] as? String ?? "idle")
            } else {
                self?.face.show(.off)
            }
            self?.draw()
            self?.updateIfAsked()
        }
    }

    /// The brain has a newer heare downloaded, checked and backed up for: put
    /// it in. Unpacking is slow, so it happens off the main thread first.
    private func updateIfAsked() {
        guard !updating, let update = state?["update"] as? [String: Any],
              update["state"] as? String == "installing",
              let dmg = update["dmg"] as? String, let version = update["version"] as? String else { return }
        updating = true
        DispatchQueue.global().async {
            let prepared = Updater.prepare(dmg: dmg, version: version)
            DispatchQueue.main.async { [weak self] in
                switch prepared {
                case .success(let ready):
                    self?.replacing = ready
                    NSApp.terminate(nil)
                case .failure(let failure):
                    self?.updateFailed(failure.why)
                }
            }
        }
    }

    private func updateFailed(_ why: String) {
        NSLog("heare: update: %@", why)
        updating = false
        guard let token = config.masterToken else { return }
        Local.call(config.brainBase.appendingPathComponent("api/update/failed"), method: "POST",
                   body: ["why": why], headers: ["X-Heare-Token": token]) { _ in }
    }

    private static let phases = ["idle": "waiting", "hearing": "listening",
                                 "thinking": "thinking", "speaking": "speaking"]

    private func status() -> String {
        if !config.brainHere {
            return config.url.isEmpty ? tr("Body · the brain is elsewhere") : tr("Body · the brain: {url}", ["url": config.url])
        }
        guard let state else { return tr("The brain is waking up…") }
        let model = state["model"] as? [String: Any]
        let name = (model?["name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "heare"
        let phase = state["phase"] as? String ?? ""
        return "\(name) · \(tr(Self.phases[phase] ?? phase))"
    }

    // MARK: - the menu

    func menuWillOpen(_ menu: NSMenu) { look() }

    private func draw() {
        menu.removeAllItems()
        let line = NSMenuItem(title: status(), action: nil, keyEquivalent: "")
        line.isEnabled = false
        menu.addItem(line)
        if let update = state?["update"] as? [String: Any], update["state"] as? String == "available",
           let latest = update["latest"] as? String {
            menu.addItem(action(tr("heare {v} is out — update…", ["v": latest]), #selector(openHeare), key: ""))
        }
        menu.addItem(.separator())
        menu.addItem(action(tr("Open heare"), #selector(openHeare), key: "o"))

        let parts = NSMenuItem(title: tr("Parts"), action: nil, keyEquivalent: "")
        let list = NSMenu()
        let known = machine?["parts"] as? [[String: Any]] ?? []
        if known.isEmpty {
            let none = NSMenuItem(title: tr("the keeper has not answered yet"), action: nil, keyEquivalent: "")
            none.isEnabled = false
            list.addItem(none)
        }
        for part in known {
            guard let name = part["name"] as? String else { continue }
            let label = tr(part["label"] as? String ?? name)
            let why = part["why_not"] as? String
            let entry = NSMenuItem(title: why.map { "\(label) — \($0)" } ?? label,
                                   action: why == nil ? #selector(toggle(_:)) : nil, keyEquivalent: "")
            entry.target = self
            entry.representedObject = part
            entry.state = part["on"] as? Bool == true ? .on : .off
            entry.toolTip = (part["what"] as? String).map { tr($0) }
            entry.isEnabled = why == nil
            list.addItem(entry)
        }
        parts.submenu = list
        menu.addItem(parts)
        menu.addItem(.separator())
        menu.addItem(action(tr("Settings…"), #selector(openSettings), key: ","))
        let login = action(tr("Start at login"), #selector(toggleLogin), key: "")
        login.state = Login.on ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        menu.addItem(action(tr("About heare"), #selector(about), key: ""))
        menu.addItem(action(tr("Remove heare…"), #selector(uninstall), key: ""))
        menu.addItem(action(tr("Quit"), #selector(quit), key: "q"))
    }

    private func action(_ title: String, _ selector: Selector, key: String) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        entry.target = self
        return entry
    }

    @objc private func toggleLogin() {
        Login.set(!Login.on)
        draw()
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

    // A click on the Dock icon with no window open: the page.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { openHeare() }
        return true
    }

    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func uninstall() {
        guard let keep = Uninstall.ask() else { return }
        removing = keep
        NSApp.terminate(nil)
    }

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

    @objc private func leave() {
        if let keep = removing {
            for line in Uninstall.run(keep: keep) { NSLog("heare: %@", line) }
        }
        if let ready = replacing, !Updater.swap(ready) {
            // This app is back in its place; the brain will say it failed
            // when it is asked again. Starting over is the simplest truth.
            NSLog("heare: update: the new version could not be put in; the old one stays")
        }
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    /// The menu at the top while a window is open (Presence). Without an Edit
    /// menu ⌘V does nothing in a web view — a key could not be pasted.
    private func mainMenu() -> NSMenu {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            let holder = NSMenuItem()
            holder.submenu = menu
            main.addItem(holder)
            return menu
        }
        func plain(_ title: String, _ selector: Selector, _ key: String,
                   _ modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let entry = NSMenuItem(title: title, action: selector, keyEquivalent: key)
            entry.keyEquivalentModifierMask = modifiers
            return entry
        }

        _ = submenu("heare", [
            action(tr("About heare"), #selector(about), key: ""),
            action(tr("Check for updates…"), #selector(checkForUpdates), key: ""),
            .separator(),
            action(tr("Settings…"), #selector(openSettings), key: ","),
            .separator(),
            plain(tr("Hide heare"), #selector(NSApplication.hide(_:)), "h"),
            plain(tr("Hide others"), #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            plain(tr("Show all"), #selector(NSApplication.unhideAllApplications(_:)), ""),
            .separator(),
            action(tr("Quit heare"), #selector(quit), key: "q"),
        ])
        _ = submenu(tr("Edit"), [
            plain(tr("Undo"), Selector(("undo:")), "z"),
            plain(tr("Redo"), Selector(("redo:")), "Z"),
            .separator(),
            plain(tr("Cut"), #selector(NSText.cut(_:)), "x"),
            plain(tr("Copy"), #selector(NSText.copy(_:)), "c"),
            plain(tr("Paste"), #selector(NSText.paste(_:)), "v"),
            plain(tr("Select all"), #selector(NSText.selectAll(_:)), "a"),
        ])
        let window = submenu(tr("Window"), [
            plain(tr("Minimize"), #selector(NSWindow.performMiniaturize(_:)), "m"),
            plain(tr("Close"), #selector(NSWindow.performClose(_:)), "w"),
            .separator(),
            action(tr("heare console"), #selector(openHeare), key: "0"),
        ])
        NSApp.windowsMenu = window
        let help = submenu(tr("Help"), [
            action(tr("Guide"), #selector(openGuide), key: ""),
            action(tr("Report a problem…"), #selector(reportProblem), key: ""),
            .separator(),
            action(tr("Open the data folder"), #selector(openData), key: ""),
            action(tr("Open the logs"), #selector(openLogs), key: ""),
        ])
        NSApp.helpMenu = help
        return main
    }

    // MARK: - about, help, updates

    private static let repository = URL(string: "https://github.com/onelenyk/heare")!

    @objc private func about() {
        NSApp.activate(ignoringOtherApps: true)
        let text = NSMutableAttributedString(
            string: tr("A voice assistant for the Mac.") + "\n\nPolyForm Shield 1.0.0\n",
            attributes: [.font: NSFont.systemFont(ofSize: 11),
                         .foregroundColor: NSColor.secondaryLabelColor])
        text.append(NSAttributedString(string: "github.com/onelenyk/heare", attributes: [
            .font: NSFont.systemFont(ofSize: 11), .link: Self.repository]))
        let centre = NSMutableParagraphStyle()
        centre.alignment = .center
        text.addAttribute(.paragraphStyle, value: centre, range: NSRange(location: 0, length: text.length))
        NSApp.orderFrontStandardAboutPanel(options: [.credits: text])
    }

    @objc private func checkForUpdates() {
        openHeare()
        guard config.brainHere, let token = config.masterToken else { return }
        // The page shows what was found: the banner above the conversation.
        Local.call(config.brainBase.appendingPathComponent("api/update/check"), method: "POST",
                   body: [:], headers: ["X-Heare-Token": token]) { [weak self] _ in self?.look() }
    }

    @objc private func openGuide() {
        NSWorkspace.shared.open(Self.repository.appendingPathComponent("blob/main/README.md"))
    }

    @objc private func reportProblem() {
        NSWorkspace.shared.open(Self.repository.appendingPathComponent("issues/new"))
    }

    @objc private func openData() { NSWorkspace.shared.open(Config.heare) }

    @objc private func openLogs() { NSWorkspace.shared.open(Config.heare.appendingPathComponent("logs")) }
}
