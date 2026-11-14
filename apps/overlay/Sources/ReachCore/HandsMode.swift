import Foundation

/// Hands mode (your hand moves the real mouse) as seen by the overlay: pure logic only.
/// The backend owns the process and broadcasts `hands_state` with the TRUE state on start, stop and on its own exit
/// (open palm, crash, camera failure). The menu checkmark and the pill follow that, never the last request.
public enum HandsMode {
    public static let hint = "Point to move, pinch to click, open palm to stop"
    public static let onLabel = "Hands on"
    public static let offLabel = "Hands off"
    public static let needsAccessibility = "Hands mode needs you: allow Accessibility in Set up Reach…"
    public static let hintSeconds: Double = 3
    public static let offSeconds: Double = 2

    /// The pill's look for a live phase: glyph tint and a short label.
    public enum Tint: Equatable, Sendable { case grey, blue, green, amber }

    public static func look(phase: String?) -> (tint: Tint, label: String, pulse: Bool) {
        switch phase {
        case "pointing": return (.blue, "Pointing", false)
        case "pinching": return (.green, "Click", true)
        case "dragging": return (.green, "Dragging", true)
        case "paused": return (.amber, "Paused", false)
        case "leaving": return (.amber, "Leaving…", false)
        case "no_hand": return (.grey, "No hand", false)
        default: return (.grey, onLabel, false)
        }
    }

    /// A hand signal (`hands_gesture`) -> what the overlay does, given whether dictation / a voice ask is on now.
    public enum Signal: Equatable, Sendable {
        case toggleDictation
        case startListening
        case stopListening
    }

    public static func signal(_ gesture: String, dictationOn: Bool, listening: Bool) -> Signal? {
        switch gesture {
        case "thumbs_up": return .toggleDictation
        case "peace": return listening ? .stopListening : .startListening
        default: return nil
        }
    }

    /// The 1 s pill hint for a signal: glyph + words.
    public static let signalHintSeconds: Double = 1

    public static func signalHint(_ s: Signal, dictationOn: Bool) -> String {
        switch s {
        case .toggleDictation: return dictationOn ? "👍 Dictation off" : "👍 Dictation on"
        case .startListening: return "✌️ Listening"
        case .stopListening: return "✌️ Sent"
        }
    }

    /// What a toggle press (hotkey, menu item or spoken phrase) does.
    public enum Request: Equatable, Sendable {
        case send(on: Bool)
        case needsAccessibility
    }

    /// Toggle against the server's truth. Turning on requires Accessibility; turning off never does.
    public static func toggle(currentOn: Bool, accessibility: Bool) -> Request {
        if currentOn { return .send(on: false) }
        return accessibility ? .send(on: true) : .needsAccessibility
    }

    /// "Reach, hands mode on" / "Reach, hands mode off" -> true / false; anything else -> nil.
    public static func phrase(_ text: String) -> Bool? {
        let t = text.lowercased().filter { $0.isLetter || $0 == " " }.split(separator: " ").joined(separator: " ")
        // the wake word may already be stripped ("hands mode on"); either form is a local switch
        let body = t.hasPrefix("reach ") ? String(t.dropFirst(6)) : t
        guard body.hasPrefix("hands mode") || body.hasPrefix("hand mode") || body.hasPrefix("turn hands mode")
        else { return nil }
        if t.hasSuffix(" on") { return true }
        if t.hasSuffix(" off") { return false }
        return nil
    }
}

/// The overlay's copy of the server truth, plus the short-lived pill lines it causes.
public struct HandsModeState: Equatable, Sendable {
    public private(set) var on = false
    public private(set) var pid: Int?
    public private(set) var hintShown = false
    public private(set) var phase: String?

    public enum Effect: Equatable, Sendable {
        case none
        /// first time on in this run: show `HandsMode.hint` for 3 s
        case showHint
        /// turned off (open palm or exit): show "Hands off" for 2 s
        case showOff(reason: String?)
    }

    public init() {}

    /// Applies one `hands_state`. Repeats of the same state are no-ops (the server may answer a no-op toggle).
    public mutating func apply(on newOn: Bool, pid newPid: Int?, reason: String?, phase newPhase: String? = nil) -> Effect {
        let was = on
        on = newOn
        pid = newOn ? newPid : nil
        if !newOn { phase = nil } else if let newPhase { phase = newPhase }
        if newOn, !was {
            if hintShown { return .none }
            hintShown = true
            return .showHint
        }
        if !newOn, was { return .showOff(reason: reason) }
        return .none
    }
}
