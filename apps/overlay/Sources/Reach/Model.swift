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
    private var askAfterCapture: String?
    /// Bumped per capture; a finished capture whose generation is stale is dropped.
    private(set) var captureGen = 0
    var expandRequested: (() -> Void)?
    var circleRequested: (() -> Void)?

    let files: RunFiles
    private let client = ReachClient()
    private var retry: Timer?
    private var pendingAsk: String?
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

    func submit(_ text: String? = nil) {
        let text = (text ?? draft).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard state.connected else {
            pendingAsk = text
            log("not connected yet; ask queued")
            return
        }
        if let why = state.submitBlock(listening: listening, capturing: capturing) {
            if capturing, !listening, state.phase != .working {
                askAfterCapture = text
                draft = ""
                log("ask waits for the circle capture")
            } else {
                log("ask not sent: \(why)")
                selectionNote = why
            }
            return
        }
        log("state working")
        mutate { $0.submitted() }
        log("ask: \(text)" + (selection != nil ? " (with selection)" : ""))
        client.send(.ask(text: text, selection: selection))
        draft = ""
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
        if let q = askAfterCapture { askAfterCapture = nil; submit(q) }
        return true
    }

    /// The chip's clear button: drop the hand selection here and on the server (an empty `select` clears it there).
    func clearSelection() {
        selection = nil
        selectionNote = nil
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
        if case .ready = msg, let q = pendingAsk { pendingAsk = nil; submit(q) }
    }

    private func mutate(_ f: (inout PanelState) -> Void) {
        let before = state.phase
        f(&state)
        if state.phase != before { onPhase?(state.phase) }
    }
}
