import AppKit
import ReachCore
import ScreenCaptureKit
import Vision

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
        w.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)   // above the ready layer and the panel (click-through)
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
        // Robust check: preflight can say no while capture works (and the reverse right after a grant), so a false
        // preflight asks macOS once per run and then lets the real capture below decide; only a throw means missing.
        if !CGPreflightScreenCaptureAccess(), !askedForCapture {
            askedForCapture = true
            _ = CGRequestScreenCaptureAccess()
            await MainActor.run { PermissionProbe.askedScreenThisRun = true }
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
        // Leave out every Reach window (panel, ready layer, halo, lens), whatever is on screen at the time.
        let mine = content.applications.filter { $0.processID == getpid() }
        let filter = SCContentFilter(display: display, excludingApplications: mine, exceptingWindows: [])
        let image: CGImage
        do { image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config) }
        catch { throw CGPreflightScreenCaptureAccess() ? CaptureError.failed(error.localizedDescription) : CaptureError.permission }

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
