import Foundation

public enum Phase: String, Equatable, Sendable {
    case idle, working, needsYou = "needs you", done, failed
}

/// Everything the panel shows, advanced by `apply`. No UI, no I/O, so it is unit-testable.
public struct PanelState: Equatable, Sendable {
    public var phase: Phase = .idle
    public var connected = false
    public var taskID: String?
    public var answer = ""
    public var note: String?
    public var receipt: Receipt?
    /// The verdict of the last receipt: Done, Needs you, Failed or Unverified (never Done for a failed write).
    public var verdict: Verdict?
    /// The tool running now (for the one-line progress); kept until the next tool or the end.
    public var tool: String?
    public var evidence: [EvidenceCard] = []
    public var decisions: [DrexDecision] = []   // newest last, at most 3
    public var gesture: HandStatus?               // what Hands mode is doing, from .reach/logs/hands.jsonl (EXPERIMENTAL)
    public var pointer: (x: Double, y: Double, label: String?)? {
        get { pointerStore.map { ($0.x, $0.y, $0.label) } }
        set { pointerStore = newValue.map { PointerSpot(x: $0.x, y: $0.y, label: $0.label) } }
    }
    private var pointerStore: PointerSpot?
    /// The selection made by hand (pinch), kept for the next typed or voice ask until cleared.
    public private(set) var handSelection: Selection?
    public var handChip: HandChip? { handSelection.map(HandChip.init) }
    private var awaitingAccept = false

    public init() {}

    struct PointerSpot: Equatable, Sendable { var x: Double; var y: Double; var label: String? }

    public var stateLine: String {
        var s = phase.rawValue
        if let note, !note.isEmpty { s += " · \(note)" }
        return s
    }

    // MARK: local inputs

    public mutating func submitted() {
        phase = .working
        taskID = nil
        awaitingAccept = true
        answer = ""
        note = nil
        receipt = nil
        verdict = nil
        tool = nil
        evidence = []
        pointer = nil
    }

    /// Newest Hands mode status tailed from `.reach/logs/hands.jsonl`; independent of the current task.
    public mutating func gestureDecided(_ g: HandStatus) {
        gesture = g
    }

    /// Something only the user can fix (no server, no token, a missing permission).
    public mutating func needsUser(_ why: String) {
        phase = .needsYou
        note = why
    }

    public mutating func clearHandSelection() { handSelection = nil }

    /// Why a new ask cannot go now, or nil. One ask at a time; never while listening or while a circle is being read.
    public func submitBlock(listening: Bool, capturing: Bool) -> String? {
        if phase == .working { return "an ask is already running (Esc stops it)" }
        if listening { return "still listening: release Option-Space to send" }
        if capturing { return "still reading the circle" }
        return nil
    }

    public mutating func disconnected(_ why: String) {
        connected = false
        if phase == .working { phase = .failed; note = "connection lost: \(why)" }
        else { phase = .needsYou; note = why }
    }

    // MARK: server inputs

    public mutating func apply(_ msg: ServerMessage) {
        switch msg {
        case .ready:
            connected = true
            if phase == .needsYou { phase = .idle; note = nil }
        case .accepted(let id):
            if awaitingAccept { taskID = id; awaitingAccept = false }
        case .event(let id, let kind, let body):
            guard isMine(id) else { return }
            switch kind {
            case "text_delta":
                answer += body["text"]?.string ?? ""
            case "decision":
                decisions.append(DrexDecision(event: body))
                if decisions.count > 3 { decisions.removeFirst(decisions.count - 3) }
            case "tool_call_start":
                tool = body["name"]?.string ?? "a tool"
            case "tool_call_end", "turn_start":
                break
            default:
                break
            }
        case .transcript(let id, let role, let text):
            if role == "assistant", isMine(id), !text.isEmpty { answer = text }
        case .receipt(let r):
            guard isMine(r.task_id) else { return }
            receipt = r
            if let t = r.final_text, !t.isEmpty { answer = t }
            let v = Verdict.of(r)
            verdict = v
            phase = v.phase
            switch v {
            case .done: note = nil
            case .stopped(let why): note = "stopped (\(why))"
            case .needsYou(let s), .failed(let s), .unverified(let s): note = s
            }
            taskID = nil
        case .evidence(let card):
            if let id = card.task_id, !isMine(id) { return }
            if !evidence.contains(where: { $0.stableID == card.stableID }) { evidence.append(card) }
        case .pointer(let id, let x, let y, let label, _):
            if let id, !isMine(id) { return }
            pointer = (x, y, label)
        case .error(let message, let id):
            if let id, !isMine(id) { return }
            if phase == .working || id != nil { phase = .failed }
            note = message
        case .selection(let sel):
            handSelection = sel.isEmpty ? nil : sel
        case .added(let name, let passages):
            note = "added \(name) (\(passages) passages)"
        case .unknown, .sttResult, .ttsAudio, .addressed, .handsState:
            break
        }
    }

    /// Events belong to the current task; before `accepted` arrives, accept any.
    private func isMine(_ id: String) -> Bool {
        guard let taskID else { return phase == .working }
        return id == taskID
    }

    public static func == (a: PanelState, b: PanelState) -> Bool {
        a.phase == b.phase && a.connected == b.connected && a.taskID == b.taskID && a.answer == b.answer
            && a.note == b.note && a.receipt == b.receipt && a.verdict == b.verdict && a.tool == b.tool && a.evidence == b.evidence && a.decisions == b.decisions
            && a.pointerStore == b.pointerStore && a.handSelection == b.handSelection && a.gesture == b.gesture
    }
}

/// What the "selected by hand" chip shows.
public struct HandChip: Equatable, Sendable {
    public var title = "Selected by hand"
    /// First non-empty line of the selected text, else the region size.
    public var line: String
    public var thumbnailPath: String?

    public init(_ sel: Selection) {
        let first = (sel.text ?? "").split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }.first { !$0.isEmpty }
        if let first {
            line = first.count > 80 ? String(first.prefix(79)) + "…" : first
        } else if let r = sel.region, r.count == 4 {
            line = "region \(Int(r[2]))×\(Int(r[3])) · no text read"
        } else {
            line = "no text read"
        }
        thumbnailPath = sel.image_path
    }
}
