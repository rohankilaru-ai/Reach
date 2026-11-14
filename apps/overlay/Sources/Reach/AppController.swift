import AppKit
import ReachCore
import SwiftUI

@MainActor
final class AppController: NSObject, NSApplicationDelegate {
    let model: OverlayModel
    private var panel: ReachPanel!
    private let picker = RegionPicker()
    private let halo = RegionHalo()
    private var ghost: NSWindow?
    private var escHotkey: UInt32?

    init(files: RunFiles) {
        model = OverlayModel(files: files)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let host = NSHostingView(rootView: PanelView(model: model))
        host.sizingOptions = [.preferredContentSize]
        panel = ReachPanel(content: host)
        panel.onEscape = { [weak self] in self?.escape() }
        place()
        log("panel ready (Option-Space toggles, Option-Shift-Space selects a region, Esc aborts)")

        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option, name: "Option-Space") { [weak self] in
            self?.toggle()
        }
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option | Keys.shift, name: "Option-Shift-Space") { [weak self] in
            self?.selectRegion()
        }
        model.onPhase = { [weak self] phase in self?.phaseChanged(phase) }
        model.onPointer = { [weak self] x, y, label in self?.showPointer(x: x, y: y, label: label) }
        model.connect()
        show()
    }

    private func place() {
        guard let screen = NSScreen.main else { return }
        let f = screen.visibleFrame
        panel.setFrameTopLeftPoint(NSPoint(x: f.midX - 210, y: f.maxY - 80))
    }

    func toggle() {
        if panel.isVisible { panel.orderOut(nil); halo.hide() } else { show() }
    }

    private func show() {
        panel.orderFrontRegardless()
        panel.makeKey()
    }

    private func escape() {
        if model.state.phase == .working { model.abort() } else { panel.orderOut(nil); halo.hide() }
    }

    /// While a task runs, Esc aborts from anywhere; the hotkey is released as soon as it ends.
    private func phaseChanged(_ phase: Phase) {
        if phase == .working, escHotkey == nil {
            escHotkey = Hotkeys.shared.register(keyCode: Keys.escape, modifiers: 0, name: "Esc (abort)") { [weak self] in
                self?.model.abort()
            }
        } else if phase != .working, let id = escHotkey {
            Hotkeys.shared.unregister(id)
            escHotkey = nil
        }
    }

    func selectRegion() {
        panel.orderOut(nil)
        picker.begin { [weak self] rect in
            guard let self else { return }
            guard let rect else { self.show(); return }
            self.halo.show(around: rect)
            self.show()
            self.captureRegion(rect)
        }
    }

    private func captureRegion(_ rect: NSRect) {
        // Contract region is [x, y, w, h] in points with a top-left origin.
        let top = NSScreen.screens.first?.frame.maxY ?? rect.maxY
        let region = [rect.minX, top - rect.maxY, rect.width, rect.height].map { Double($0) }
        model.setSelection(Selection(region: region), note: "region selected · reading…")
        let dir = model.files.capturesDir
        Task { @MainActor in
            do {
                let (image, url) = try await RegionCapture.capture(rect, into: dir)
                let text = await RegionCapture.recognizeText(image)
                let words = text.split { $0 == " " || $0 == "\n" }.count
                log("region captured to \(url.path), \(words) words read")
                self.model.setSelection(Selection(text: text.isEmpty ? nil : text, region: region, image_path: url.path),
                                        note: "region · \(words) words read")
            } catch let e as CaptureError {
                log("capture: \(e)")
                self.model.setSelection(Selection(region: region), note: e.description)
                if case .permission = e { self.model.needsUser("allow Screen Recording for Reach") }
            } catch {
                log("capture error: \(error)")
                self.model.setSelection(Selection(region: region), note: "capture failed")
            }
        }
    }

    /// A ghost pointer that rests on a spot for a few seconds (the backend fills these from S4/S6).
    private func showPointer(x: Double, y: Double, label: String?) {
        ghost?.orderOut(nil)
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        let size = NSSize(width: 160, height: 34)
        let w = NSWindow(contentRect: NSRect(x: x - 9, y: top - y - 9, width: size.width, height: size.height),
                         styleMask: .borderless, backing: .buffered, defer: false)
        w.level = .floating
        w.isOpaque = false
        w.backgroundColor = .clear
        w.ignoresMouseEvents = true
        w.contentView = NSHostingView(rootView: HStack(spacing: 6) {
            Circle().fill(Palette.accent.opacity(0.85)).frame(width: 18, height: 18)
                .overlay(Circle().stroke(.white.opacity(0.8), lineWidth: 1.5))
            if let label { Text(label).font(.system(size: 11, weight: .medium)).foregroundStyle(.white)
                .padding(.horizontal, 6).padding(.vertical, 2).background(Capsule().fill(.black.opacity(0.6))) }
            Spacer(minLength: 0)
        })
        w.orderFrontRegardless()
        ghost = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak w] in w?.orderOut(nil) }
    }
}
