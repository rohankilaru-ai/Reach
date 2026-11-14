import CoreGraphics

/// When the expanded answer panel folds away. Pure clocks and one decision, so the rules are tested without windows.
///
/// - Folds 8 s after the latest of: the panel opening / the answer landing, speech ending, a real interaction.
/// - A real interaction happens INSIDE the panel: the mouse moving over it, a scroll, a click, a key typed in its
///   field. Keyboard focus, a cursor parked anywhere (even in the margin around the panel), and an unused Speak
///   button are not interactions.
/// - Hard maximum: 20 s with no interaction folds no matter what (speech, a draft, a stale flag), except while an
///   ask runs or the user typed within the last 5 s.
public struct FoldClock: Equatable, Sendable {
    public static let idleAfter: Double = 8
    public static let hardMax: Double = 20
    public static let typingGrace: Double = 5

    public enum Verdict: String, Equatable, Sendable {
        case stay
        case idle = "idle"
        case hardMax = "no interaction for 20 s"
    }

    /// Last real interaction inside the panel (or when it opened / the answer landed).
    public private(set) var interacted: Double
    public private(set) var speechEnded: Double?
    public private(set) var typed: Double?

    public init(now: Double = 0) { interacted = now }

    /// The panel opened or an answer landed: the clocks start over.
    public mutating func opened(now: Double) { interacted = now; speechEnded = nil; typed = nil }
    public mutating func interaction(now: Double) { interacted = max(interacted, now) }
    public mutating func typedKey(now: Double) { interaction(now: now); typed = now }
    public mutating func speechFinished(now: Double) { speechEnded = now }

    /// `running`: an ask is running, or a hold / capture / listening is in progress.
    /// `speaking`: speech is still playing. `hasDraft`: unsent text in the field (kept until the hard maximum).
    public func decide(now: Double, expanded: Bool, running: Bool, speaking: Bool, hasDraft: Bool = false) -> Verdict {
        guard expanded else { return .stay }
        if running { return .stay }
        if let t = typed, now - t < Self.typingGrace { return .stay }
        if now - interacted >= Self.hardMax - 1e-9 { return .hardMax }
        if speaking || hasDraft { return .stay }
        let since = max(interacted, speechEnded ?? -Double.infinity)
        return now - since >= Self.idleAfter - 1e-9 ? .idle : .stay
    }

    /// Whether the mouse counts as interacting: inside the panel's exact frame (no margin) and moved since the last
    /// sample. A parked cursor, or one resting just outside, never keeps the panel open.
    public static func hoverCounts(mouse: CGPoint, last: CGPoint?, panel: CGRect, minMove: CGFloat = 1) -> Bool {
        guard panel.contains(mouse), let last else { return false }
        return hypot(mouse.x - last.x, mouse.y - last.y) >= minMove
    }
}
