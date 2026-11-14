import AppKit
import AVFoundation
import ReachCore
import SwiftUI

// MARK: - ready-to-circle layer (shown only while Option-Space is held)

/// While the key is held: a soft vignette dims the screen edges, a halo follows the cursor, and a freehand loop can
/// be drawn with the user's own mouse. Non-activating, so the app underneath keeps focus. Global Cocoa points.
@MainActor
final class ReadyLayer {
    private var windows: [NSPanel] = []
    var onCircle: (([CGPoint], CGRect) -> Void)?
    var onTooSmall: (() -> Void)?

    var isShown: Bool { !windows.isEmpty }

    func show() {
        guard windows.isEmpty else { return }
        for screen in NSScreen.screens {
            let w = NSPanel(contentRect: screen.frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered,
                            defer: false)
            w.level = NSWindow.Level(rawValue: NSWindow.Level.floating.rawValue + 1)
            w.backgroundColor = .clear
            w.isOpaque = false
            w.hasShadow = false
            w.ignoresMouseEvents = false
            w.acceptsMouseMovedEvents = true
            w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            w.sharingType = .none
            let v = ReadyView(frame: NSRect(origin: .zero, size: screen.frame.size))
            v.origin = screen.frame.origin
            v.onFinish = { [weak self] outcome in
                switch outcome {
                case .closed(let path, let box): self?.onCircle?(path, box)
                case .tooSmall: self?.onTooSmall?()
                case .cancelled: break
                }
            }
            w.contentView = v
            w.alphaValue = 0
            w.orderFrontRegardless()
            windows.append(w)
        }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.18
            windows.forEach { $0.animator().alphaValue = 1 }
        }
    }

    func hide() {
        let ws = windows
        windows = []
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.18
            ws.forEach { $0.animator().alphaValue = 0 }
        }, completionHandler: { MainActor.assumeIsolated { ws.forEach { $0.orderOut(nil) } } })
    }
}

final class ReadyView: NSView {
    var origin: CGPoint = .zero
    var onFinish: ((LassoRecorder.Outcome) -> Void)?
    private var rec = LassoRecorder()
    private var closed = false
    private var cursor: CGPoint?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: bounds, options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self))
    }
    private func local(_ e: NSEvent) -> CGPoint {
        let p = convert(e.locationInWindow, from: nil)
        return CGPoint(x: min(max(p.x, bounds.minX), bounds.maxX), y: min(max(p.y, bounds.minY), bounds.maxY))
    }
    private func global(_ p: CGPoint) -> CGPoint { CGPoint(x: p.x + origin.x, y: p.y + origin.y) }

    override func mouseMoved(with event: NSEvent) { cursor = local(event); needsDisplay = true }
    override func mouseExited(with event: NSEvent) { cursor = nil; needsDisplay = true }
    override func mouseDown(with event: NSEvent) {
        closed = false
        cursor = local(event)
        rec.begin(global(cursor!)); needsDisplay = true
    }
    override func mouseDragged(with event: NSEvent) { cursor = local(event); rec.add(global(cursor!)); needsDisplay = true }
    override func mouseUp(with event: NSEvent) {
        rec.add(global(local(event)))
        let outcome = rec.end()
        if case .closed = outcome { closed = true } else { rec.cancel() }
        needsDisplay = true
        onFinish?(outcome)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        // vignette: clear in the middle, gently darker at the edges
        let c = CGPoint(x: bounds.midX, y: bounds.midY)
        let r = hypot(bounds.width, bounds.height) / 2
        let colors = [NSColor.black.withAlphaComponent(0.0).cgColor, NSColor.black.withAlphaComponent(0.10).cgColor,
                      NSColor.black.withAlphaComponent(0.32).cgColor] as CFArray
        if let g = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 0.55, 1]) {
            ctx.drawRadialGradient(g, startCenter: c, startRadius: 0, endCenter: c, endRadius: r, options: [.drawsAfterEndLocation])
        }
        // cursor halo: a soft ring that says "you can circle now"
        if let p = cursor, rec.state != .drawing {
            let ring = NSBezierPath(ovalIn: NSRect(x: p.x - 18, y: p.y - 18, width: 36, height: 36))
            Palette.accentNS.withAlphaComponent(0.10).setFill(); ring.fill()
            Palette.accentNS.withAlphaComponent(0.75).setStroke(); ring.lineWidth = 1.5; ring.stroke()
        }
        // the loop
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
        Palette.accentNS.withAlphaComponent(0.92).setStroke()
        path.lineWidth = 3
        path.stroke()
        NSGraphicsContext.restoreGraphicsState()
        if closed { Palette.accentNS.withAlphaComponent(0.08).setFill(); path.fill() }
    }
}

// MARK: - spoken replies (on-device AVSpeechSynthesizer)

@MainActor
final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
    private let synth = AVSpeechSynthesizer()
    var onSpeaking: ((Bool) -> Void)?

    override init() {
        super.init()
        synth.delegate = self
    }

    func speak(_ answer: String) {
        stop()
        let text = SpokenText.clean(answer)
        guard !text.isEmpty else { return }
        let u = AVSpeechUtterance(string: text)
        u.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        synth.speak(u)
        onSpeaking?(true)
        log("speaking \(text.count) chars")
    }

    func stop() {
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        onSpeaking?(false)
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        DispatchQueue.main.async { MainActor.assumeIsolated { self.onSpeaking?(false) } }
    }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) {
        DispatchQueue.main.async { MainActor.assumeIsolated { self.onSpeaking?(false) } }
    }
}

// MARK: - zoom lens (local only)

final class LensScale: ObservableObject {
    @Published var scale: Double = 2.5
}

struct LensView: View {
    let image: NSImage
    @ObservedObject var zoom: LensScale
    var onClose: () -> Void

    var body: some View {
        let size = ZoomLens.lensSize(image: image.size, scale: zoom.scale)
        VStack(spacing: 8) {
            ScrollView([.horizontal, .vertical]) {
                Image(nsImage: image).resizable().interpolation(.high)
                    .frame(width: image.size.width * zoom.scale, height: image.size.height * zoom.scale)
            }
            .frame(width: size.width, height: size.height)
            .background(Color.black.opacity(0.5))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            HStack(spacing: 8) {
                Image(systemName: "plus.magnifyingglass").foregroundStyle(Palette.accent)
                Slider(value: $zoom.scale, in: ZoomLens.minScale...ZoomLens.maxScale).controlSize(.small)
                Text(String(format: "%.1f×", zoom.scale)).font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.7)).frame(width: 36)
                Button(action: onClose) { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.white.opacity(0.6)).help("Close (Esc)")
            }
        }
        .padding(8)
        .background(ZStack { VisualEffect(); Color.black.opacity(0.35) })
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(.white.opacity(0.1)))
    }
}

/// Scroll and pinch change the zoom; everything else goes to SwiftUI.
final class LensHost: NSHostingView<LensView> {
    var zoom: LensScale?
    override func scrollWheel(with e: NSEvent) {
        guard let zoom, e.modifierFlags.contains(.command) || !e.hasPreciseScrollingDeltas else { return super.scrollWheel(with: e) }
        zoom.scale = ZoomLens.scrolled(zoom.scale, deltaY: Double(e.scrollingDeltaY))
    }
    override func magnify(with e: NSEvent) {
        guard let zoom else { return }
        zoom.scale = ZoomLens.pinched(zoom.scale, magnification: Double(e.magnification))
    }
}

@MainActor
final class ZoomLensWindow {
    private var panel: NSPanel?
    private var outside: Any?
    private var esc: UInt32?
    let zoom = LensScale()

    var isOpen: Bool { panel != nil }

    func open(_ sel: Selection, near anchor: CGPoint) {
        close()
        guard let path = sel.image_path, let raw = NSImage(contentsOfFile: path) else { log("zoom: no captured image"); return }
        // show at point size (the capture is at the display's pixel scale)
        if let r = sel.region, r.count == 4, r[2] > 0, r[3] > 0 { raw.size = NSSize(width: r[2], height: r[3]) }
        zoom.scale = 2.5
        let host = LensHost(rootView: LensView(image: raw, zoom: zoom, onClose: { [weak self] in self?.close() }))
        host.zoom = zoom
        host.sizingOptions = [.intrinsicContentSize]
        let p = NSPanel(contentRect: NSRect(origin: .zero, size: host.fittingSize),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.level = .statusBar
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = true
        p.isMovableByWindowBackground = true
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        p.contentView = host
        let vis = NSScreen.main?.visibleFrame ?? .zero
        let s = host.fittingSize
        p.setFrameOrigin(Placement.clamp(CGPoint(x: anchor.x - s.width / 2, y: anchor.y - s.height - 40), size: s, in: vis))
        p.orderFrontRegardless()
        panel = p
        outside = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.close() } }
        }
        esc = Hotkeys.shared.register(keyCode: Keys.escape, modifiers: 0, name: "Esc (close lens)") { [weak self] in self?.close() }
        log("zoom: lens open at \(zoom.scale)x (local, no network)")
    }

    func close() {
        guard let p = panel else { return }
        p.orderOut(nil)
        panel = nil
        if let o = outside { NSEvent.removeMonitor(o) }
        outside = nil
        if let e = esc { Hotkeys.shared.unregister(e) }
        esc = nil
        log("zoom: lens closed")
    }
}
