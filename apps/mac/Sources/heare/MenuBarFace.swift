import AppKit

/// The face in the menu bar: what the assistant is doing, with every window
/// closed. Drawn here on the design system's 120-unit grid (MenuBar group)
/// as a template image, so macOS colours it for a light or dark bar. Only
/// «speaking» moves: four mouth frames, one every 120 ms.
final class MenuBarFace {
    enum Look: Equatable { case idle, hearing, thinking, speaking, off }

    private weak var button: NSStatusBarButton?
    private var look: Look?
    private var frame = 0
    private var timer: Timer?

    init(button: NSStatusBarButton?) {
        self.button = button
        show(.off)
    }

    /// The brain's phase, as its events and its state name it.
    func show(phase: String?) {
        switch phase {
        case "hearing": show(.hearing)
        case "thinking": show(.thinking)
        case "speaking": show(.speaking)
        case nil: show(.off)
        default: show(.idle)
        }
    }

    func show(_ next: Look) {
        guard next != look else { return }
        look = next
        timer?.invalidate()
        timer = nil
        frame = 0
        draw()
        let still = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        if next == .speaking && !still {
            timer = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: true) { [weak self] _ in
                guard let self else { return }
                self.frame = (self.frame + 1) % Self.mouths.count
                self.draw()
            }
        }
    }

    private func draw() {
        guard let look else { return }
        button?.image = Self.image(look, frame: frame)
        button?.setAccessibilityLabel(Self.said[look])
    }

    private static let said: [Look: String] = [
        .idle: "heare: чекає", .hearing: "heare: слухає", .thinking: "heare: думає",
        .speaking: "heare: говорить", .off: "heare: вимкнено",
    ]

    // The mouth's bar heights, frame by frame (design system: menubar-speaking-1…4).
    private static let mouths: [[CGFloat]] = [[10, 24, 14], [20, 12, 26], [14, 28, 10], [24, 16, 18]]

    static func image(_ look: Look, frame: Int = 0) -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { rect in
            let unit = rect.width / 120
            func pill(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) {
                let r = NSRect(x: x * unit, y: y * unit, width: w * unit, height: h * unit)
                NSBezierPath(roundedRect: r, xRadius: w / 2 * unit, yRadius: w / 2 * unit).fill()
            }
            NSColor.black.set()
            let head = NSBezierPath(roundedRect: NSRect(x: 10 * unit, y: 10 * unit, width: 100 * unit, height: 100 * unit),
                                    xRadius: 34 * unit, yRadius: 34 * unit)
            head.lineWidth = 11 * unit
            head.stroke()
            switch look {
            case .idle:
                pill(34, 32, 14, 24); pill(72, 32, 14, 24)
            case .hearing:
                pill(34, 28, 14, 32); pill(72, 28, 14, 32)
            case .thinking:
                pill(39, 26, 14, 24); pill(77, 26, 14, 24)
            case .off:
                pill(34, 42, 14, 9); pill(72, 42, 14, 9)
            case .speaking:
                pill(34, 28, 14, 20); pill(72, 28, 14, 20)
                for (x, h) in zip([39.0, 55, 71] as [CGFloat], mouths[frame % mouths.count]) {
                    pill(x, 78 - h / 2, 10, h)
                }
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}

/// The brain's events, read for one thing: its phase, the moment it changes.
/// Polling the state every few seconds would start the mouth moving seconds
/// after the voice did. Reconnects on its own when the brain restarts.
final class PhaseStream {
    private var task: Task<Void, Never>?

    func start(config: @escaping () -> Config, phase: @escaping (String?) -> Void) {
        task?.cancel()
        task = Task {
            while !Task.isCancelled {
                let now = await MainActor.run { config() }
                if now.brainHere, let token = now.masterToken {
                    var request = URLRequest(url: now.brainBase.appendingPathComponent("api/events"))
                    request.setValue(token, forHTTPHeaderField: "X-Heare-Token")
                    request.timeoutInterval = 30
                    do {
                        let (lines, _) = try await URLSession.shared.bytes(for: request)
                        for try await line in lines.lines {
                            guard line.hasPrefix("data: "),
                                  let data = line.dropFirst(6).data(using: .utf8),
                                  let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                                  event["kind"] as? String == "phase" else { continue }
                            let value = event["phase"] as? String
                            await MainActor.run { phase(value) }
                        }
                    } catch {}
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }
}
