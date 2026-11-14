import Foundation

/// What a saved hand gesture does (EXPERIMENTAL). Mirrors the `action` object in `.reach/gestures.json`.
public struct GestureAction: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, CaseIterable, Sendable { case top, bottom, keys, command, ask }
    public var kind: Kind
    public var value: String

    public init(kind: Kind, value: String = "") { self.kind = kind; self.value = value }

    /// One short line for the gesture list: "go to top", "press cmd+shift+t", "say scroll down", "ask: …".
    public var summary: String {
        switch kind {
        case .top: return "go to top"
        case .bottom: return "go to bottom"
        case .keys: return "press \(value)"
        case .command: return "command \(value.replacingOccurrences(of: "_", with: " "))"
        case .ask: return "ask: \(value)"
        }
    }

    /// top / bottom need no value; the others need a non-blank one.
    public var needsValue: Bool { kind != .top && kind != .bottom }
}

/// One saved gesture from `.reach/gestures.json` (extra fields in the file are ignored).
public struct SavedGesture: Codable, Equatable, Sendable {
    public var name: String
    public var action: GestureAction
    public var hands: Int?

    public var line: String {
        let h = (hands ?? 1) == 2 ? "2 hands" : "1 hand"
        return "\(name) · \(action.summary) · \(h)"
    }
}

public enum HandGestures {
    public static let maxName = 40
    public static let helper = "Perform the gesture twice when asked (about 10 frames each). Two-hand gestures: keep both hands in view."
    /// Router commands a gesture may run (everything but "uncertain"), sorted for the picker.
    public static var commands: [String] { HandsFree.commands.subtracting(["uncertain"]).sorted() }

    /// Reads `{"gestures":[...]}`; a missing or broken file is an empty list.
    public static func load(_ data: Data?) -> [SavedGesture] {
        struct File: Decodable { var gestures: [SavedGesture]? }
        guard let data, let f = try? JSONDecoder().decode(File.self, from: data) else { return [] }
        return f.gestures ?? []
    }

    /// The record form is valid: name 1...40 characters (trimmed), and a value where the action needs one.
    public static func canRecord(name: String, action: GestureAction) -> Bool {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty, n.count <= maxName else { return false }
        return !action.needsValue || !action.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// A relayed `command` gesture as the hands-free router decision for that command (same handling as voice).
    public static func decision(command: String) -> AddressedDecision {
        let words = command.replacingOccurrences(of: "_", with: " ")
        return AddressedDecision.decide(reply: AddressedReply(text: words, addressed: true, command: command),
                                        ask: words, timedOut: false)
    }
}

/// "hands on" / "hands off" / "hands" typed or said as the whole utterance (handled locally, never sent).
public enum HandsCommand: Equatable, Sendable {
    case on, off, toggle

    public static func parse(_ text: String) -> HandsCommand? {
        let words = text.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
        switch words {
        case ["hands"], ["hands", "mode"], ["hand", "mode"]: return .toggle
        case ["hands", "on"], ["hands", "mode", "on"], ["hand", "mode", "on"]: return .on
        case ["hands", "off"], ["hands", "mode", "off"], ["hand", "mode", "off"]: return .off
        default: return nil
        }
    }
}
