import Foundation

/// One hand-gesture decision from Drex (purpose `gesture_route`), as written to `.reach/logs/drex.jsonl` by
/// finger_processing/commands.py. EXPERIMENTAL: the hand pipeline is a separate process; this only reads its log.
public struct GestureDecision: Equatable, Sendable {
    public var choice: String?        // idle | point | click
    public var probability: Double?   // Drex's probability for `choice`
    public var accepted: Bool         // false: below the confidence bar, so the mode did not change
    public var ok: Bool               // false: the Drex call failed and the local rules decided
    public var ms: Int?
    public var error: String?

    public init?(jsonLine: Data) {
        guard let v = try? JSONDecoder().decode(JSONValue.self, from: jsonLine),
              v["purpose"]?.string == "gesture_route" else { return nil }
        let a = v["answers"]?["gesture"]
        choice = a?["choice"]?.string
        probability = choice.flatMap { a?["probabilities"]?[$0]?.number }
        if case .bool(let b)? = v["accepted"] { accepted = b } else { accepted = true }
        if case .bool(let b)? = v["ok"] { ok = b } else { ok = true }
        ms = v["latency_ms"]?.number.map { Int($0) }
        error = v["error"]?.string
    }

    public var line: String {
        guard ok else { return "hand: drex failed, local rules decided" + (error.map { " (\($0.prefix(40)))" } ?? "") }
        var s = "hand: \(choice ?? "?")"
        if let probability { s += " \(String(format: "%.2f", probability))" }
        if let ms { s += " · \(ms) ms" }
        if !accepted { s += " · not confident, mode kept" }
        return s
    }
}

/// Tails the Drex log and returns gesture decisions appended since the last poll (complete lines only).
/// Starts at the current end of the file, so old decisions are not replayed.
public final class DrexLogTail: @unchecked Sendable {
    public let url: URL
    private var offset: UInt64
    private var partial = Data()

    public init(url: URL, fromStart: Bool = false) {
        self.url = url
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.uint64Value
        offset = fromStart ? 0 : (size ?? 0)
    }

    public func poll() -> [GestureDecision] {
        guard let h = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? h.close() }
        let end = (try? h.seekToEnd()) ?? 0
        if end < offset { offset = 0; partial = Data() }   // log truncated or replaced
        guard end > offset else { return [] }
        try? h.seek(toOffset: offset)
        let data = (try? h.readToEnd()) ?? Data()
        offset += UInt64(data.count)
        partial.append(data)
        var out: [GestureDecision] = []
        while let nl = partial.firstIndex(of: 0x0A) {
            let line = Data(partial[partial.startIndex..<nl])
            partial = Data(partial[partial.index(after: nl)...])
            if let g = GestureDecision(jsonLine: line) { out.append(g) }
        }
        return out
    }
}
