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

    let files: RunFiles
    private let client = ReachClient()
    private var retry: Timer?
    private var pendingAsk: String?
    var onPhase: ((Phase) -> Void)?
    var onPointer: ((Double, Double, String?) -> Void)?

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
        if let sel { client.send(.select(sel)) }
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
        case .pointer(_, let x, let y, let label):
            log("pointer \(Int(x)),\(Int(y)) \(label ?? "")")
            onPointer?(x, y, label)
        case .transcript(_, let role, let text): log("transcript \(role): \(text.prefix(120))")
        case .error(let m, _): log("server error: \(m)")
        case .added(let n, let p): log("added \(n) (\(p) passages)")
        case .unknown(let t): log("ignored message type \(t)")
        }
        var after = state
        after.apply(msg)
        if after.phase != before { log("state \(after.phase.rawValue)") }
        mutate { $0.apply(msg) }
        if case .ready = msg, let q = pendingAsk { pendingAsk = nil; submit(q) }
    }

    private func mutate(_ f: (inout PanelState) -> Void) {
        let before = state.phase
        f(&state)
        if state.phase != before { onPhase?(state.phase) }
    }
}
