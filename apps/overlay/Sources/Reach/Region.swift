import AppKit
import ReachCore
import ScreenCaptureKit
import Vision

// MARK: - circle (freehand lasso)

/// A transparent full-screen layer per display that records one freehand loop with the user's own mouse, drawn live
/// as a soft glowing line. On release the loop closes itself. Points come back in global Cocoa coordinates.
@MainActor
final class LassoPicker {
    private var windows: [NSWindow] = []
    private var done: ((LassoRecorder.Outcome) -> Void)?
    var log: ((String) -> Void)?

    func begin(_ completion: @escaping (LassoRecorder.Outcome) -> Void) {
        cancel()
        done = completion
        for screen in NSScreen.screens {
            let w = KeyableWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
            w.level = .screenSaver
            w.backgroundColor = NSColor.black.withAlphaComponent(0.10)
            w.isOpaque = false
            w.ignoresMouseEvents = false
            w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            w.sharingType = .none   // never in Reach's own capture
            let v = LassoView(frame: NSRect(origin: .zero, size: screen.frame.size))
            v.origin = screen.frame.origin
            v.onState = { [weak self] st in self?.log?("lasso: \(st.rawValue)") }
            v.onFinish = { [weak self] outcome in self?.finish(outcome) }
            w.contentView = v
            w.makeKeyAndOrderFront(nil)
            w.makeFirstResponder(v)
            windows.append(w)
        }
        NSCursor.crosshair.push()
        NSApp.activate(ignoringOtherApps: true)
        log?("lasso: idle (drag a loop around something; Esc cancels)")
    }

    func cancel() { if done != nil { finish(.cancelled) } }

    private func finish(_ outcome: LassoRecorder.Outcome) {
        let ws = windows
        windows = []
        if !ws.isEmpty { NSCursor.pop() }
        // let the closed loop linger a moment, then fade the layer out
        let delay: Double = { if case .closed = outcome { return 0.25 }; return 0 }()
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = 0.2
                ws.forEach { $0.animator().alphaValue = 0 }
            }, completionHandler: { MainActor.assumeIsolated { ws.forEach { $0.orderOut(nil) } } })
        }
        let cb = done
        done = nil
        cb?(outcome)
    }
}

/// Borderless windows cannot become key by default, so Esc would never reach the lasso layer.
final class KeyableWindow: NSWindow {
    override var canBecomeKey: Bool { true }
}

final class LassoView: NSView {
    var origin: CGPoint = .zero
    var onFinish: ((LassoRecorder.Outcome) -> Void)?
    var onState: ((LassoRecorder.State) -> Void)?
    private var rec = LassoRecorder()
    private var closed = false

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { rec.cancel(); onState?(.cancelled); onFinish?(.cancelled) }
    }
    private func global(_ e: NSEvent) -> CGPoint {
        var p = convert(e.locationInWindow, from: nil)
        // stay on this display: the capture is clipped to it, so the mask must be too
        p.x = min(max(p.x, bounds.minX), bounds.maxX)
        p.y = min(max(p.y, bounds.minY), bounds.maxY)
        return CGPoint(x: p.x + origin.x, y: p.y + origin.y)
    }
    override func mouseDown(with event: NSEvent) {
        closed = false
        rec.begin(global(event)); onState?(.drawing); needsDisplay = true
    }
    override func mouseDragged(with event: NSEvent) { rec.add(global(event)); needsDisplay = true }
    override func mouseUp(with event: NSEvent) {
        rec.add(global(event))
        let outcome = rec.end()
        onState?(rec.state)
        if case .closed = outcome { closed = true; needsDisplay = true }
        onFinish?(outcome)
    }
    override func draw(_ dirtyRect: NSRect) {
        let pts = rec.points.map { CGPoint(x: $0.x - origin.x, y: $0.y - origin.y) }
        guard pts.count > 1 else { return }
        let path = NSBezierPath()
        path.move(to: pts[0])
        pts.dropFirst().forEach { path.line(to: $0) }
        if closed { path.close() }
        path.lineCapStyle = .round
        path.lineJoinStyle = .round
        NSGraphicsContext.saveGraphicsState()
        let glow = NSShadow()
        glow.shadowColor = Palette.accentNS
        glow.shadowBlurRadius = 10
        glow.set()
        Palette.accentNS.withAlphaComponent(0.9).setStroke()
        path.lineWidth = 3
        path.stroke()
        NSGraphicsContext.restoreGraphicsState()
        if closed {
            Palette.accentNS.withAlphaComponent(0.08).setFill()
            path.fill()
        }
    }
}

// MARK: - halo around the chosen region

/// Reach's halo: a soft ring that breathes around the region while the question about it is live.
@MainActor
final class RegionHalo {
    private var window: NSWindow?

    /// A gentle halo that fades in along the loop (global Cocoa points).
    func show(loop: [CGPoint]) {
        let box = Lasso.boundingBox(loop)
        show(around: box, path: loop)
    }

    func show(around rect: NSRect, path loop: [CGPoint]? = nil) {
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
        if let loop, loop.count >= 3 {
            let p = CGMutablePath()
            p.addLines(between: loop.map { CGPoint(x: $0.x - frame.minX, y: $0.y - frame.minY) })
            p.closeSubpath()
            ring.path = p
            ring.lineJoin = .round
        } else {
            ring.path = CGPath(roundedRect: v.bounds.insetBy(dx: 4, dy: 4), cornerWidth: 10, cornerHeight: 10, transform: nil)
        }
        let fadeIn = CABasicAnimation(keyPath: "strokeEnd")
        fadeIn.fromValue = 0
        fadeIn.toValue = 1
        fadeIn.duration = 0.45
        fadeIn.timingFunction = CAMediaTimingFunction(name: .easeOut)
        ring.add(fadeIn, forKey: "draw")
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

nonisolated(unsafe) private var askedForCapture = false

enum RegionCapture {
    /// Captures `rect` (global Cocoa coordinates) with ScreenCaptureKit and writes a PNG into `dir`.
    static func capture(_ rect: NSRect, into dir: URL) async throws -> (CGImage, URL) {
        guard CGPreflightScreenCaptureAccess() else {
            // Ask macOS once per run so the first capture shows the system prompt; then the panel says "needs you".
            if !askedForCapture { askedForCapture = true; _ = CGRequestScreenCaptureAccess() }
            throw CaptureError.permission
        }
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

    /// Captures the loop's bounding box, masks everything outside the loop (transparent) and saves that PNG.
    static func captureLoop(_ loop: [CGPoint], box: NSRect, into dir: URL) async throws -> (CGImage, URL) {
        let (raw, url) = try await capture(box, into: dir)
        let scale = CGFloat(raw.width) / max(box.width, 1)
        guard let masked = Lasso.mask(raw, polygon: Lasso.toImageSpace(loop, box: box, scale: scale)) else {
            throw CaptureError.failed("mask")
        }
        let rep = NSBitmapImageRep(cgImage: masked)
        guard let png = rep.representation(using: .png, properties: [:]) else { throw CaptureError.failed("png") }
        let out = url.deletingLastPathComponent().appendingPathComponent(
            url.lastPathComponent.replacingOccurrences(of: "region-", with: "circle-"))
        try png.write(to: out)
        try? FileManager.default.removeItem(at: url)   // keep only the masked image
        return (masked, out)
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
