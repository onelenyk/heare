// The app's icon until the real logo is chosen: the menu bar's waveform on
// a rounded square. Writes AppIcon.icns beside the app's Info.plist.
//
//     swift apps/mac/icon/make_icon.swift
import AppKit

let here = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
let mac = here.deletingLastPathComponent()
let set = FileManager.default.temporaryDirectory.appendingPathComponent("AppIcon.iconset")
try? FileManager.default.removeItem(at: set)
try FileManager.default.createDirectory(at: set, withIntermediateDirectories: true)

func draw(_ side: CGFloat) -> Data {
    let image = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
        // Apple's grid: the shape is 824 of 1024, centred.
        let inset = rect.width * 100 / 1024
        let body = rect.insetBy(dx: inset, dy: inset)
        let shape = NSBezierPath(roundedRect: body, xRadius: body.width * 0.225, yRadius: body.width * 0.225)
        NSGradient(starting: NSColor(calibratedRed: 0.45, green: 0.36, blue: 0.95, alpha: 1),
                   ending: NSColor(calibratedRed: 0.22, green: 0.16, blue: 0.52, alpha: 1))!
            .draw(in: shape, angle: -90)
        let config = NSImage.SymbolConfiguration(pointSize: body.width * 0.5, weight: .semibold)
            .applying(.init(paletteColors: [.white]))
        if let wave = NSImage(systemSymbolName: "waveform", accessibilityDescription: nil)?
            .withSymbolConfiguration(config) {
            let size = wave.size
            wave.draw(in: NSRect(x: body.midX - size.width / 2, y: body.midY - size.height / 2,
                                 width: size.width, height: size.height))
        }
        return true
    }
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(side), pixelsHigh: Int(side),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try draw(CGFloat(base)).write(to: set.appendingPathComponent("icon_\(base)x\(base).png"))
    try draw(CGFloat(base * 2)).write(to: set.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
let iconutil = Process()
iconutil.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
iconutil.arguments = ["-c", "icns", set.path, "-o", mac.appendingPathComponent("AppIcon.icns").path]
try iconutil.run()
iconutil.waitUntilExit()
print(iconutil.terminationStatus == 0 ? "AppIcon.icns" : "iconutil failed")
