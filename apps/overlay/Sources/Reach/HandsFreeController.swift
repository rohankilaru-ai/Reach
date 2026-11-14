import AppKit
import ReachCore
import Speech

/// Hands-free listening: continuous on-device recognition, segmented by pauses, each segment checked by the
/// backend (`utterance` -> `addressed`). Only text leaves the recognizer; no audio is written anywhere, and
/// overheard text is dropped as soon as the reply (or the 1.5 s timeout) says it was not for Reach.
@MainActor
final class HandsFreeController {
    private let model: OverlayModel
    private let speech = SpeechInput()
    private(set) var toggle = HandsFreeToggle()
    private var seg = Segmenter()
    private var pending = PendingUtterance()
    private var echo = EchoGate()
    private var pinch = PinchSignal()
    private var running = false
    private var startedAt = Date.distantPast
    private var timer: Timer?
    /// Runs an addressed ask through the normal ask path (AppController wires this).
    var onAsk: (String) -> Void = { _ in }
    /// Pinch held / released: same as holding / releasing Option-Space.
    var onPinchHold: (Bool) -> Void = { _ in }
    /// Whether the push-to-talk path owns the microphone right now.
    var pttBusy: () -> Bool = { false }
    var onChange: () -> Void = {}
    /// "Reach, start dictating" / "Reach, stop dictating" (handled locally, never sent).
    var onDictation: (Bool) -> Void = { _ in }

    private var now: Double { ProcessInfo.processInfo.systemUptime }

    init(model: OverlayModel) {
        self.model = model
        model.onAddressed = { [weak self] r in self?.replied(r) }
        model.onHandState = { [weak self] s in self?.handState(s) }
    }

    var isOn: Bool { toggle.on }

    func setOn(_ on: Bool) {
        toggle.set(on)
        log("hands-free \(on ? "on" : "off")")
        apply()
    }

    func flip() { setOn(!toggle.on) }

    func pauseFiveMinutes() {
        toggle.pause(minutes: 5, now: now)
        log("hands-free paused for 5 minutes")
        apply()
    }

    func speakingChanged(_ on: Bool) {
        echo.speakingChanged(on, now: now)
        if on { stopRecognizer() }   // never transcribe Reach's own voice
    }

    private func apply() {
        model.handsFree = toggle.on
        if toggle.on {
            model.voiceLine = HandsFree.hint
            if timer == nil {
                timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                    MainActor.assumeIsolated { self?.tick() }
                }
            }
        } else {
            timer?.invalidate(); timer = nil
            stopRecognizer()
            if pinch.pinching { onPinchHold(false) }
            pinch = PinchSignal()
        }
        onChange()
    }

    private func tick() {
        let t = now
        if case .release = pinch.tick(now: t) { onPinchHold(false) }
        if pending.expire(at: t) == .ignore { flicker() }
        let want = toggle.active(now: t) && echo.mayListen(now: t) && !pttBusy() && !pinch.pinching
        if want, !running { startRecognizer() }
        if !want, running { stopRecognizer() }
        if running, Date().timeIntervalSince(startedAt) > HandsFree.restartEvery { restart() }
        if let s = seg.tick(now: t) { emit(s) }
    }

    private func startRecognizer() {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
            model.voiceLine = "hands-free needs Microphone and Speech Recognition (Set up Reach…)"
            setOn(false); return
        }
        guard speech.onDevice else {   // never fall back to Apple's server for always-on listening
            model.voiceLine = "hands-free needs on-device speech recognition"
            setOn(false); return
        }
        speech.recordTo = nil          // text only: no audio file, ever
        running = true
        startedAt = Date()
        seg.reset()
        speech.start(onResult: { [weak self] heard in
            guard let self, self.running else { return }
            if let s = self.seg.feed(heard.text, now: self.now) { self.emit(s) }
        }, onError: { [weak self] _ in
            guard let self else { return }
            self.stopRecognizer()   // the next tick restarts it (about 0.2 s later)
        })
    }

    private func stopRecognizer() {
        guard running else { return }
        running = false
        speech.cancel()
        seg.reset()
    }

    private func restart() { stopRecognizer() }   // tick() starts a fresh task

    /// One segment: "stop listening" locally, else ask the backend whether it was for Reach.
    private func emit(_ s: Segmenter.Segment) {
        restart()   // fresh task per segment: partials can revise earlier words
        if WakeWord.isStopListening(s.text) { setOn(false); return }
        if let d = DictationCommand.wakeToggle(s.text) { onDictation(d); return }
        guard model.state.connected else { flicker(); return }   // unreachable: not addressed
        pending.sent(text: s.text, ask: s.ask, at: now)
        model.send(.utterance(text: s.text, wakeMatched: s.wakeMatched))
    }

    private func replied(_ r: AddressedReply) {
        guard let d = pending.reply(r, at: now) else { return }
        act(d)
    }

    /// A relayed hand gesture's router command: handled exactly like the same command routed from speech.
    func run(command: String) {
        guard HandsFree.commands.contains(command), command != "uncertain" else { log("gesture: unknown command \(command)"); return }
        act(HandGestures.decision(command: command))
    }

    private func act(_ d: AddressedDecision) {
        switch d {
        case .ignore: flicker()
        case .ask(let text, let chip):
            model.handsFreeChip = chip
            onAsk(text)
        case .localStop(let chip):
            model.handsFreeChip = chip
            model.onStopSpeaking?()
            model.abort()
        }
    }

    private func flicker() {
        model.handsFreeFlicker = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in self?.model.handsFreeFlicker = false }
    }

    private func handState(_ s: String?) {
        guard toggle.active(now: now) else { return }   // pinch means "select" unless hands-free is on
        switch pinch.update(state: s, now: now) {
        case .press: stopRecognizer(); onPinchHold(true)
        case .release: onPinchHold(false)
        case .none: break
        }
    }
}
