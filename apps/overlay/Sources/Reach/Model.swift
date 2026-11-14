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
    var onStopSpeaking: (() -> Void)?

    let files: RunFiles
    private let client = ReachClient()
    private var retry: Timer?
    private var pendingAsk: (String, Bool)?
    var onPhase: ((Phase) -> Void)?
    /// (x, y, label, byHand). Hand pointers arrive at ~15 Hz and fade after 1 s without one.
    var onPointer: ((Double, Double, String?, Bool) -> Void)?
    /// A selection arrived from the camera worker (pinch); the panel opens without taking focus.
    var onHandSelection: ((Selection) -> Void)?
    private var lastHandPointer = Date.distantPast

    init(files: RunFiles) {
        self.files = files
        client.log = { log($0) }
        client.onMessage = { [weak self] msg in self?.handle(msg) }
        client.onClose = { [weak self] why in
            guard let self else { return }
            log("disconnected: \(why)")
            self.mutate { $0.disconnected(why) }
            self.scheduleReconnect()
        }
    }

    /// Connects once the server's run files exist; keeps trying every second up to `waitSeconds`.
    func connect(waitSeconds: Double = .infinity) {
        let deadline = Date().addingTimeInterval(waitSeconds)
        func attempt() {
            if let info = files.read() {
                client.connect(port: info.port, token: info.token)
                return
            }
            if Date() > deadline {
                log("no server: \(files.runDir.path)/server.json not found")
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
        case .pointer(let id, let x, let y, let label):
            let byHand = id == nil
            if !byHand || Date().timeIntervalSince(lastHandPointer) > 1 {
                log("pointer \(Int(x)),\(Int(y)) \(label ?? "")\(byHand ? " (hand)" : "")")
            }
            if byHand { lastHandPointer = Date() }
            onPointer?(x, y, label, byHand)
        case .selection(let sel):
            let chip = HandChip(sel)
            log("selection by hand: \"\(chip.line)\" thumbnail \(chip.thumbnailPath ?? "none")")
            selection = sel.isEmpty ? nil : sel
            selectionIsCircle = false
            selectionNote = nil
        case .transcript(_, let role, let text): log("transcript \(role): \(text.prefix(120))")
        case .error(let m, _): log("server error: \(m)")
        case .added(let n, let p): log("added \(n) (\(p) passages)")
        case .unknown(let t): log("ignored message type \(t)")
        }
        var after = state
        after.apply(msg)
        if after.phase != before { log("state \(after.phase.rawValue)") }
        mutate { $0.apply(msg) }
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
