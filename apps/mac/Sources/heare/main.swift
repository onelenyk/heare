import AppKit

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
