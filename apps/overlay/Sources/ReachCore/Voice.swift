import Foundation

/// Decides whether a finished transcript is worth sending as an ask.
public enum VoiceGate {
    public static let minConfidence: Float = 0.4

    public enum Verdict: Equatable, Sendable {
        case send(String)
        case reject(String)   // why, shown in the panel
    }

    /// `confidences` are the per-segment values of the FINAL transcription (partials report 0). Empty text,
    /// no segments, or a mean below 0.4 is rejected.
    public static func judge(text: String, confidences: [Float]) -> Verdict {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, let avg = average(confidences), avg >= minConfidence else { return .reject("didn't catch that") }
        return .send(t)
    }

    /// Mean segment confidence, nil when there are no segments.
    public static func average(_ c: [Float]) -> Float? {
        c.isEmpty ? nil : c.reduce(0, +) / Float(c.count)
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
