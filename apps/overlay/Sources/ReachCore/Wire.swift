import Foundation

/// A loosely typed JSON value, used for event payloads whose shape varies (Drex answers, locators).
public enum JSONValue: Codable, Equatable, Sendable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .object(let o): try c.encode(o)
        case .array(let a): try c.encode(a)
        case .null: try c.encodeNil()
        }
    }

    public subscript(key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }
    public var string: String? { if case .string(let s) = self { return s }; return nil }
    public var number: Double? { if case .number(let n) = self { return n }; return nil }
    public var object: [String: JSONValue]? { if case .object(let o) = self { return o }; return nil }
}

// MARK: - client -> server

public struct Selection: Codable, Equatable, Sendable {
    public var text: String?
    public var app: String?
    /// [x, y, w, h] in screen points (top-left origin).
    public var region: [Double]?
    public var image_path: String?
    /// "pinch" when the camera worker made it (relayed by the server); nil for the overlay's own selections.
    public var source: String?
    /// Circle selection: the loop as `[[x, y], ...]` in screen points, top-left origin (at most ~64 points).
    public var path: [[Double]]?

    public init(text: String? = nil, app: String? = nil, region: [Double]? = nil, image_path: String? = nil,
                source: String? = nil, path: [[Double]]? = nil) {
        self.text = text; self.app = app; self.region = region; self.image_path = image_path; self.source = source
        self.path = path
    }

    public var isEmpty: Bool { (text ?? "").isEmpty && region == nil && image_path == nil }
}

public enum ClientMessage: Equatable, Sendable {
    /// `role` tells the server what kind of client this is; "overlay" receives relayed hand selections and pointers.
    case hello(token: String, role: String? = "overlay")
    case ask(text: String, selection: Selection?)
    case abort(taskID: String?)
    case select(Selection)
    /// Cloud speech-to-text of a WAV under .reach/audio/ (answered by `sttResult`).
    case stt(audioPath: String, id: String)
    /// Cloud text-to-speech (answered by `ttsAudio`).
    case tts(text: String, id: String)
    /// Hands-free: one segment heard while always listening (answered by `addressed`). Text only, never audio.
    case utterance(text: String, wakeMatched: Bool)
    /// Hands mode on/off: the backend starts or stops the hand-as-mouse process (answered by `hands_state`).
    case hands(on: Bool)

    public func json() throws -> String {
        var obj: [String: JSONValue] = [:]
        let enc = JSONEncoder()
        switch self {
        case .hello(let token, let role):
            obj = ["type": .string("hello"), "token": .string(token)]
            if let role { obj["role"] = .string(role) }
        case .ask(let text, let sel):
            obj = ["type": .string("ask"), "text": .string(text)]
            if let sel { obj["selection"] = try JSONDecoder().decode(JSONValue.self, from: enc.encode(sel)) }
        case .abort(let id):
            obj = ["type": .string("abort")]
            if let id { obj["task_id"] = .string(id) }
        case .select(let sel):
            obj = ["type": .string("select"), "selection": try JSONDecoder().decode(JSONValue.self, from: enc.encode(sel))]
        case .stt(let path, let id):
            obj = ["type": .string("stt"), "audio_path": .string(path), "format": .string("wav"), "id": .string(id)]
        case .tts(let text, let id):
            obj = ["type": .string("tts"), "text": .string(text), "id": .string(id)]
        case .utterance(let text, let wake):
            obj = ["type": .string("utterance"), "text": .string(text), "source": .string("always_listening"),
                   "wake_matched": .bool(wake)]
        case .hands(let on):
            obj = ["type": .string("hands"), "on": .bool(on)]
        }
        enc.outputFormatting = [.sortedKeys]
        return String(decoding: try enc.encode(JSONValue.object(obj)), as: UTF8.self)
    }
}

// MARK: - server -> client

public struct Receipt: Decodable, Equatable, Sendable {
    public var task_id: String
    public var status: String
    public var reason: String
    public var system1_asks: Int
    public var tokens: Int
    public var duration_ms: Int
    public var est_cost_usd: Double?
    public var final_text: String?
    public var tools: [String]?
    public var ndi_calls: Int?
    /// done | not_done | aborted, from the last browser task (nil when there was none).
    public var outcome: String?
    /// verified | unverified | failed, from readbacks of native writes (nil when nothing was written).
    public var write_outcome: String?

    /// One compact line for the panel.
    public var line: String {
        "\(reason)\(write_outcome.map { " · write \($0)" } ?? "") · Drex asks \(system1_asks) · \(tokens) tok · \(duration_ms) ms"
    }
}

public struct EvidenceCard: Decodable, Equatable, Sendable {
    public var task_id: String?
    public var id: String?
    public var source: String?
    public var file: String?
    public var quote: String?
    public var page: Int?
    public var level: Int?
    public var region: JSONValue?
    public var locator: JSONValue?

    public var title: String {
        let name = source ?? file.map { ($0 as NSString).lastPathComponent } ?? "source"
        return page.map { "\(name) · p. \($0)" } ?? name
    }
    public var stableID: String { id ?? "\(file ?? source ?? "")#\(page ?? 0)#\(quote ?? "")" }
}

public struct DrexDecision: Equatable, Sendable {
    public var purpose: String
    public var choice: String?
    public var ms: Int?
    public var ok: Bool

    /// Pulls a readable choice out of the heterogeneous `answers` object.
    public init(event: JSONValue) {
        purpose = event["purpose"]?.string ?? "decision"
        ms = event["duration_ms"]?.number.map { Int($0) }
        if case .bool(let b)? = event["ok"] { ok = b } else { ok = true }
        var found: String?
        if let answers = event["answers"]?.object {
            for key in answers.keys.sorted() {
                guard let a = answers[key] else { continue }
                if let c = a["choice"]?.string { found = "\(key)=\(c)"; break }
                if let o = a.object, let (k, v) = o.first(where: { $0.value.number != nil }), let p = v.number {
                    found = "\(k) \(String(format: "%.2f", p))"; break
                }
                if let s = a.string { found = s; break }
            }
        }
        choice = found
    }

    public var line: String {
        var s = purpose
        if let choice { s += ": \(choice)" }
        if let ms { s += " (\(ms) ms)" }
        if !ok { s += " failed" }
        return s
    }
}

public enum ServerMessage: Equatable, Sendable {
    case ready(protocol: Int)
    case accepted(taskID: String)
    case event(taskID: String, kind: String, body: JSONValue)
    case receipt(Receipt)
    case evidence(EvidenceCard)
    /// A nil task id is a hand pointer relayed from the camera worker.
    /// `state` is the relayed hand state ("hover" | "pinch"), nil for task pointers.
    case pointer(taskID: String?, x: Double, y: Double, label: String?, state: String? = nil)
    /// A selection made by hand (pinch), relayed by the server. It does not start an ask.
    case selection(Selection)
    case transcript(taskID: String, role: String, text: String)
    case error(message: String, taskID: String?)
    case added(name: String, passages: Int)
    case sttResult(text: String, ms: Int, error: String?, id: String?)
    case ttsAudio(path: String, ms: Int, error: String?, id: String?)
    /// Hands-free: is the utterance aimed at the assistant?
    case addressed(AddressedReply)
    /// Hands mode: the backend's TRUE state (start, stop, or the process exiting on its own).
    case handsState(on: Bool, pid: Int?, reason: String?)
    case unknown(type: String)

    public static func decode(_ text: String) throws -> ServerMessage {
        let data = Data(text.utf8)
        let v = try JSONDecoder().decode(JSONValue.self, from: data)
        let type = v["type"]?.string ?? ""
        let tid = v["task_id"]?.string
        switch type {
        case "ready":
            return .ready(protocol: Int(v["protocol"]?.number ?? 1))
        case "accepted":
            return .accepted(taskID: tid ?? "")
        case "event":
            let body = v["event"] ?? .null
            return .event(taskID: tid ?? "", kind: body["type"]?.string ?? "", body: body)
        case "receipt":
            return .receipt(try JSONDecoder().decode(Receipt.self, from: data))
        case "evidence":
            return .evidence(try JSONDecoder().decode(EvidenceCard.self, from: data))
        case "pointer":
            guard let x = v["x"]?.number, let y = v["y"]?.number, x.isFinite, y.isFinite else { return .unknown(type: type) }
            return .pointer(taskID: (tid?.isEmpty ?? true) ? nil : tid, x: x, y: y, label: v["label"]?.string,
                            state: v["state"]?.string)
        case "selection":
            guard let sel = v["selection"] else { return .unknown(type: type) }
            return .selection(try JSONDecoder().decode(Selection.self, from: JSONEncoder().encode(sel)))
        case "transcript":
            return .transcript(taskID: tid ?? "", role: v["role"]?.string ?? "", text: v["text"]?.string ?? "")
        case "error":
            return .error(message: v["message"]?.string ?? "error", taskID: tid)
        case "added":
            return .added(name: v["name"]?.string ?? "", passages: Int(v["passages"]?.number ?? 0))
        case "stt_result":
            return .sttResult(text: v["text"]?.string ?? "", ms: Int(v["ms"]?.number ?? 0), error: v["error"]?.string,
                              id: v["id"]?.string)
        case "tts_audio":
            return .ttsAudio(path: v["path"]?.string ?? "", ms: Int(v["ms"]?.number ?? 0), error: v["error"]?.string,
                             id: v["id"]?.string)
        case "addressed":
            var on = false
            if case .bool(let b)? = v["addressed"] { on = b }
            return .addressed(AddressedReply(text: v["text"]?.string ?? "", pAddressed: v["p_addressed"]?.number,
                                             addressed: on, command: v["command"]?.string, reason: v["reason"]?.string))
        case "hands_state":
            var on = false
            if case .bool(let b)? = v["on"] { on = b }
            return .handsState(on: on, pid: v["pid"]?.number.map { Int($0) }, reason: v["reason"]?.string)
        default:
            return .unknown(type: type)
        }
    }
}
