import Foundation

/// Decides whether a finished transcript is worth sending as an ask.
public enum VoiceGate {
    public static let minConfidence: Float = 0.4

    public enum Verdict: Equatable, Sendable {
        case send(String)
        case reject(String)   // why, shown in the panel
    }

    /// `confidences` are the per-segment values of the final transcription. An empty list or one where every
    /// value is exactly 0 means the recognizer did not report confidence; that is treated as "unreported" and
    /// non-empty text passes (logged by the caller), rather than rejecting every utterance.
    public static func judge(text: String, confidences: [Float]) -> Verdict {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return .reject("didn't catch that") }
        guard let avg = average(confidences) else { return .send(t) }
        return avg >= minConfidence ? .send(t) : .reject("didn't catch that")
    }

    /// Mean confidence, or nil when unreported (no segments, or all exactly 0).
    public static func average(_ c: [Float]) -> Float? {
        guard !c.isEmpty, c.contains(where: { $0 != 0 }) else { return nil }
        return c.reduce(0, +) / Float(c.count)
    }
}

/// Hold-to-talk: key down starts a session, key up finishes it, Esc cancels. No audio, no UI.
public struct PushToTalk: Equatable, Sendable {
    public enum Phase: Equatable, Sendable { case idle, listening, finishing }
    public enum Action: Equatable, Sendable {
        case start(session: Int)
        case finish(session: Int)
        case cancel(session: Int)
        case none
    }

    public private(set) var phase: Phase = .idle
    public private(set) var session = 0

    public init() {}

    /// Key down. Auto-repeat or a press while finishing does nothing.
    public mutating func press() -> Action {
        guard phase == .idle else { return .none }
        session += 1
        phase = .listening
        return .start(session: session)
    }

    /// Key up. Only ends a session that is listening.
    public mutating func release() -> Action {
        guard phase == .listening else { return .none }
        phase = .finishing
        return .finish(session: session)
    }

    public mutating func cancel() -> Action {
        guard phase != .idle else { return .none }
        phase = .idle
        return .cancel(session: session)
    }

    /// The final transcript for `id` arrived. Returns true when it should be used; false for a stale or
    /// cancelled session (a cancelled recognition task can still call back).
    public mutating func finished(session id: Int) -> Bool {
        guard id == session, phase == .finishing else { return false }
        phase = .idle
        return true
    }

    /// A partial for `id` should be shown only while that session is listening.
    public func accepts(partialFrom id: Int) -> Bool { id == session && phase == .listening }
}
