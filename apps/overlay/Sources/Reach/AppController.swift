import AppKit
import Combine
import ReachCore
import SwiftUI

@MainActor
final class AppController: NSObject, NSApplicationDelegate {
    let model: OverlayModel
    private var panel: ReachPanel!
    private let lasso = LassoPicker()
    private let halo = RegionHalo()
    private var ghost: NSWindow?
    private var ghostLabel: String??
    private var ghostFade: DispatchWorkItem?
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
        let host = NSHostingView(rootView: PanelView(model: model, onSnap: { [weak self] in self?.snapToTop() }))
        host.sizingOptions = [.intrinsicContentSize]  // fit() sizes the window, keeping its top edge
        self.host = host
        panel = ReachPanel(content: host)
        sizeWatch = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.fit(animated: false) } }
        }
        panel.onEscape = { [weak self] in self?.escape() }
        model.expandRequested = { [weak self] in self?.expand(focus: true, why: "click") }
        model.expanded = false
        fit(animated: false)
        place()
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: panel, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.savePosition() }
        }
        log("panel ready (tap Option-Space to type, hold it to talk, or circle something; Esc cancels)")
        model.circleRequested = { [weak self] in self?.selectRegion() }

        // One key: tap opens/closes, press-and-hold (> 0.25 s) talks.
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option, name: "Option-Space (tap / hold)",
                                onRelease: { [weak self] in self?.optionSpaceUp() }) { [weak self] in
            self?.optionSpaceDown()
        }
        // The older combos still work, silently (not in the UI or README).
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option | Keys.shift, name: "legacy circle combo") { [weak self] in
            self?.selectRegion()
        }
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.control | Keys.option, name: "legacy talk combo",
                                onRelease: { [weak self] in self?.voiceRelease() }) { [weak self] in
            self?.voicePress()
        }
        model.onPhase = { [weak self] phase in self?.phaseChanged(phase) }
        model.onPointer = { [weak self] x, y, label, byHand in self?.showPointer(x: x, y: y, label: label, byHand: byHand) }
        model.onHandSelection = { [weak self] _ in
            // open without taking focus: the user's hand is on whatever they were reading
            self?.expand(focus: false, why: "hand selection")
        }
        model.connect()
        panel.orderFrontRegardless()  // the idle pill; never takes focus on launch
        log("panel: pill at \(frameLine())")
        idleTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.collapseIfIdle() }
        }
    }

    // MARK: island placement, pill <-> panel

    private var host: NSView?
    private var sizeWatch: AnyCancellable?

    /// Sizes the window to its SwiftUI content, keeping the top edge and centre (pill grows downward).
    private func fit(animated: Bool) {
        guard let host, let panel else { return }
        host.layoutSubtreeIfNeeded()
        let s = host.intrinsicContentSize
        if ProcessInfo.processInfo.environment["REACH_DEBUG_FIT"] != nil { log("fit: content \(s) frame \(panel.frame.size)") }
        guard s.width > 0, s.height > 0, s != panel.frame.size else { return }
        let r = NSRect(origin: Placement.keepTop(old: panel.frame, newSize: s, visible: panel.screen?.visibleFrame), size: s)
        placing = true
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.28
                ctx.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrame(r, display: true)
            }
        } else {
            panel.setFrame(r, display: true)
        }
        placing = false
    }

    private var idleTimer: Timer?
    private var lastActivity = Date()
    private var placing = false
    static let collapseAfter: TimeInterval = 8

    private func frameLine() -> String {
        let f = panel.frame
        return "x \(Int(f.minX)) y \(Int(f.minY)) w \(Int(f.width)) h \(Int(f.height))"
    }

    private static func displayID(_ s: NSScreen) -> String {
        (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber).map { "\($0)" } ?? "main"
    }

    private func notchOrigin(_ s: NSScreen) -> NSPoint {
        Placement.notchOrigin(screen: s.frame, visible: s.visibleFrame, safeTop: s.safeAreaInsets.top,
                              auxLeft: s.auxiliaryTopLeftArea, auxRight: s.auxiliaryTopRightArea, size: panel.frame.size)
    }

    /// Saved spot on the current display if there is one, else hugging the notch at the top of the main screen.
    private func place() {
        guard let screen = NSScreen.main else { return }
        let saved = (UserDefaults.standard.dictionary(forKey: "panelTopLeft") as? [String: [Double]] ?? [:])
            .compactMapValues { $0.count == 2 ? CGPoint(x: $0[0], y: $0[1]) : nil }
        let displays = Dictionary(NSScreen.screens.map { (Self.displayID($0), $0.visibleFrame) }, uniquingKeysWith: { a, _ in a })
        placing = true
        defer { placing = false }
        if let o = Placement.restore(saved: saved, display: Self.displayID(screen), displays: displays, size: panel.frame.size) {
            panel.setFrameOrigin(o)
        } else {
            panel.setFrameOrigin(notchOrigin(screen))
        }
    }

    private func savePosition() {
        guard !placing, let screen = panel.screen else { return }
        var saved = UserDefaults.standard.dictionary(forKey: "panelTopLeft") as? [String: [Double]] ?? [:]
        saved[Self.displayID(screen)] = [panel.frame.minX, panel.frame.maxY]
        UserDefaults.standard.set(saved, forKey: "panelTopLeft")
    }

    /// Double-click on the header or pill: back to the notch, and forget the saved spot on this display.
    func snapToTop() {
        guard let screen = panel.screen ?? NSScreen.main else { return }
        var saved = UserDefaults.standard.dictionary(forKey: "panelTopLeft") as? [String: [Double]] ?? [:]
        saved[Self.displayID(screen)] = nil
        UserDefaults.standard.set(saved, forKey: "panelTopLeft")
        placing = true
        let o = notchOrigin(screen)
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.25
            panel.animator().setFrameOrigin(o)
        }
        placing = false
        log("panel: snapped to top")
    }

    func expand(focus: Bool, why: String) {
        lastActivity = Date()
        if !panel.isVisible { panel.orderFrontRegardless() }
        if !model.expanded {
            model.expanded = true
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.fit(animated: true)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { log("panel: expanded (\(why)) at \(self.frameLine())") }
            }
        }
        if focus { panel.makeKey() }
    }

    func collapse(why: String) {
        guard model.expanded else { return }
        model.expanded = false
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.fit(animated: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { log("panel: pill (\(why)) at \(self.frameLine())") }
        }
    }

    /// Collapses after ~8 s idle; never while an ask runs, while listening, or with text in the field.
    private func collapseIfIdle() {
        let busy = model.state.phase == .working || model.listening || !model.draft.isEmpty
        if busy { lastActivity = Date(); return }
        if model.expanded, Date().timeIntervalSince(lastActivity) > Self.collapseAfter { collapse(why: "idle") }
    }

    func toggle() {
        if !panel.isVisible || !model.expanded { expand(focus: true, why: "hotkey") } else { collapse(why: "hotkey") }
    }

    private func show() {
        expand(focus: true, why: "summon")
    }

    private func escape() {
        if ptt.phase != .idle { voiceCancel(); return }
        if model.state.phase == .working { model.abort() } else { collapse(why: "esc"); halo.hide() }
    }

    /// While a task runs, Esc aborts from anywhere; the hotkey is released as soon as it ends.
    private var key = TapOrHold()

    private func optionSpaceDown() {
        let token = key.down(at: ProcessInfo.processInfo.systemUptime)
        DispatchQueue.main.asyncAfter(deadline: .now() + TapOrHold.threshold) { [weak self] in
            guard let self else { return }
            if self.key.holdCheck(token: token, at: ProcessInfo.processInfo.systemUptime) == .startHold {
                log("Option-Space held: listening")
                self.voicePress()
            }
        }
    }

    private func optionSpaceUp() {
        switch key.up(at: ProcessInfo.processInfo.systemUptime) {
        case .tap: toggle()
        case .endHold: voiceRelease()
        default: break
        }
    }

    private func phaseChanged(_ phase: Phase) {
        if phase != .idle { expand(focus: false, why: "answer \(phase.rawValue)") }
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
            if ptt.session == id { _ = ptt.cancel() }
            model.voiceLine = "hold Option-Space while you speak"
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
        guard heard.final else {
            // Only a final transcript carries confidences; never send a partial.
            speech.cancel()
            log("voice: no final transcript within 2 s (last partial \"\(heard.text)\"); not sent")
            model.draft = ""
            model.voiceLine = "didn't catch that"
            return
        }
        let avg = VoiceGate.average(heard.confidences).map { String(format: "%.2f", $0) } ?? "none"
        log("voice heard \"\(heard.text)\" (confidence \(avg))")
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

    /// Circle selection: a freehand loop with the user's own mouse; only the inside of the loop is kept.
    func selectRegion() {
        panel.orderOut(nil)
        lasso.log = { log($0) }
        lasso.begin { [weak self] outcome in
            guard let self else { return }
            switch outcome {
            case .cancelled:
                self.panel.orderFrontRegardless()
            case .tooSmall:
                self.model.selectionNote = "draw a bigger circle"
                self.expand(focus: false, why: "circle too small")
            case .closed(let loop, let box):
                self.halo.show(loop: loop)
                self.expand(focus: true, why: "circle")
                self.captureLoop(loop, box: box)
            }
        }
    }

    private func captureLoop(_ loop: [CGPoint], box: NSRect) {
        let top = NSScreen.screens.first?.frame.maxY ?? box.maxY
        let region = Lasso.region(box, primaryTop: top)
        let path = Lasso.contractPath(loop, primaryTop: top)
        log("circle: \(loop.count) points, box \(region.map { Int($0) })")
        model.setSelection(Selection(region: region, path: path), note: "circled · reading…")
        let dir = model.files.capturesDir
        Task { @MainActor in
            do {
                let (image, url) = try await RegionCapture.captureLoop(loop, box: box, into: dir)
                let text = await RegionCapture.recognizeText(image)
                let words = text.split { $0 == " " || $0 == "\n" }.count
                log("circle captured to \(url.path) (outside the loop masked), \(words) words read")
                self.model.setSelection(Selection(text: text.isEmpty ? nil : text, region: region, image_path: url.path,
                                                  path: path), note: "circled · \(words) words read")
            } catch let e as CaptureError {
                log("capture: \(e)")
                self.model.setSelection(Selection(region: region, path: path), note: e.description)
                if case .permission = e { self.model.needsUser("allow Screen Recording for Reach") }
            } catch {
                log("capture error: \(error)")
                self.model.setSelection(Selection(region: region, path: path), note: "capture failed")
            }
        }
    }

    /// One reusable ghost pointer. Task pointers rest 4 s; hand pointers (~15 Hz) fade calmly 1 s after the last one.
    private func showPointer(x: Double, y: Double, label: String?, byHand: Bool) {
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        let size = NSSize(width: 160, height: 34)
        let origin = NSPoint(x: x - 9, y: top - y - 9)
        if ghost == nil || ghostLabel != .some(label) {
            ghost?.orderOut(nil)
            let w = NSWindow(contentRect: NSRect(origin: origin, size: size), styleMask: .borderless, backing: .buffered,
                             defer: false)
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
            ghost = w
            ghostLabel = .some(label)
        }
        guard let w = ghost else { return }
        w.setFrameOrigin(origin)
        NSAnimationContext.runAnimationGroup { ctx in ctx.duration = 0; w.animator().alphaValue = 1 }  // stops a fade
        w.orderFrontRegardless()
        ghostFade?.cancel()
        let fade = DispatchWorkItem { [weak w] in
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = 0.45
                w?.animator().alphaValue = 0
            }, completionHandler: { [weak w] in
                MainActor.assumeIsolated { if w?.alphaValue == 0 { w?.orderOut(nil) } }
            })
        }
        ghostFade = fade
        DispatchQueue.main.asyncAfter(deadline: .now() + (byHand ? 1 : 4), execute: fade)
    }
}
