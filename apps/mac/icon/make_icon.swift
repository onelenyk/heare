// The app's icon: the face from the page — a rounded head, two pill eyes,
// a mouth of sound bars — white on the violet of «Нічний фіолет». Chosen on
// the brand canvas, 25 October. Writes AppIcon.icns beside Info.plist.
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
        NSColor(srgbRed: 0x6d / 255, green: 0x58 / 255, blue: 0xc4 / 255, alpha: 1).setFill()
        shape.fill()
        // The face is drawn on a 120-unit square, as on the canvas.
        let unit = body.width / 112
        func at(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
            NSRect(x: body.minX + (x - 4) * unit, y: body.maxY - (y - 4 + h) * unit,
                   width: w * unit, height: h * unit)
        }
        NSColor.white.set()
        let head = NSBezierPath(roundedRect: at(22, 24, 76, 72), xRadius: 26 * unit, yRadius: 26 * unit)
        head.lineWidth = 6 * unit
        head.stroke()
        // The mouth is a sound, not teeth: bars of the heights it speaks in.
        for (x, y, w, h) in [(42.0, 44.0, 9.0, 17.0), (69, 44, 9, 17),
                             (46, 74, 5, 7), (54, 70.5, 5, 14), (62, 72.5, 5, 10), (70, 75, 5, 5)] {
            NSBezierPath(roundedRect: at(x, y, w, h), xRadius: w / 2 * unit, yRadius: w / 2 * unit).fill()
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
