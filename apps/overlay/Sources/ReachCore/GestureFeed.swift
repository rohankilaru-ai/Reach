import Foundation

/// What Hands mode is doing right now ("point to move · pinch to click", "dragging", "selecting", ...), one JSON line
/// per change in `.reach/logs/hands.jsonl` (written by hands_mode/run.py). EXPERIMENTAL: Hands mode is a separate
/// process; the overlay only reads its log.
public struct HandStatus: Equatable, Sendable {
    public var state: String

    public init(state: String) { self.state = state }

    public init?(jsonLine: Data) {
        guard let v = try? JSONDecoder().decode(JSONValue.self, from: jsonLine),
              let s = v["state"]?.string, !s.isEmpty else { return nil }
        state = s
    }

    public var line: String { "hands: \(state)" }
}

/// Tails the Hands mode log and returns the statuses appended since the last poll (complete lines only).
/// Starts at the current end of the file, so old lines are not replayed; a truncated file starts over.
public final class HandsLogTail: @unchecked Sendable {
    public let url: URL
    private var offset: UInt64
    private var partial = Data()

    public init(url: URL, fromStart: Bool = false) {
        self.url = url
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.uint64Value
        offset = fromStart ? 0 : (size ?? 0)
    }

    public func poll() -> [HandStatus] {
        guard let h = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? h.close() }
        let end = (try? h.seekToEnd()) ?? 0
        if end < offset { offset = 0; partial = Data() }
        guard end > offset else { return [] }
        try? h.seek(toOffset: offset)
        let data = (try? h.readToEnd()) ?? Data()
        offset += UInt64(data.count)
        partial.append(data)
        var out: [HandStatus] = []
        while let nl = partial.firstIndex(of: 0x0A) {
            let line = Data(partial[partial.startIndex..<nl])
            partial = Data(partial[partial.index(after: nl)...])
            if let s = HandStatus(jsonLine: line) { out.append(s) }
        }
        return out
    }
}
