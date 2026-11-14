import AppKit
import ScreenCaptureKit
import Vision

// MARK: - drag rectangle

/// A transparent full-screen window per display that records one drag, in global Cocoa coordinates.
@MainActor
final class RegionPicker {
    private var windows: [NSWindow] = []
    private var done: ((NSRect?) -> Void)?

    func begin(_ completion: @escaping (NSRect?) -> Void) {
        cancel()
        done = completion
        for screen in NSScreen.screens {
            let w = NSWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
            w.level = .screenSaver
            w.backgroundColor = NSColor.black.withAlphaComponent(0.12)
            w.isOpaque = false
            w.ignoresMouseEvents = false
            w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            let v = DragView(frame: NSRect(origin: .zero, size: screen.frame.size))
            v.onFinish = { [weak self] local in
                guard let self else { return }
                let global = local.map { $0.offsetBy(dx: screen.frame.minX, dy: screen.frame.minY) }
                self.finish(global)
            }
            w.contentView = v
            w.makeKeyAndOrderFront(nil)
            windows.append(w)
        }
        NSCursor.crosshair.push()
        NSApp.activate(ignoringOtherApps: true)
    }

    func cancel() { finish(nil) }

    private func finish(_ rect: NSRect?) {
        guard !windows.isEmpty || done != nil else { return }
        windows.forEach { $0.orderOut(nil) }
        if !windows.isEmpty { NSCursor.pop() }
        windows = []
        let cb = done
        done = nil
        cb?(rect.flatMap { $0.width > 4 && $0.height > 4 ? $0 : nil })
    }
}

final class DragView: NSView {
    var onFinish: ((NSRect?) -> Void)?
    private var start: NSPoint?
    private var current: NSRect?

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func keyDown(with event: NSEvent) { if event.keyCode == 53 { onFinish?(nil) } }
    override func mouseDown(with event: NSEvent) { start = convert(event.locationInWindow, from: nil) }
    override func mouseDragged(with event: NSEvent) {
        guard let start else { return }
        let p = convert(event.locationInWindow, from: nil)
        current = NSRect(x: min(start.x, p.x), y: min(start.y, p.y), width: abs(p.x - start.x), height: abs(p.y - start.y))
        needsDisplay = true
    }
    override func mouseUp(with event: NSEvent) { onFinish?(current) }
    override func draw(_ dirtyRect: NSRect) {
        guard let r = current else { return }
        NSColor.clear.setFill()
        r.fill(using: .copy)
        Palette.accentNS.setStroke()
        let path = NSBezierPath(roundedRect: r, xRadius: 6, yRadius: 6)
        path.lineWidth = 2
        path.stroke()
    }
}

// MARK: - halo around the chosen region

/// Reach's halo: a soft ring that breathes around the region while the question about it is live.
@MainActor
final class RegionHalo {
    private var window: NSWindow?

    func show(around rect: NSRect) {
        hide()
        let pad: CGFloat = 10
        let frame = rect.insetBy(dx: -pad, dy: -pad)
        let w = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
        w.level = .floating
        w.backgroundColor = .clear
        w.isOpaque = false
        w.ignoresMouseEvents = true
        w.hasShadow = false
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        w.sharingType = .none   // never appears in Reach's own captures
        let v = NSView(frame: NSRect(origin: .zero, size: frame.size))
        v.wantsLayer = true
        let ring = CAShapeLayer()
        ring.path = CGPath(roundedRect: v.bounds.insetBy(dx: 4, dy: 4), cornerWidth: 10, cornerHeight: 10, transform: nil)
        ring.fillColor = nil
        ring.strokeColor = Palette.accentNS.cgColor
        ring.lineWidth = 2.5
        ring.shadowColor = Palette.accentNS.cgColor
        ring.shadowRadius = 8
        ring.shadowOpacity = 0.9
        ring.shadowOffset = .zero
        let breathe = CABasicAnimation(keyPath: "opacity")
        breathe.fromValue = 1.0
        breathe.toValue = 0.35
        breathe.duration = 1.1
        breathe.autoreverses = true
        breathe.repeatCount = .infinity
        breathe.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        ring.add(breathe, forKey: "breathe")
        v.layer?.addSublayer(ring)
        w.contentView = v
        w.orderFrontRegardless()
        window = w
    }

    func hide() {
        window?.orderOut(nil)
        window = nil
    }
}

// MARK: - capture + OCR

enum CaptureError: Error, CustomStringConvertible {
    case permission, noDisplay, failed(String)
    var description: String {
        switch self {
        case .permission: return "Screen Recording is off for Reach: typed asks still work"
        case .noDisplay: return "no display under the selection"
        case .failed(let s): return "capture failed: \(s)"
        }
    }
}

enum RegionCapture {
    /// Captures `rect` (global Cocoa coordinates) with ScreenCaptureKit and writes a PNG into `dir`.
    static func capture(_ rect: NSRect, into dir: URL) async throws -> (CGImage, URL) {
        guard CGPreflightScreenCaptureAccess() else { throw CaptureError.permission }
        guard let screen = await MainActor.run(body: { NSScreen.screens.first { $0.frame.intersects(rect) } }),
              let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID
        else { throw CaptureError.noDisplay }
        let content: SCShareableContent
        do { content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) }
        catch { throw CaptureError.permission }
        guard let display = content.displays.first(where: { $0.displayID == number }) else { throw CaptureError.noDisplay }

        // Display-local, top-left origin, in points.
        let local = rect.intersection(screen.frame)
        let src = CGRect(x: local.minX - screen.frame.minX,
                         y: screen.frame.maxY - local.maxY,
                         width: local.width, height: local.height)
        let scale = screen.backingScaleFactor
        let config = SCStreamConfiguration()
        config.sourceRect = src
        config.width = Int(src.width * scale)
        config.height = Int(src.height * scale)
        config.showsCursor = false
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let image: CGImage
        do { image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) }
        catch { throw CaptureError.failed(error.localizedDescription) }

        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("region-\(Int(Date().timeIntervalSince1970 * 1000)).png")
        let rep = NSBitmapImageRep(cgImage: image)
        guard let png = rep.representation(using: .png, properties: [:]) else { throw CaptureError.failed("png") }
        try png.write(to: url)
        return (image, url)
    }

    /// Reads text locally with Vision; nothing leaves the machine here.
    static func recognizeText(_ image: CGImage) async -> String {
        await withCheckedContinuation { cont in
            let req = VNRecognizeTextRequest { req, _ in
                let lines = (req.results as? [VNRecognizedTextObservation] ?? []).compactMap { $0.topCandidates(1).first?.string }
                cont.resume(returning: lines.joined(separator: "\n"))
            }
            req.recognitionLevel = .accurate
            req.usesLanguageCorrection = true
            do { try VNImageRequestHandler(cgImage: image).perform([req]) }
            catch { cont.resume(returning: "") }
        }
    }
}
