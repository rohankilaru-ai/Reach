import AppKit
import Combine
import ReachCore
import SwiftUI

@MainActor
final class AppController: NSObject, NSApplicationDelegate {
    let model: OverlayModel
    private var panel: ReachPanel!
    private let ready = ReadyLayer()
    private let halo = RegionHalo()
    private let lens = ZoomLensWindow()
    private let speaker = Speaker()
    private var gesture = HubGesture()
    private var hover = Hover.Tracker()
    private var hoverTimer: Timer?
    private var ghost: NSWindow?
    private var ghostLabel: String??
    private var ghostFade: DispatchWorkItem?
    private var escHotkey: UInt32?
    private let speech = SpeechInput()
    private var ptt = PushToTalk()
    private var voiceEsc: [UInt32] = []
    private var lastHeard: Heard?
    /// Cloud STT for the current hold: request id, recording, start, and the result once it arrives.
    private var stt: (id: String, file: URL, t0: Date, text: String?, err: String?, done: Bool)?
    /// Cloud TTS request whose audio may still play (nil after Esc, a new hold, or the on-device fallback).
    private var ttsID: String?
    private var ttsSeq = 0
    private let setup = SetupWindow()
    private var menuBar: MenuBarItem?
    private var pillMode = PillMode.load()

    init(files: RunFiles) {
        model = OverlayModel(files: files)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let host = NSHostingView(rootView: PanelView(model: model, onSnap: { [weak self] in self?.snapToTop() },
                                                     onDragEnd: { [weak self] in self?.dragEnded() },
                                                     onSpeak: { [weak self] in
                                                         guard let self else { return }
                                                         self.say(self.model.state.answer)
                                                     },
                                                     onZoom: { [weak self] in
                                                         if let z = self?.model.zoomTarget { self?.openLens(z) }
                                                     },
                                                     onAction: { [weak self] a in self?.act(a) }))
        host.sizingOptions = [.intrinsicContentSize]  // fit() sizes the window, keeping its top edge
        self.host = host
        panel = ReachPanel(content: host)
        sizeWatch = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.fit(animated: false) } }
        }
        panel.onEscape = { [weak self] in self?.escape() }
        model.expandRequested = { [weak self] in self?.expand(focus: true, why: "click") }
        model.expanded = false
        place()
        log("panel ready (hold Option-Space, circle what you mean, say what to do; tap to type; Esc cancels)")
        model.onZoom = { [weak self] sel in self?.openLens(sel) }
        model.onSpeak = { [weak self] text in self?.say(text) }
        model.onStopSpeaking = { [weak self] in self?.ttsID = nil; self?.speaker.stop() }
        model.onSttResult = { [weak self] text, err, id in self?.sttArrived(text, err, id) }
        model.onTtsAudio = { [weak self] path, err, id in self?.ttsArrived(path, err, id) }
        speaker.onSpeaking = { [weak self] on in
            self?.model.speaking = on
            if !on { self?.lastActivity = Date() }   // the 8 s fold starts after the audio ends
        }
        ready.onCircle = { [weak self] loop, box in self?.circleDrawn(loop, box: box) }
        ready.onTooSmall = { [weak self] in self?.model.selectionNote = "draw a bigger circle" }

        // One key: tap opens/closes the typing panel; hold talks and lets you circle at the same time.
        Hotkeys.shared.register(keyCode: Keys.space, modifiers: Keys.option, name: "Option-Space (tap / hold)",
                                onRelease: { [weak self] in self?.optionSpaceUp() }) { [weak self] in
            self?.optionSpaceDown()
        }
        model.onPhase = { [weak self] phase in self?.phaseChanged(phase) }
        model.onPointer = { [weak self] x, y, label, byHand in self?.showPointer(x: x, y: y, label: label, byHand: byHand) }
        model.onHandSelection = { [weak self] _ in
            // open without taking focus: the user's hand is on whatever they were reading
            self?.expand(focus: false, why: "hand selection")
        }
        model.connect()
        if pillMode == .always { panel.orderFrontRegardless() }  // the idle pill; never takes focus on launch
        log("panel: pill at \(frameLine()) (pill mode \(pillMode.rawValue))")
        let bar = MenuBarItem(model: model)
        bar.pillShown = { [weak self] in self?.panel.isVisible == true }
        bar.onTogglePill = { [weak self] in self?.togglePillFromMenu() }
        bar.onPillMode = { [weak self] m in self?.setPillMode(m) }
        bar.onSetup = { [weak self] in self?.setup.open() }
        menuBar = bar
        // Open the checklist once per launch when a required permission is missing.
        Task { @MainActor in
            let s = await PermissionProbe.read(probeCapture: true)
            let missing = PermissionRules.missingRequired(s)
            log("setup: required missing: \(missing.isEmpty ? "none" : missing.map(\.rawValue).joined(separator: ", "))")
            if !missing.isEmpty { self.setup.open() }
        }
        idleTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.collapseIfIdle() }
        }
        // Hover: move the mouse up to the top-centre to open; polled (no input monitoring permission needed).
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.hoverTick() }
        }
    }

    private var pillSize = CGSize(width: 120, height: 32)

    private func hoverTick() {
        guard let panel, let screen = panel.screen ?? NSScreen.main else { return }
        if !model.expanded, panel.frame.height < 64 { pillSize = panel.frame.size }   // not mid-animation
        let mouse = NSEvent.mouseLocation
        if !pillMode.hoverSummons, !model.expanded { return }
        let hot = Hover.hotZone(anchor: anchor, pill: pillSize, screenTop: screen.frame.maxY)
        if Hover.keepZone(panel: panel.frame).contains(mouse), model.expanded { lastActivity = Date() }
        let busy = model.state.phase == .working || model.listening || !model.draft.isEmpty || gesture.phase != .idle
            || model.capturing || model.speaking || lens.isOpen || model.capturing
            || gesture.phase != .idle || NSEvent.pressedMouseButtons != 0 || panel.isKeyWindow
        switch hover.update(mouse: mouse, now: ProcessInfo.processInfo.systemUptime, expanded: model.expanded, hot: hot,
                            panel: panel.frame, busy: busy) {
        case .expand: expand(focus: false, why: "hover", byHover: true)
        case .collapse: collapse(why: "hover away")
        case .none: break
        }
    }

    // MARK: island placement, pill <-> panel

    private var host: NSView?
    private var sizeWatch: AnyCancellable?
    /// Top-centre of the panel; every frame is computed from it, so resizes never drift. Saved only after a user drag.
    private var anchor: CGPoint = .zero
    private static let anchorsKey = "panelAnchorTopCenter"

    /// Sizes the window to its SwiftUI content around the anchor (the pill grows downward from it).
    private func fit(animated: Bool) {
        guard let host, let panel else { return }
        host.layoutSubtreeIfNeeded()
        let s = host.intrinsicContentSize
        guard s.width > 0, s.height > 0 else { return }
        if let vis = panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame {
            let m = PanelBudget.answerMax(visibleHeight: vis.height, spaceBelowAnchor: anchor.y - vis.minY, chrome: 190)
            if abs(model.answerMax - m) > 1 { model.answerMax = m }
        }
        let r = NSRect(origin: Placement.origin(anchor: anchor, size: s, visible: panel.screen?.visibleFrame ?? NSScreen.main?.visibleFrame),
                       size: s)
        guard r != panel.frame else { return }
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.34
                ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.3, 1.25, 0.5, 1)   // a small spring overshoot
                panel.animator().setFrame(r, display: true)
            }
        } else {
            panel.setFrame(r, display: true)
        }
    }

    private var idleTimer: Timer?
    private var lastActivity = Date()
    static let collapseAfter: TimeInterval = 8

    private func frameLine() -> String {
        let f = panel.frame
        return "x \(Int(f.minX)) y \(Int(f.minY)) w \(Int(f.width)) h \(Int(f.height))"
    }

    private static func displayID(_ s: NSScreen) -> String {
        (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber).map { "\($0)" } ?? "main"
    }

    private func notchAnchor(_ s: NSScreen) -> CGPoint {
        Placement.notchAnchor(screen: s.frame, visible: s.visibleFrame, safeTop: s.safeAreaInsets.top,
                              auxLeft: s.auxiliaryTopLeftArea, auxRight: s.auxiliaryTopRightArea)
    }

    private static func savedAnchors() -> [String: CGPoint] {
        (UserDefaults.standard.dictionary(forKey: anchorsKey) as? [String: [Double]] ?? [:])
            .compactMapValues { $0.count == 2 ? CGPoint(x: $0[0], y: $0[1]) : nil }
    }

    private static func storeAnchor(_ a: CGPoint?, display: String) {
        var d = UserDefaults.standard.dictionary(forKey: anchorsKey) as? [String: [Double]] ?? [:]
        d[display] = a.map { [Double($0.x), Double($0.y)] }
        UserDefaults.standard.set(d, forKey: anchorsKey)
    }

    /// Saved anchor on the main display if there is one, else hugging the notch at the top of the main screen.
    private func place() {
        guard let screen = NSScreen.main else { return }
        let displays = Dictionary(NSScreen.screens.map { (Self.displayID($0), $0.visibleFrame) }, uniquingKeysWith: { a, _ in a })
        anchor = Placement.restoreAnchor(saved: Self.savedAnchors(), display: Self.displayID(screen), displays: displays)
            ?? notchAnchor(screen)
        fit(animated: false)
    }

    /// End of a user drag on the pill or header: the new top-centre becomes the anchor for that display.
    func dragEnded() {
        anchor = CGPoint(x: panel.frame.midX, y: panel.frame.maxY)
        if let screen = panel.screen { Self.storeAnchor(anchor, display: Self.displayID(screen)) }
        log("panel: moved, anchor \(Int(anchor.x)),\(Int(anchor.y)) saved")
    }

    /// Double-click on the header or pill: back to the notch, and forget the saved spot on this display.
    func snapToTop() {
        guard let screen = panel.screen ?? NSScreen.main else { return }
        Self.storeAnchor(nil, display: Self.displayID(screen))
        anchor = notchAnchor(screen)
        fit(animated: true)
        log("panel: snapped to top")
    }

    func expand(focus: Bool, why: String, byHover: Bool = false) {
        lastActivity = Date()
        if !byHover { hover.openedElsewhere() }
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
        halo.hide()
        hover.collapsed()
        model.typing = false
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
        hidePillIfIdle()
    }

    /// Auto / Hidden: the collapsed pill leaves the screen once nothing is going on.
    private func hidePillIfIdle() {
        guard let panel, panel.isVisible else { return }
        let busy = model.state.phase == .working || model.listening || !model.draft.isEmpty || gesture.phase != .idle
            || model.capturing || model.speaking || model.typing || panel.isKeyWindow || ptt.phase != .idle
        guard !PillVisibility.shouldShow(mode: pillMode, expanded: model.expanded, busy: busy) else { return }
        guard panel.frame.height < 64 else { return }   // wait for the fold animation to finish
        panel.orderOut(nil)
        log("panel: pill hidden (\(pillMode.rawValue))")
    }

    private func setPillMode(_ m: PillMode) {
        pillMode = m
        UserDefaults.standard.set(m.rawValue, forKey: PillMode.key)
        log("panel: pill mode \(m.rawValue)")
        if m == .always { panel.orderFrontRegardless() } else { hidePillIfIdle() }
    }

    private func togglePillFromMenu() {
        if panel.isVisible {
            collapse(why: "menu")
            panel.orderOut(nil)
        } else {
            lastActivity = Date()
            panel.orderFrontRegardless()
            if pillMode != .always { expand(focus: false, why: "menu") }   // folds away again after ~8 s idle
        }
    }

    func toggle() {
        // a hover-opened panel is not focused yet: the tap focuses the field instead of closing it
        if !panel.isVisible || !model.expanded || !panel.isKeyWindow { model.typing = true; expand(focus: true, why: "hotkey") }
        else { collapse(why: "hotkey") }
    }

    private func show() {
        expand(focus: true, why: "summon")
    }

    /// Esc cancels everything: the hold, the circle, listening, speech, the lens; aborts a running ask; folds when idle.
    func escape() {
        ttsID = nil
        speaker.stop()
        if lens.isOpen { lens.close(); return }
        if gesture.phase != .idle {
            _ = gesture.cancel()
            ready.hide(); halo.hide()
            panel.level = .statusBar
            _ = model.beginCapture()   // a late capture from this hold is now stale
            model.capturing = false
            voiceCancel()
            model.dropCircle()
            releaseHoldEsc()
            log("hold cancelled")
            return
        }
        if ptt.phase != .idle { voiceCancel(); return }
        if model.state.phase == .working { model.abort() } else { collapse(why: "esc"); halo.hide() }
    }

    /// While a task runs, Esc aborts from anywhere; the hotkey is released as soon as it ends.
    private var key = TapOrHold()

    private func optionSpaceDown() {
        lastActivity = Date()
        if !panel.isVisible { panel.orderFrontRegardless() }   // the pill appears as soon as the key goes down
        let token = key.down(at: ProcessInfo.processInfo.systemUptime)
        DispatchQueue.main.asyncAfter(deadline: .now() + TapOrHold.threshold) { [weak self] in
            guard let self else { return }
            if self.key.holdCheck(token: token, at: ProcessInfo.processInfo.systemUptime) == .startHold {
                self.holdBegan()
            }
        }
    }

    private func optionSpaceUp() {
        switch key.up(at: ProcessInfo.processInfo.systemUptime) {
        case .tap: ttsID = nil; speaker.stop(); toggle()
        case .endHold: holdEnded()
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

    // MARK: one gesture: hold, circle, speak, release

    func holdBegan() {
        ttsID = nil
        speaker.stop()   // never transcribe Reach's own voice
        lens.close()
        let h = gesture.holdStarted()
        model.dropCircle()
        log("hold \(h): listening, ready to circle")
        ready.show()
        // While circling, the panel sits under the ready layer so it never covers a loop drawn under the notch.
        panel.level = .floating
        panel.orderFrontRegardless()
        if voiceEsc.isEmpty {
            for mods in [0, Keys.option] {   // Option is still down during the hold
                if let h = Hotkeys.shared.register(keyCode: Keys.escape, modifiers: mods, name: "Esc (cancel hold)",
                                                   { [weak self] in self?.escape() }) { voiceEsc.append(h) }
            }
        }
        voicePress()
    }

    func holdEnded() {
        gesture.released()
        ready.hide()
        panel.level = .statusBar
        log("hold released")
        if ptt.phase == .listening { voiceRelease() }
        // no voice session (mic or speech refused, or still asking): a circle still opens the typing box
        if ptt.phase == .idle { releaseHoldEsc(); resolve(gesture.heard(.reject("nothing heard"))) }
    }

    private func circleDrawn(_ loop: [CGPoint], box: CGRect) {
        guard gesture.phase != .idle else { return }
        let gen = model.beginCapture()
        gesture.circleDrawn(gen: gen)
        halo.show(loop: loop)
        let top = NSScreen.screens.first?.frame.maxY ?? box.maxY
        let region = Lasso.region(box, primaryTop: top)
        let path = Lasso.contractPath(loop, primaryTop: top)
        log("circle: \(loop.count) points, box \(region.map { Int($0) })")
        model.selectionNote = "circled · reading…"
        let dir = model.files.capturesDir
        Task { @MainActor in
            var sel = Selection(region: region, path: path)
            var note = "circled"
            do {
                let (image, url) = try await RegionCapture.captureLoop(loop, box: box, into: dir)
                let text = await RegionCapture.recognizeText(image)
                let words = text.split { $0 == " " || $0 == "\n" }.count
                log("circle captured to \(url.path) (outside the loop masked), \(words) words read")
                sel = Selection(text: text.isEmpty ? nil : text, region: region, image_path: url.path, path: path)
                note = "circled · \(words) words read"
            } catch let e as CaptureError {
                log("capture: \(e)")
                note = e.description
                if case .permission = e { self.model.needsUser("allow Screen Recording for Reach") }
            } catch {
                log("capture error: \(error)")
                note = "capture failed"
            }
            guard gen == self.model.captureGen else { return }
            self.model.capturing = false
            if sel.image_path != nil { self.model.zoomTarget = sel }
            self.model.selectionNote = note
            self.resolve(self.gesture.circleCaptured(gen: gen, sel))
        }
    }

    /// Executes what a hold turned into.
    private func resolve(_ r: HubGesture.Resolution?) {
        guard let r else { return }
        switch r {
        case .ask(let text, let sel):
            log("hold -> one ask\(sel != nil ? " with the circle" : "")")
            model.voiceLine = "heard: \(text)"
            model.submit(text, selection: sel, fromVoice: true)
            if sel != nil { model.selectionNote = nil }
        case .typeWithChip(let sel):
            log("hold -> circle attached, typing")
            model.attachCircle(sel, note: model.selectionNote ?? "circled")
            model.typing = true
            model.voiceLine = "circle attached: type what to do"
            expand(focus: true, why: "circle without words")
        case .zoom(let sel):
            log("hold -> zoom")
            model.voiceLine = nil
            openLens(sel)
        case .nothing(let why):
            log("hold -> nothing (\(why))")
            model.voiceLine = why
        case .cancelled:
            break
        }
    }

    func openLens(_ sel: Selection) {
        // never over the panel or the circled area
        var avoid = [panel.frame]
        var target = CGRect(x: panel.frame.midX, y: panel.frame.minY, width: 1, height: 1)
        if let r = sel.region, r.count == 4 {
            let top = NSScreen.screens.first?.frame.maxY ?? 0
            target = CGRect(x: r[0], y: top - r[1] - r[3], width: r[2], height: r[3])
            avoid.append(target)
        }
        let vis = (NSScreen.screens.first { $0.frame.intersects(target) } ?? NSScreen.main)?.visibleFrame ?? .zero
        lens.open(sel) { size in LensPlacement.place(size: size, avoid: avoid, near: target, visible: vis) }
    }

    /// The one action an end state offers.
    private func act(_ a: PlainState.Action) {
        switch a {
        case .retry: model.retryLast()
        case .startServer: model.connect()
        case .openScreenSettings:
            PermissionProbe.askedScreenThisRun = true
            NSWorkspace.shared.open(URL(string: PermissionKind.screenRecording.settingsURL)!)
            setup.open()
        case .relaunch:
            PermissionProbe.relaunch()
        case .openMicSettings:
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!)
        case .circle, .none: break
        }
    }

    // MARK: hold to talk

    /// Key down: ask for Microphone and Speech Recognition the first time, then listen while the key is held.
    func voicePress() {
        guard case .start(let id) = ptt.press() else { return }
        expand(focus: false, why: "hold")
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
        stt = nil
        speech.recordTo = model.voiceMode == .cloud
            ? model.reachDir.appendingPathComponent("audio/hold-\(UUID().uuidString.prefix(8)).wav") : nil
        model.voiceLine = speech.onDevice ? "listening… (on this Mac)"
                                          : "listening… (on-device speech unavailable: using Apple's server)"
        log("voice listening (session \(id), on-device \(speech.onDevice))")
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
            self.model.level = 0
            _ = self.ptt.cancel()
            self.speech.cancel()
            self.endListening()
            self.model.voiceLine = "voice: \(why)"
        }, onLevel: { [weak self] v in
            guard let self, self.model.listening else { return }
            self.model.level = self.model.level * 0.5 + v * 0.5
        })
    }

    /// Key up: stop the mic, wait briefly for the final transcript.
    func voiceRelease() {
        guard case .finish(let id) = ptt.release() else { return }
        if !model.listening { _ = ptt.cancel(); return }   // still waiting on a permission prompt
        model.voiceLine = "transcribing…"
        speech.finish()
        if model.voiceMode == .cloud, let f = speech.recorded {
            let sid = "stt-\(id)-\(UUID().uuidString.prefix(6))"
            stt = (sid, f, Date(), nil, nil, false)
            model.send(.stt(audioPath: f.path, id: sid))
            DispatchQueue.main.asyncAfter(deadline: .now() + CloudVoice.sttWait) { [weak self] in self?.complete(id, force: true) }
        } else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.complete(id) }
        }
    }

    private func sttArrived(_ text: String, _ err: String?, _ id: String?) {
        guard let s = stt, s.id == id, !s.done else { return }
        stt?.text = text; stt?.err = err; stt?.done = true
        if lastHeard?.final == true { complete(ptt.session) }   // otherwise wait for Apple's final or the timeout
    }

    /// Ends the cloud STT for this hold and deletes the recording.
    private func dropStt() {
        if let f = stt?.file { try? FileManager.default.removeItem(at: f) }
        stt = nil
    }

    private func complete(_ id: Int, force: Bool = false) {
        // Cloud STT still in flight and time left: wait (the result or the 2.5 s timer calls back).
        if let s = stt, !s.done, !force, Date().timeIntervalSince(s.t0) < CloudVoice.sttWait,
           ptt.session == id, ptt.phase == .finishing { return }
        guard ptt.finished(session: id) else { return }
        endListening()
        let heard = lastHeard ?? Heard(text: "", confidences: [], final: false)
        if let s = stt {
            let pick = CloudVoice.pick(cloudText: s.done ? s.text : nil, cloudError: s.err,
                                       elapsed: Date().timeIntervalSince(s.t0), appleText: heard.text)
            dropStt()
            if case .cloud(let text) = pick {
                speech.cancel()
                log("voice heard (cloud) \"\(text)\" (on-device had \"\(heard.text)\")")
                model.draft = ""
                resolve(gesture.heard(.send(text)))
                return
            }
            log("voice: cloud transcript not used (\(s.err ?? (s.done ? "empty or no speech" : "timeout"))); on-device")
        }
        guard heard.final else {
            // Only a final transcript carries confidences; never send a partial.
            speech.cancel()
            log("voice: no final transcript within 2 s (last partial \"\(heard.text)\"); not sent")
            model.draft = ""
            model.voiceLine = "didn't catch that"
            resolve(gesture.heard(.reject("didn't catch that")))
            return
        }
        let avg = VoiceGate.average(heard.confidences).map { String(format: "%.2f", $0) } ?? "none"
        log("voice heard \"\(heard.text)\" (confidence \(avg))")
        let verdict = VoiceGate.judge(text: heard.text, confidences: heard.confidences)
        model.draft = ""
        if case .reject(let why) = verdict { model.voiceLine = why }
        resolve(gesture.heard(verdict))
    }

    func voiceCancel() {
        guard case .cancel = ptt.cancel() else { return }
        speech.cancel()
        dropStt()
        if let f = speech.recorded { try? FileManager.default.removeItem(at: f) }
        endListening()
        model.draft = ""
        model.voiceLine = "voice cancelled"
        log("voice cancelled")
    }

    // MARK: cloud voice replies

    /// Speaks an answer: cloud TTS when the setting is cloud (on-device after 3 s or on error), else on-device.
    func say(_ answer: String) {
        ttsID = nil
        speaker.stop()
        let text = SpokenText.clean(answer, limit: 600)
        guard !text.isEmpty else { return }
        guard model.voiceMode == .cloud, model.state.connected else { speaker.speak(answer); return }
        ttsSeq += 1
        let id = "tts-\(ttsSeq)"
        ttsID = id
        ttsStart = Date()
        model.send(.tts(text: text, id: id))
        DispatchQueue.main.asyncAfter(deadline: .now() + CloudVoice.ttsWait) { [weak self] in
            guard let self, self.ttsID == id else { return }
            log("cloud voice: no audio within 3 s; speaking on-device")
            self.ttsID = nil
            self.speaker.speak(answer)
        }
    }
    private var ttsStart = Date()

    private func ttsArrived(_ path: String, _ err: String?, _ id: String?) {
        let file = URL(fileURLWithPath: path)
        let mine = CloudVoice.isAudioPath(path, reachDir: model.reachDir)
        if CloudVoice.playCloud(path: path, error: err, id: id, current: ttsID, elapsed: Date().timeIntervalSince(ttsStart)), mine {
            ttsID = nil
            speaker.play(file: file)
            return
        }
        if !path.isEmpty, mine { try? FileManager.default.removeItem(at: file) }   // stale or late: never kept
        if id != nil, id == ttsID {   // failed: fall back now
            ttsID = nil
            log("cloud voice: \(err ?? "unusable audio"); speaking on-device")
            speaker.speak(model.state.answer)
        }
    }

    private func voiceBlocked(_ id: Int, _ why: String) {
        log("voice blocked: \(why) (circling still works)")
        if ptt.session == id { _ = ptt.cancel() }
        model.voiceLine = why
        model.needsUser(why)
    }

    private func endListening() {
        model.listening = false
        model.level = 0
        releaseHoldEsc()
    }

    private func releaseHoldEsc() {
        voiceEsc.forEach { Hotkeys.shared.unregister($0) }
        voiceEsc = []
    }

    /// One reusable ghost pointer. Task pointers rest 4 s; hand pointers (~15 Hz) fade calmly 1 s after the last one.
    private func showPointer(x: Double, y: Double, label rawLabel: String?, byHand: Bool) {
        let label = PointerLabel.short(rawLabel)   // at most 30 characters
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        // fit the label: dot 18 + gap 6 + capsule padding 12, up to ~220 pt
        let textW = label.map { ($0 as NSString).size(withAttributes: [.font: NSFont.systemFont(ofSize: 11, weight: .medium)]).width } ?? 0
        let size = NSSize(width: min(220, max(40, ceil(18 + 6 + textW + 12 + 8))), height: 34)
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
                if let label { Text(label).font(.system(size: 11, weight: .medium)).foregroundStyle(.white).lineLimit(1)
                    .padding(.horizontal, 6).padding(.vertical, 2).background(Capsule().fill(.black.opacity(0.6))) }
                Spacer(minLength: 0)
            })
            ghost = w
            ghostLabel = .some(label)
        }
        guard let w = ghost else { return }
        w.setFrame(NSRect(origin: origin, size: size), display: false)
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
