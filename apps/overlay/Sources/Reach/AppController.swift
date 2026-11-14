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
    private let speech = SpeechInput()
    private var ptt = PushToTalk()
    private var voiceEsc: [UInt32] = []
    private var lastHeard: Heard?

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
        log("panel ready (Option-Space toggles, Option-Shift-Space selects a region, hold Control-Option-Space to talk, Esc aborts)")

        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option, name: "Option-Space") { [weak self] in
            self?.toggle()
        }
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option | Keys.shift, name: "Option-Shift-Space") { [weak self] in
            self?.selectRegion()
        }
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.control | Keys.option, name: "Control-Option-Space (hold to talk)",
                                onRelease: { [weak self] in self?.voiceRelease() }) { [weak self] in
            self?.voicePress()
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
        if ptt.phase != .idle { voiceCancel(); return }
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

    // MARK: hold to talk

    /// Key down: ask for Microphone and Speech Recognition the first time, then listen while the key is held.
    func voicePress() {
        guard case .start(let id) = ptt.press() else { return }
        show()
        lastHeard = nil
        model.voiceLine = "starting microphone…"
        MicAuth.request { [weak self] ok in
            guard let self else { return }
            guard ok else { self.voiceBlocked(id, "allow Microphone for Reach"); return }
            SpeechAuth.request { [weak self] status in
                guard let self else { return }
                guard status == .authorized else { self.voiceBlocked(id, "allow Speech Recognition for Reach"); return }
                self.startListening(id)
            }
        }
    }

    private func startListening(_ id: Int) {
        // The key may have come up while macOS was asking for permission; then there is nothing to hear.
        guard ptt.accepts(partialFrom: id) else {
            _ = ptt.cancel()
            model.voiceLine = "hold Control-Option-Space while you speak"
            return
        }
        model.listening = true
        model.draft = ""
        model.voiceLine = speech.onDevice ? "listening… (on this Mac)"
                                          : "listening… (on-device speech unavailable: using Apple's server)"
        log("voice listening (session \(id), on-device \(speech.onDevice))")
        for mods in [0, Keys.control | Keys.option] {
            if let h = Hotkeys.shared.register(keyCode: Keys.escape, modifiers: mods, name: "Esc (cancel voice)",
                                               { [weak self] in self?.voiceCancel() }) { voiceEsc.append(h) }
        }
        speech.start(onResult: { [weak self] heard in
            guard let self, self.ptt.session == id else { return }
            self.lastHeard = heard
            if self.ptt.accepts(partialFrom: id) { self.model.draft = heard.text }
            if heard.final, self.ptt.phase == .finishing { self.complete(id) }
        }, onError: { [weak self] why in
            guard let self, self.ptt.session == id else { return }
            if self.ptt.phase == .finishing { self.complete(id); return }
            guard self.ptt.phase == .listening else { return }
            log("voice error: \(why)")
            _ = self.ptt.cancel()
            self.speech.cancel()
            self.endListening()
            self.model.voiceLine = "voice: \(why)"
        })
    }

    /// Key up: stop the mic, wait briefly for the final transcript.
    func voiceRelease() {
        guard case .finish(let id) = ptt.release() else { return }
        if !model.listening { _ = ptt.cancel(); return }   // still waiting on a permission prompt
        model.voiceLine = "transcribing…"
        speech.finish()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.complete(id) }
    }

    private func complete(_ id: Int) {
        guard ptt.finished(session: id) else { return }
        endListening()
        let heard = lastHeard ?? Heard(text: "", confidences: [], final: false)
        let avg = VoiceGate.average(heard.confidences).map { String(format: "%.2f", $0) } ?? "unreported"
        log("voice heard \"\(heard.text)\" (final \(heard.final), confidence \(avg))")
        switch VoiceGate.judge(text: heard.text, confidences: heard.confidences) {
        case .send(let text):
            model.voiceLine = "heard: \(text)"
            model.submit(text)
        case .reject(let why):
            model.draft = ""
            model.voiceLine = why
        }
    }

    func voiceCancel() {
        guard case .cancel = ptt.cancel() else { return }
        speech.cancel()
        endListening()
        model.draft = ""
        model.voiceLine = "voice cancelled"
        log("voice cancelled")
    }

    private func voiceBlocked(_ id: Int, _ why: String) {
        log("voice blocked: \(why)")
        if ptt.session == id { _ = ptt.cancel() }
        model.voiceLine = why
        model.needsUser(why)
    }

    private func endListening() {
        model.listening = false
        voiceEsc.forEach { Hotkeys.shared.unregister($0) }
        voiceEsc = []
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
