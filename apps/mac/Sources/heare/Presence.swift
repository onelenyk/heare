import AppKit

/// Whether heare is in the Dock right now.
///
/// It lives in the menu bar; a window of its own made it «nobody's» — not in
/// the Dock, not in ⌘Tab, no menu at the top. While a window is open heare is
/// an ordinary app; when the last one closes it goes back to the menu bar.
enum Presence {
    private static var open = Set<ObjectIdentifier>()

    static func shown(_ window: NSWindow) {
        open.insert(ObjectIdentifier(window))
        if NSApp.activationPolicy() != .regular { NSApp.setActivationPolicy(.regular) }
    }

    static func closed(_ window: NSWindow) {
        open.remove(ObjectIdentifier(window))
        if open.isEmpty && NSApp.activationPolicy() != .accessory {
            NSApp.setActivationPolicy(.accessory)
        }
    }
}
