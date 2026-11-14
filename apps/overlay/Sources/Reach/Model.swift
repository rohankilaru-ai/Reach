import AppKit
import ReachCore

func log(_ s: String) {
    print("[reach] \(s)")
    fflush(stdout)
}

/// Owns the connection and the panel state. Main thread only.
@MainActor
final class OverlayModel: ObservableObject {
    @Published private(set) var state = PanelState()
    @Published var draft = ""
    /// Hands mode (your hand moves the real mouse): the backend's TRUE state from `hands_state`.
    @Published var handsOn = false
    /// A short pill line about hands mode ("Hands off", the one-time hint, or "needs you"); cleared by a timer.
    @Published var handsLine: String?
    /// Live phase from hands_mode (no_hand / pointing / pinching / dragging / paused / leaving).
    @Published var handsPhase: String?
    var hands = HandsModeState()
    private var handsLineID = 0
    var onHandsChange: ((Bool) -> Void)?
    /// Developer toggle (menu bar): raw receipt lines, Drex decisions and cost in Details. Off by default.
    @Published var technicalDetails: Bool = UserDefaults.standard.bool(forKey: "technicalDetails") {
        didSet { UserDefaults.standard.set(technicalDetails, forKey: "technicalDetails") }
    }

    func flashHands(_ line: String, seconds: Double) {
        handsLineID += 1
        let id = handsLineID
        handsLine = line
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in
            if self?.handsLineID == id { self?.handsLine = nil }
        }
    }
    @Published var selection: Selection?
    @Published var selectionNote: String?
    /// Voice line under the text field: "listening…", "heard: …", "didn't catch that". Survives `submitted()`.
    @Published var voiceLine: String?
    @Published var listening = false
    /// Pill (false) or full panel (true). The app controller owns when it changes.
    @Published var expanded = true
    /// A circle capture + OCR is in flight; an ask typed meanwhile waits for it (it carries the text and image).
    @Published var capturing = false
    private var askAfterCapture: (String, Bool)?
    /// Bumped per capture; a finished capture whose generation is stale is dropped.
    private(set) var captureGen = 0
    var expandRequested: (() -> Void)?
    /// The attached selection is the user's own circle (used by one ask, then dropped), not a hand selection.
    @Published var selectionIsCircle = false
    /// The last circle with a captured image: what the Zoom chip and the word "zoom" open.
    @Published var zoomTarget: Selection?
    /// Audio level 0...1 while listening, for the meter.
    @Published var level: Float = 0
    /// Height the answer area may take (set from the screen by the app controller).
    @Published var answerMax: CGFloat = 360
    /// The answer area's measured content height; publishing it makes the window refit after late layout changes.
    @Published var measuredAnswer: CGFloat = 0
    @Published var speaking = false
    /// The panel was summoned to type (tap or a circle without words): the text field shows.
    @Published var typing = false
    /// Receipt, Drex decisions, tools and cost sit behind a collapsed "Details" disclosure. Remembered.
    @Published var showDetails: Bool = UserDefaults.standard.bool(forKey: "showDetails") {
        didSet { UserDefaults.standard.set(showDetails, forKey: "showDetails") }
    }
    /// A tool-using run's one-line result, shown in the pill for ~3 s instead of opening the panel.
    @Published var compact: CompactResult?
    /// A run ended needing the user: the menu bar icon shows a dot until the next ask.
    @Published var needsYouBadge = false
    var onCompact: ((CompactResult) -> Void)?
    var onBadge: ((Bool) -> Void)?
    /// Soft system sounds on completion. Persisted, default on.
    @Published var soundsOn: Bool = (UserDefaults.standard.object(forKey: "sounds") as? Bool) ?? true {
        didSet { UserDefaults.standard.set(soundsOn, forKey: "sounds") }
    }
    /// The last ask, for "Try again".
    private var lastAsk: (String, Selection?, Bool)?
    /// Speak replies to voice asks automatically. Persisted.
    @Published var speakReplies: Bool = (UserDefaults.standard.object(forKey: "speakReplies") as? Bool) ?? true {
        didSet { UserDefaults.standard.set(speakReplies, forKey: "speakReplies") }
    }
    /// The running (or last) ask came from voice.
    private(set) var lastAskFromVoice = false
    var onZoom: ((Selection) -> Void)?
    var onSpeak: ((String) -> Void)?
    /// Cloud voice results (text, error, id) and (path, error, id).
    var onSttResult: ((String, String?, String?) -> Void)?
    var onTtsAudio: ((String, String?, String?) -> Void)?
    /// Voice: cloud (Whisper + DeepInfra TTS) or on-device (Apple). Default cloud.
    @Published var voiceMode: VoiceMode = VoiceMode.load() {
        didSet { UserDefaults.standard.set(voiceMode.rawValue, forKey: VoiceMode.key) }
    }
    var reachDir: URL { files.reachDir }
    func send(_ m: ClientMessage) { client.send(m) }
    var onStopSpeaking: (() -> Void)?

    let files: RunFiles
    private let client = ReachClient()
    private var retry: Timer?
    private lazy var launcher = BackendLauncher(files: files)
    private var reconnectFailures = 0
    private var pendingAsk: (String, Bool)?
    private let gestureTail: HandsLogTail
    private var gestureTimer: Timer?
    var onPhase: ((Phase) -> Void)?
    /// (x, y, label, byHand). Hand pointers arrive at ~15 Hz and fade after 1 s without one.
    var onPointer: ((Double, Double, String?, Bool) -> Void)?
    /// A selection arrived from the camera worker (pinch); the panel opens without taking focus.
    var onHandSelection: ((Selection) -> Void)?
    /// Hands-free: relayed hand state ("pinch" / "hover" / nil) with each hand pointer.
    var onHandState: ((String?) -> Void)?
    /// Hands-free: the server's `addressed` reply.
    var onAddressed: ((AddressedReply) -> Void)?
    /// Hands-free listening is on (the pill shows the listening dot); `handsFreeFlicker` is the faint "ignored" blink.
    @Published var handsFree = false
    @Published var handsFreeFlicker = false
    @Published var handsFreeChip: String?
    /// When the current selection was attached (hands-free asks use it only within 60 s).
    var selectionAt = Date.distantPast
    private var lastHandPointer = Date.distantPast

    init(files: RunFiles) {
        self.files = files
        gestureTail = HandsLogTail(url: files.reachDir.appendingPathComponent("logs/hands.jsonl"))
        client.log = { log($0) }
        client.onMessage = { [weak self] msg in self?.handle(msg) }
        client.onClose = { [weak self] why in
            guard let self else { return }
            log("disconnected: \(why)")
            self.mutate { $0.disconnected(why) }
            self.scheduleReconnect()
        }
        // Stream what Hands mode is doing (hands_mode/run.py writes one line per change).
        gestureTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.pollGestures() }
        }
    }

    private func pollGestures() {
        guard let g = gestureTail.poll().last else { return }
        mutate { $0.gestureDecided(g) }
    }

    /// Connects once the server's run files exist; keeps trying every second up to `waitSeconds`.
    func connect(waitSeconds: Double = .infinity) {
        let deadline = Date().addingTimeInterval(waitSeconds)
        func attempt() {
            if let info = files.read(), launcher.serverReachable() {
                reconnectFailures = 0
                client.connect(port: info.port, token: info.token)
                return
            }
            launcher.ensureRunning()   // nothing is listening: start the backend ourselves
            if Date() > deadline {
                log("no server reachable: \(files.runDir.path)/server.json")
                mutate { $0.needsUser("start `reach serve`") }
                return
            }
            if state.phase != .needsYou { mutate { $0.needsUser("waiting for `reach serve`") } }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { attempt() }
        }
        attempt()
    }

    private func scheduleReconnect() {
        retry?.invalidate()
        reconnectFailures += 1
        if reconnectFailures >= 2 { launcher.ensureRunning() }
        retry = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.connect() }
        }
    }

    func submit(_ text: String? = nil, selection explicit: Selection? = nil, fromVoice: Bool = false) {
        let text = (text ?? draft).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if ZoomCommand.isZoom(text) {   // local: never reaches the server
            draft = ""
            if let z = explicit ?? zoomTarget, z.image_path != nil { onZoom?(z) }
            else { selectionNote = "circle something first, then say zoom" }
            return
        }
        guard state.connected else {
            pendingAsk = (text, fromVoice)
            log("not connected yet; ask queued")
            return
        }
        if let why = state.submitBlock(listening: listening, capturing: capturing) {
            if capturing, !listening, state.phase != .working {
                askAfterCapture = (text, fromVoice)
                draft = ""
                log("ask waits for the circle capture")
            } else {
                log("ask not sent: \(why)")
                selectionNote = why
            }
            return
        }
        onStopSpeaking?()
        let sel = explicit ?? selection
        lastAskFromVoice = fromVoice
        log("state working")
        compact = nil
        if needsYouBadge { needsYouBadge = false; onBadge?(false) }
        mutate { $0.submitted() }
        log("ask\(fromVoice ? " (voice)" : ""): \(text)" + (sel != nil ? " (with selection\(sel?.path != nil ? ", circle" : ""))" : ""))
        client.send(.ask(text: text, selection: sel))
        sentAsks.append((text, sel))
        lastAsk = (text, sel, fromVoice)
        typing = false
        draft = ""
        if selectionIsCircle || explicit?.path != nil {   // a circle belongs to one ask
            if selectionIsCircle { selection = nil; selectionNote = nil; selectionIsCircle = false }
        }
    }

    /// Asks sent in this run (text, selection): the hub selftest checks there is exactly one per gesture.
    private(set) var sentAsks: [(String, Selection?)] = []

    /// A circle drawn without words: attach it as a chip for the typed ask (local only; the ask carries it).
    func attachCircle(_ sel: Selection, note: String) {
        selection = sel
        selectionAt = Date()
        selectionIsCircle = true
        selectionNote = note
    }

    /// Start of a hold: drop a circle chip from before (each hold starts clean).
    func dropCircle() {
        if selectionIsCircle { selection = nil; selectionNote = nil; selectionIsCircle = false }
        zoomTarget = nil
    }

    func retryLast() {
        guard let a = lastAsk else { return }
        log("try again: \(a.0)")
        submit(a.0, selection: a.1, fromVoice: a.2)
    }

    func abort() {
        guard state.phase == .working else { return }
        log("abort sent")
        client.send(.abort(taskID: state.taskID))
    }

    func setSelection(_ sel: Selection?, note: String?) {
        selection = sel
        selectionNote = note
        mutate { $0.clearHandSelection() }  // a drag-selected region replaces any hand selection
        if let sel { client.send(.select(sel)) }
    }

    /// Starts a capture generation; `finishCapture` with an older one is ignored.
    func beginCapture() -> Int {
        captureGen += 1
        capturing = true
        return captureGen
    }

    /// Applies a capture result only if it is the newest; then sends an ask that was waiting for it.
    @discardableResult
    func finishCapture(_ gen: Int, _ sel: Selection, note: String) -> Bool {
        guard gen == captureGen else { log("stale capture \(gen) dropped (newest \(captureGen))"); return false }
        capturing = false
        setSelection(sel, note: note)
        if let q = askAfterCapture { askAfterCapture = nil; submit(q.0, fromVoice: q.1) }
        return true
    }

    /// The chip's clear button: drop the hand selection here and on the server (an empty `select` clears it there).
    func clearSelection() {
        selection = nil
        selectionNote = nil
        selectionIsCircle = false
        mutate { $0.clearHandSelection() }
        client.send(.select(Selection()))
        log("selection cleared")
    }

    func needsUser(_ why: String) { mutate { $0.needsUser(why) } }

    private func handle(_ msg: ServerMessage) {
        let before = state.phase
        switch msg {
        case .ready(let p):
            log("connected (protocol \(p))")
        case .accepted(let id): log("accepted task \(id)")
        case .event(_, let kind, let body):
            if kind == "decision" { log("drex \(DrexDecision(event: body).line)") }
            else if kind != "text_delta" { log("event \(kind)") }
        case .receipt(let r): log("receipt \(r.status): \(r.line)")
        case .evidence(let c): log("evidence \(c.title)")
        case .pointer(let id, let x, let y, let label, let handState):
            let byHand = id == nil
            if !byHand || Date().timeIntervalSince(lastHandPointer) > 1 {
                log("pointer \(Int(x)),\(Int(y)) \(label ?? "")\(byHand ? " (hand)" : "")")
            }
            if byHand { lastHandPointer = Date(); onHandState?(handState) }
            onPointer?(x, y, label, byHand)
        case .selection(let sel):
            let chip = HandChip(sel)
            log("selection by hand: \"\(chip.line)\" thumbnail \(chip.thumbnailPath ?? "none")")
            selection = sel.isEmpty ? nil : sel
            selectionAt = Date()
            selectionIsCircle = false
            selectionNote = nil
        case .transcript(_, let role, let text): log("transcript \(role): \(text.prefix(120))")
        case .error(let m, _): log("server error: \(m)")
        case .added(let n, let p): log("added \(n) (\(p) passages)")
        case .sttResult(let t, let ms, let err, let id):
            log("stt \(id ?? "-"): \(err.map { "error \($0)" } ?? "\"\(t.prefix(80))\"") (\(ms) ms)")
            onSttResult?(t, err, id)
            return
        case .ttsAudio(let path, let ms, let err, let id):
            log("tts \(id ?? "-"): \(err.map { "error \($0)" } ?? "audio ready") (\(ms) ms)")
            onTtsAudio?(path, err, id)
            return
        case .addressed(let r):
            // never log the overheard text: only the probability and the flag
            log("addressed \(r.addressed) p \(r.pAddressed.map { String(format: "%.2f", $0) } ?? "-")")
            onAddressed?(r)
            return
        case .handsState(let on, let pid, let reason, let phase):
            if phase == nil || !on {
                log("hands mode \(on ? "on" : "off")\(pid.map { " (pid \($0))" } ?? "")\(reason.map { " \($0)" } ?? "")")
            }
            let effect = hands.apply(on: on, pid: pid, reason: reason, phase: phase)
            if handsPhase != hands.phase { handsPhase = hands.phase }
            switch effect {
            case .showHint: flashHands(HandsMode.hint, seconds: HandsMode.hintSeconds)
            case .showOff: flashHands(HandsMode.offLabel, seconds: HandsMode.offSeconds)
            case .none: break
            }
            if handsOn != on { handsOn = on; onHandsChange?(on) }
            return
        case .unknown(let t): log("ignored message type \(t)")
        }
        var after = state
        after.apply(msg)
        if after.phase != before { log("state \(after.phase.rawValue)") }
        // decided before the phase change reaches the app, so a tool run never opens the panel
        if case .receipt(let r) = msg, before == .working, after.phase != .working, let v = after.verdict {
            let end = TaskEnd.of(r, verdict: v)
            if case .compact(let c) = end { compact = c; log("compact: \(c.line)") } else { compact = nil }
            let needs = { if case .needsYou = v { return true }; return false }()
            if needs != needsYouBadge { needsYouBadge = needs; onBadge?(needs) }
        }
        mutate { $0.apply(msg) }
        if case .receipt = msg, let c = compact, before == .working { onCompact?(c) }
        if case .selection(let sel) = msg, !sel.isEmpty { onHandSelection?(sel) }
        if case .ready = msg, let q = pendingAsk { pendingAsk = nil; submit(q.0, fromVoice: q.1) }
        if case .receipt = msg, before == .working, state.phase != .working, let v = state.verdict {
            log("verdict: \(v.title)")
            if case .stopped = v {} else if SpokenText.autoSpeak(fromVoice: lastAskFromVoice, toggle: speakReplies),
                                           !state.answer.isEmpty {
                onSpeak?(state.answer)
            }
        }
    }

    private func mutate(_ f: (inout PanelState) -> Void) {
        let before = state.phase
        f(&state)
        if state.phase != before { onPhase?(state.phase) }
    }
}
