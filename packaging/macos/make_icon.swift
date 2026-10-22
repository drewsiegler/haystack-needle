// Draws the Haystack Needle app's icon: a needle in a haystack, on the macOS
// icon grid. AppIcon.icns beside this file is what it made, so building the
// app doesn't need it; run it again only to change the icon:
//     swift packaging/macos/make_icon.swift /tmp/AppIcon.iconset
//     iconutil -c icns /tmp/AppIcon.iconset -o packaging/macos/AppIcon.icns
import AppKit

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

// In a 1024-point square, y up.
func draw(_ ctx: CGContext) {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    // The body: a rounded square with a soft shadow, night blue.
    let body = CGPath(roundedRect: CGRect(x: 100, y: 100, width: 824, height: 824),
                      cornerWidth: 185, cornerHeight: 185, transform: nil)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 24, color: color(0x000000, 0.3))
    ctx.addPath(body)
    ctx.setFillColor(color(0x1B3550))
    ctx.fillPath()
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addPath(body)
    ctx.clip()
    let sky = CGGradient(colorsSpace: space, colors: [color(0x2D5A85), color(0x15293F)] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(sky, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])

    // The needle, drawn first so the stack hides its lower end.
    let eyeEnd = CGPoint(x: 812, y: 842), point = CGPoint(x: 560, y: 430)
    ctx.setLineCap(.round)
    ctx.setStrokeColor(color(0x0E1C2B, 0.55))
    ctx.setLineWidth(34)
    ctx.move(to: eyeEnd)
    ctx.addLine(to: point)
    ctx.strokePath()
    ctx.setStrokeColor(color(0xE9EEF2))
    ctx.setLineWidth(24)
    ctx.move(to: eyeEnd)
    ctx.addLine(to: point)
    ctx.strokePath()
    // Its eye: a slot along the needle near the top.
    let along = CGPoint(x: point.x - eyeEnd.x, y: point.y - eyeEnd.y)
    let length = (along.x * along.x + along.y * along.y).squareRoot()
    let unit = CGPoint(x: along.x / length, y: along.y / length)
    ctx.setStrokeColor(color(0x1B3550))
    ctx.setLineWidth(8)
    ctx.move(to: CGPoint(x: eyeEnd.x + unit.x * 14, y: eyeEnd.y + unit.y * 14))
    ctx.addLine(to: CGPoint(x: eyeEnd.x + unit.x * 52, y: eyeEnd.y + unit.y * 52))
    ctx.strokePath()

    // The haystack: a dome of straw on the ground.
    ctx.setFillColor(color(0x000000, 0.25))
    ctx.fillEllipse(in: CGRect(x: 190, y: 228, width: 644, height: 70))
    let stack = CGMutablePath()
    // Its sides meet in a soft peak.
    stack.move(to: CGPoint(x: 210, y: 262))
    stack.addCurve(to: CGPoint(x: 512, y: 748), control1: CGPoint(x: 210, y: 500), control2: CGPoint(x: 388, y: 668))
    stack.addCurve(to: CGPoint(x: 814, y: 262), control1: CGPoint(x: 636, y: 668), control2: CGPoint(x: 814, y: 500))
    stack.closeSubpath()
    ctx.saveGState()
    ctx.addPath(stack)
    ctx.clip()
    let straw = CGGradient(colorsSpace: space, colors: [color(0xF3C863), color(0xD99A33)] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(straw, start: CGPoint(x: 512, y: 748), end: CGPoint(x: 512, y: 262),
                           options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    // Strands of straw.
    ctx.setStrokeColor(color(0xB97921, 0.55))
    ctx.setLineWidth(12)
    ctx.setLineCap(.round)
    for (x, lean) in [(300, -40), (380, -24), (460, -10), (540, 8), (620, 22), (700, 38)] as [(CGFloat, CGFloat)] {
        ctx.move(to: CGPoint(x: x + lean * 0.4, y: 290))
        ctx.addQuadCurve(to: CGPoint(x: x + lean, y: 640), control: CGPoint(x: x + lean * 1.4, y: 470))
        ctx.strokePath()
    }
    ctx.setStrokeColor(color(0xFFE29A, 0.7))
    ctx.setLineWidth(8)
    for (x, lean) in [(340, -30), (500, 0), (660, 30)] as [(CGFloat, CGFloat)] {
        ctx.move(to: CGPoint(x: x + lean * 0.4, y: 330))
        ctx.addQuadCurve(to: CGPoint(x: x + lean, y: 600), control: CGPoint(x: x + lean * 1.3, y: 470))
        ctx.strokePath()
    }
    ctx.restoreGState()
    // A few loose straws across its edge.
    ctx.setStrokeColor(color(0xE2A73F))
    ctx.setLineWidth(10)
    for (from, to) in [((300, 560), (236, 534)), ((440, 690), (404, 744)), ((594, 682), (648, 724)),
                       ((540, 730), (552, 790)), ((232, 400), (178, 420))] as [((CGFloat, CGFloat), (CGFloat, CGFloat))] {
        ctx.move(to: CGPoint(x: from.0, y: from.1))
        ctx.addLine(to: CGPoint(x: to.0, y: to.1))
        ctx.strokePath()
    }
    ctx.restoreGState()
}

let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset")
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
for (points, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let pixels = points * scale
    let ctx = CGContext(data: nil, width: pixels, height: pixels, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
    draw(ctx)
    let name = "icon_\(points)x\(points)\(scale == 2 ? "@2x" : "").png"
    let png = NSBitmapImageRep(cgImage: ctx.makeImage()!).representation(using: .png, properties: [:])!
    try png.write(to: out.appendingPathComponent(name))
}
