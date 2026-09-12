import AppKit

let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let rect = NSRect(x: 0, y: 0, width: 1024, height: 1024)
let background = NSBezierPath(roundedRect: rect, xRadius: 220, yRadius: 220)
NSGradient(starting: NSColor(calibratedRed: 0.12, green: 0.28, blue: 0.24, alpha: 1),
           ending: NSColor(calibratedRed: 0.035, green: 0.11, blue: 0.10, alpha: 1))!.draw(in: background, angle: -70)
NSColor(calibratedRed: 0.81, green: 0.94, blue: 0.85, alpha: 1).setFill()
let heights: [CGFloat] = [100, 210, 330, 470, 330, 210, 100]
for (index, height) in heights.enumerated() {
    let bar = NSRect(x: 260 + CGFloat(index) * 76, y: (1024-height)/2, width: 48, height: height)
    NSBezierPath(roundedRect: bar, xRadius: 24, yRadius: 24).fill()
}
image.unlockFocus()
let representation = NSBitmapImageRep(data: image.tiffRepresentation!)!
try representation.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
