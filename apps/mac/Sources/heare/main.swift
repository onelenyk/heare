import AppKit

// `heare --uninstall keep-memory|all`: the menu's «Видалити heare…» from a
// terminal, with no window. heare must not be running: it holds the files.
let arguments = CommandLine.arguments
if let at = arguments.firstIndex(of: "--uninstall") {
    guard at + 1 < arguments.count, let keep = Uninstall.Keep(rawValue: arguments[at + 1]) else {
        FileHandle.standardError.write("usage: heare --uninstall keep-memory|all\n".data(using: .utf8)!)
        exit(2)
    }
    let me = ProcessInfo.processInfo.processIdentifier
    let others = NSRunningApplication.runningApplications(
        withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
        .filter { $0.processIdentifier != me }
    if !others.isEmpty {
        FileHandle.standardError.write("heare ще працює — спершу «Вийти» в його меню\n".data(using: .utf8)!)
        exit(1)
    }
    for line in Uninstall.run(keep: keep) { print(line) }
    exit(0)
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)

// A logout or `kill` sends SIGTERM: leave the way "Вийти" does, taking the
// keeper and the brain along, instead of dying and orphaning them.
var stops: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { NSApp.terminate(nil) }
    source.resume()
    stops.append(source)
}

app.run()
