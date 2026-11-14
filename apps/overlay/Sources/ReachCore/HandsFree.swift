import Foundation

/// Hands-free listening: pure logic only (no audio, no UI). The app feeds recognizer text in and acts on the
/// decisions that come out. Overheard text is never stored here beyond the one pending utterance.
public enum HandsFree {
    public static let hint = "Say \"Reach, ...\" or just ask; I only act when you are talking to me."
    public static let pauseGap: Double = 0.9
    public static let replyTimeout: Double = 1.5
    public static let echoResume: Double = 0.5
    public static let restartEvery: Double = 50
    public static let selectionMaxAge: Double = 60

    /// The 12 router commands (backend snake_case names).
    public static let commands: Set<String> = [
        "explain_this", "open_this", "scroll_down", "scroll_up", "pause", "continue", "compare_these",
        "put_this_here", "cancel", "next", "stop", "uncertain",
    ]
    /// Commands handled locally without the model: stop speech and abort a running ask.
    public static let localStop: Set<String> = ["stop", "cancel", "pause"]
}

/// Strict wake phrase: "reach" or "hey reach" at the very start (case and punctuation ignored).
public enum WakeWord {
    public struct Match: Equatable, Sendable {
        public var matched: Bool
        /// The text with the wake phrase removed (the ask); the original text when not matched.
        public var stripped: String
    }

    public static func match(_ text: String) -> Match {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let words = trimmed.split(whereSeparator: { $0.isWhitespace })
        func norm(_ w: Substring) -> String { w.lowercased().filter { $0.isLetter } }
        var drop = 0
        if words.count >= 1, norm(words[0]) == "reach" { drop = 1 }
        else if words.count >= 2, norm(words[0]) == "hey", norm(words[1]) == "reach" { drop = 2 }
        guard drop > 0 else { return Match(matched: false, stripped: trimmed) }
        var rest = words.dropFirst(drop).joined(separator: " ")
        while let f = rest.first, f == "," || f == "." || f == "!" || f == "?" || f == ":" || f == ";" || f == " " {
            rest.removeFirst()
        }
        return Match(matched: true, stripped: rest)
    }

    /// "Reach, stop listening" (handled locally, never sent).
    public static func isStopListening(_ text: String) -> Bool {
        let m = match(text)
        guard m.matched else { return false }
        let t = m.stripped.lowercased().filter { $0.isLetter || $0 == " " }
        return t.trimmingCharacters(in: .whitespaces) == "stop listening"
    }
}

/// Cuts the running transcript into utterances: about 0.9 s without new words, or a sentence end.
public struct Segmenter: Sendable {
    public struct Segment: Equatable, Sendable {
        public var text: String
        public var wakeMatched: Bool
        public var ask: String
    }

    public private(set) var current = ""
    private var lastChange: Double = 0

    public init() {}

    /// A partial (or final) transcript of the current recognition task. Returns a segment on a sentence end.
    public mutating func feed(_ text: String, now: Double) -> Segment? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t != current { current = t; lastChange = now }
        if let last = t.last, ".?!".contains(last) { return cut() }
        return nil
    }

    /// Called on a timer; returns a segment once the pause is long enough.
    public mutating func tick(now: Double) -> Segment? {
        guard !current.isEmpty, now - lastChange >= HandsFree.pauseGap else { return nil }
        return cut()
    }

    /// Drops whatever is buffered (echo, toggle off, restart).
    public mutating func reset() { current = "" }

    private mutating func cut() -> Segment? {
        let text = current
        current = ""
        return Segmenter.accept(text)
    }

    /// Fewer than 2 words is ignored unless it starts with the wake phrase; an empty ask is ignored.
    public static func accept(_ text: String) -> Segment? {
        let m = WakeWord.match(text)
        let words = text.split(whereSeparator: { $0.isWhitespace }).count
        if !m.matched, words < 2 { return nil }
        if m.matched, m.stripped.isEmpty { return nil }
        return Segment(text: text, wakeMatched: m.matched, ask: m.stripped)
    }
}

/// The server's `addressed` reply.
public struct AddressedReply: Equatable, Sendable {
    public var text: String
    public var pAddressed: Double?
    public var addressed: Bool
    public var command: String?
    public var reason: String?
    public init(text: String, pAddressed: Double? = nil, addressed: Bool, command: String? = nil, reason: String? = nil) {
        self.text = text; self.pAddressed = pAddressed; self.addressed = addressed; self.command = command
        self.reason = reason
    }
}

/// What to do with one segment once the reply (or the timeout) is in.
public enum AddressedDecision: Equatable, Sendable {
    /// Not addressed / timeout / unreachable: nothing visible but a faint flicker; the text is dropped.
    case ignore
    /// Run as a normal ask; `chip` is a quiet command label when the router picked one.
    case ask(String, chip: String?)
    /// stop / cancel / pause: stop speech, abort a running ask, show the chip; no model call.
    case localStop(chip: String)

    public static func decide(reply: AddressedReply?, ask: String, timedOut: Bool) -> AddressedDecision {
        guard !timedOut, let reply, reply.addressed else { return .ignore }
        if let c = reply.command, HandsFree.commands.contains(c) {
            let chip = c.replacingOccurrences(of: "_", with: " ")
            if HandsFree.localStop.contains(c) { return .localStop(chip: chip) }
            return .ask(ask, chip: chip)
        }
        return .ask(ask, chip: nil)
    }
}

/// One pending utterance at a time; replies are matched by their echoed text and expire after 1.5 s.
public struct PendingUtterance: Sendable {
    public private(set) var pending: (text: String, ask: String, sentAt: Double)?
    public init() {}

    public mutating func sent(text: String, ask: String, at now: Double) { pending = (text, ask, now) }

    /// A reply arrived. Returns the decision when it matches the pending utterance in time, else nil (stale).
    public mutating func reply(_ r: AddressedReply, at now: Double) -> AddressedDecision? {
        guard let p = pending, p.text == r.text else { return nil }
        pending = nil
        return AddressedDecision.decide(reply: r, ask: p.ask, timedOut: now - p.sentAt > HandsFree.replyTimeout)
    }

    /// Timer: past 1.5 s with no reply counts as not addressed; the text is dropped.
    public mutating func expire(at now: Double) -> AddressedDecision? {
        guard let p = pending, now - p.sentAt > HandsFree.replyTimeout else { return nil }
        pending = nil
        return .ignore
    }
}

/// Listening pauses while Reach speaks and resumes 0.5 s after it stops (so it never hears itself).
public struct EchoGate: Equatable, Sendable {
    public private(set) var speaking = false
    public private(set) var stoppedAt: Double?
    public init() {}

    public mutating func speakingChanged(_ on: Bool, now: Double) {
        speaking = on
        stoppedAt = on ? nil : now
    }

    public func mayListen(now: Double) -> Bool {
        if speaking { return false }
        guard let s = stoppedAt else { return true }
        return now - s >= HandsFree.echoResume
    }
}

/// A held pinch from the hand worker counts as holding the talk key. Pointers can simply stop arriving, so
/// no pinch pointer for 0.6 s is a release.
public struct PinchSignal: Equatable, Sendable {
    public enum Action: Equatable, Sendable { case press, release, none }
    public static let staleAfter: Double = 0.6
    public private(set) var pinching = false
    private var lastPinch: Double = 0
    public init() {}

    public mutating func update(state: String?, now: Double) -> Action {
        if state == "pinch" {
            lastPinch = now
            if !pinching { pinching = true; return .press }
            return .none
        }
        if pinching { pinching = false; return .release }
        return .none
    }

    public mutating func tick(now: Double) -> Action {
        if pinching, now - lastPinch > PinchSignal.staleAfter { pinching = false; return .release }
        return .none
    }
}

/// On / off / paused. In memory only: hands-free always starts off at launch.
public struct HandsFreeToggle: Equatable, Sendable {
    public private(set) var on = false
    public private(set) var pausedUntil: Double?
    public init() {}

    @discardableResult public mutating func toggle() -> Bool { on.toggle(); pausedUntil = nil; return on }
    public mutating func set(_ v: Bool) { on = v; pausedUntil = nil }
    public mutating func pause(minutes: Double, now: Double) { pausedUntil = now + minutes * 60 }

    /// The mic should be open (on and not paused).
    public func active(now: Double) -> Bool {
        guard on else { return false }
        if let p = pausedUntil, now < p { return false }
        return true
    }
}
