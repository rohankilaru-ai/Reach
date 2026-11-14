import Foundation

/// Dictation mode: pure logic only (no audio, no Accessibility, no UI). The app feeds recognizer text in and
/// performs the insertions that come out. Nothing here talks to a model or the network.
public enum Dictation {
    public static let label = "Dictating"
    public static let hint = "Speak to type. Say \"stop dictating\" or press Option-Shift-D to stop."
    public static let noTextBox = "Click a text box first"
    public static let offFlash = "Dictation off"
    public static let silenceTimeout: Double = 60
    public static let restartEvery: Double = 50
    public static let pauseGap: Double = 1.0
    public static let maxSegment = 800
    public static let maxDeleteKeys = 200
    public static let pasteRestoreDelay: Double = 0.4

    /// Frontmost apps dictation never types into (bundle ids, lowercased). Any id containing "password" too.
    public static let denyBundles: Set<String> = [
        "com.apple.terminal", "com.googlecode.iterm2", "com.1password.1password", "com.agilebits.onepassword7",
        "com.agilebits.onepassword-osx", "com.apple.keychainaccess", "com.apple.systempreferences",
        "com.apple.systemsettings", "com.apple.settings",
    ]

    public static func isDenied(bundleID: String?) -> Bool {
        guard let b = bundleID?.lowercased(), !b.isEmpty else { return false }
        return denyBundles.contains(b) || b.contains("password")
    }
}

// MARK: commands

public enum DictationCommand: Equatable, Sendable {
    case deleteThat, newLine, stopDictating

    static func norm(_ text: String) -> String {
        let m = WakeWord.match(text)
        let t = m.stripped.lowercased().filter { $0.isLetter || $0 == " " }
        return t.split(separator: " ").joined(separator: " ")
    }

    /// Exact phrases only (case, punctuation and a leading "Reach," ignored). Anything else is text.
    public static func match(_ text: String) -> DictationCommand? {
        switch norm(text) {
        case "delete that": return .deleteThat
        case "new line": return .newLine
        case "stop dictating": return .stopDictating
        default: return nil
        }
    }

    /// The hands-free recognizer's local phrases: "Reach, start dictating" / "Reach, stop dictating".
    /// Returns true (start), false (stop), or nil (not a dictation phrase). The wake word is required here.
    public static func wakeToggle(_ text: String) -> Bool? {
        guard WakeWord.match(text).matched else { return nil }
        switch norm(text) {
        case "start dictating", "start dictation": return true
        case "stop dictating", "stop dictation": return false
        default: return nil
        }
    }
}

// MARK: formatting

public enum DictationFormat {
    /// Spoken punctuation, longest first. A value starting with "\n" is a newline.
    static let spoken: [(words: [String], mark: String)] = [
        (["new", "paragraph"], "\n\n"), (["new", "line"], "\n"),
        (["question", "mark"], "?"), (["exclamation", "mark"], "!"), (["exclamation", "point"], "!"),
        (["full", "stop"], "."), (["period"], "."), (["comma"], ","), (["colon"], ":"), (["semicolon"], ";"),
    ]

    /// Formats one finalized segment for insertion after `previous` (the character before the caret; nil when
    /// unknown or at the very start). Applies spoken punctuation, sentence capitals and the leading space.
    /// Capped at `Dictation.maxSegment` characters.
    public static func format(_ raw: String, previous: Character?) -> String {
        let words = raw.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        var out = ""
        var i = 0
        while i < words.count {
            var matched: String?
            for (ws, mark) in spoken where i + ws.count <= words.count {
                let slice = words[i..<(i + ws.count)].map { $0.lowercased().filter { $0.isLetter } }
                if slice == ws { matched = mark; i += ws.count; break }
            }
            if let mark = matched {
                while out.last == " " { out.removeLast() }
                out += mark
            } else {
                if let l = out.last, !l.isWhitespace { out += " " }
                out += words[i]
                i += 1
            }
        }
        // sentence capitals: after the previous text's sentence end (or start), and after . ? ! or a newline
        var sentenceStart = startsSentence(previous)
        var capped = ""
        for ch in out {
            if sentenceStart, ch.isLetter {
                capped += ch.uppercased(); sentenceStart = false
            } else {
                capped.append(ch)
                if ".?!\n".contains(ch) { sentenceStart = true } else if !ch.isWhitespace { sentenceStart = false }
            }
        }
        // the leading space: only when the previous char is not whitespace and the text starts with a word
        if let p = previous, !p.isWhitespace, let f = capped.first, !(".,?!:;\n".contains(f)) {
            capped = " " + capped
        }
        if capped.count > Dictation.maxSegment { capped = String(capped.prefix(Dictation.maxSegment)) }
        return capped
    }

    static func startsSentence(_ previous: Character?) -> Bool {
        guard let p = previous else { return true }
        return ".?!\n".contains(p)
    }

    /// Sentence start when the text before the caret ends with ". " / newline / nothing.
    public static func previousSignificant(_ before: String) -> Character? {
        guard let last = before.last else { return nil }
        if last == "\n" { return last }
        if last.isWhitespace {
            let trimmed = before.trimmingCharacters(in: .whitespaces)
            if let t = trimmed.last, ".?!\n".contains(t) { return "\n" }   // sentence end then space
            return trimmed.isEmpty ? nil : " "
        }
        return last
    }
}

// MARK: pause cutting

/// Turns the recognizer's running transcript into finalized segments: about 1 s without new words.
/// Partials are never inserted.
public struct DictationCutter: Sendable {
    public private(set) var current = ""
    private var lastChange: Double = 0

    public init() {}

    public mutating func feed(_ text: String, now: Double) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t != current { current = t; lastChange = now }
    }

    public mutating func tick(now: Double) -> String? {
        guard !current.isEmpty, now - lastChange >= Dictation.pauseGap else { return nil }
        defer { current = "" }
        return current
    }

    public mutating func reset() { current = "" }
}

// MARK: on / off and silence

public struct DictationToggle: Sendable {
    public enum Event: Equatable, Sendable { case none, timedOut }
    public private(set) var on = false
    private var lastSpeech: Double = 0

    public init() {}

    public mutating func set(_ v: Bool, now: Double) { on = v; lastSpeech = now }
    public mutating func flip(now: Double) { set(!on, now: now) }
    public mutating func heard(now: Double) { lastSpeech = now }

    /// Called on a timer: 60 s without speech turns dictation off.
    public mutating func tick(now: Double) -> Event {
        guard on, now - lastSpeech >= Dictation.silenceTimeout else { return .none }
        on = false
        return .timedOut
    }
}

// MARK: where the text would go

/// What Accessibility says about the focused element and the frontmost app (read only).
public struct FocusInfo: Equatable, Sendable {
    public var role: String?
    public var subrole: String?
    public var bundleID: String?
    public var pid: Int32
    public var hasValue: Bool
    public var hasSelectedRange: Bool
    public var editable: Bool
    public var isReach: Bool
    /// The frontmost app's bundle id (can differ from the focused element's app); both are deny-checked.
    public var frontBundleID: String?

    public init(role: String?, subrole: String? = nil, bundleID: String?, pid: Int32 = 0, hasValue: Bool = false,
                hasSelectedRange: Bool = false, editable: Bool = true, isReach: Bool = false, frontBundleID: String? = nil) {
        self.role = role; self.subrole = subrole; self.bundleID = bundleID; self.pid = pid; self.frontBundleID = frontBundleID
        self.hasValue = hasValue; self.hasSelectedRange = hasSelectedRange; self.editable = editable; self.isReach = isReach
    }
}

public enum TargetDecision: Equatable, Sendable {
    case type
    case noAccessibility
    case noTextBox
    case secure
    case deniedApp(String)
    case reachPanel

    public var line: String? {
        switch self {
        case .type: return nil
        case .noAccessibility: return "Dictation needs Accessibility"
        case .noTextBox: return Dictation.noTextBox
        case .secure: return "Not typing into a password field"
        case .deniedApp: return "Not typing into this app"
        case .reachPanel: return Dictation.noTextBox
        }
    }
}

public enum DictationTarget {
    static let textRoles: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox"]

    public static func decide(_ f: FocusInfo?, trusted: Bool) -> TargetDecision {
        guard trusted else { return .noAccessibility }
        guard let f else { return .noTextBox }
        if f.isReach { return .reachPanel }
        if f.role == "AXSecureTextField" || f.subrole == "AXSecureTextField" { return .secure }
        if Dictation.isDenied(bundleID: f.bundleID) { return .deniedApp(f.bundleID ?? "") }
        if Dictation.isDenied(bundleID: f.frontBundleID) { return .deniedApp(f.frontBundleID ?? "") }
        guard f.editable else { return .noTextBox }
        if let r = f.role, textRoles.contains(r) { return .type }
        if f.hasValue && f.hasSelectedRange { return .type }   // web text inputs and other custom fields
        return .noTextBox
    }
}

// MARK: inserting and "delete that"

/// Read-only Accessibility plus the AX insert/delete calls (the app's real one, or a fake in tests).
public protocol DictationFocus: AnyObject {
    var trusted: Bool { get }
    func focused() -> FocusInfo?
    /// The text before the caret (nil when the element does not expose it).
    func textBeforeCaret() -> String?
    /// Sets the selected text (inserts at the caret, replacing a selection). False when the app refused.
    func insertAX(_ text: String) -> Bool
    /// Selects the `count` characters before the caret and replaces them with nothing. False when refused.
    func deleteBeforeCaretAX(_ count: Int) -> Bool
}

public protocol DictationPasteboard: AnyObject {
    associatedtype Saved
    func save() -> Saved
    func put(_ text: String) -> Int        // returns the change count after writing
    func restore(_ saved: Saved, ifChangeCount: Int)
}

/// The only synthetic key events dictation sends: one Command-V, one Command-Z, and Delete (capped).
public protocol DictationKeys: AnyObject {
    func commandV()
    func commandZ()
    func delete(_ times: Int)
}

public enum InsertPath: Equatable, Sendable { case ax, paste }

public struct InsertLedger: Equatable, Sendable {
    public struct Entry: Equatable, Sendable {
        public var count: Int; public var path: InsertPath; public var text: String; public var pid: Int32
    }
    public private(set) var last: Entry?
    public private(set) var lastChar: Character?

    public init() {}

    public mutating func clear() { last = nil; lastChar = nil }

    public mutating func record(_ text: String, path: InsertPath, pid: Int32 = 0) {
        last = Entry(count: text.count, path: path, text: text, pid: pid)
        lastChar = text.last
    }

    public enum DeletePlan: Equatable, Sendable { case nothing, ax(Int), undo, keys(Int) }

    /// "delete that": one step back only. Paste path -> one Command-Z; AX path -> AX range (keys as fallback).
    public mutating func planDelete() -> DeletePlan {
        guard let e = last else { return .nothing }
        last = nil
        lastChar = nil
        switch e.path {
        case .paste: return .undo
        case .ax: return .ax(e.count)
        }
    }

    public static func keyCount(_ n: Int) -> Int { min(max(n, 0), Dictation.maxDeleteKeys) }
}

public enum InsertOutcome: Equatable, Sendable {
    case inserted(InsertPath, String)
    case refused(TargetDecision)
    case focusChanged
    case empty
}

/// Runs one finalized segment through the safety checks and the AX-then-paste insert.
public final class DictationInserter<P: DictationPasteboard> {
    public let focus: DictationFocus
    public let pasteboard: P
    public let keys: DictationKeys
    public private(set) var ledger = InsertLedger()
    /// Scheduler for the pasteboard restore (the app uses a 0.4 s main-queue delay; tests run it at once).
    public var later: (Double, @escaping () -> Void) -> Void

    public init(focus: DictationFocus, pasteboard: P, keys: DictationKeys,
                later: @escaping (Double, @escaping () -> Void) -> Void) {
        self.focus = focus; self.pasteboard = pasteboard; self.keys = keys; self.later = later
    }

    /// `pid` is the frontmost app when the segment began; a different app now means the user moved away.
    public func insert(_ raw: String, startedIn pid: Int32?) -> InsertOutcome {
        let f = focus.focused()
        let d = DictationTarget.decide(f, trusted: focus.trusted)
        guard d == .type, let f else { return .refused(d) }
        if let pid, pid != f.pid { return .focusChanged }
        let prev: Character? = focus.textBeforeCaret().map { DictationFormat.previousSignificant($0) } ?? ledger.lastChar
        let text = DictationFormat.format(raw, previous: prev)
        guard !text.isEmpty else { return .empty }
        if focus.insertAX(text) {
            ledger.record(text, path: .ax, pid: f.pid)
            return .inserted(.ax, text)
        }
        // last check right before the synthetic paste: never paste into an app the user just switched to
        guard focus.focused()?.pid == f.pid else { return .focusChanged }
        let saved = pasteboard.save()
        let change = pasteboard.put(text)
        keys.commandV()
        let pb = pasteboard
        later(Dictation.pasteRestoreDelay) { pb.restore(saved, ifChangeCount: change) }
        ledger.record(text, path: .paste, pid: f.pid)
        return .inserted(.paste, text)
    }

    /// A bare newline ("new line" said on its own).
    public func newLine(startedIn pid: Int32?) -> InsertOutcome { insert("new line", startedIn: pid) }

    /// "delete that": removes the last inserted segment, only in the same app and (when Accessibility shows the
    /// text before the caret) only when that text still ends with what was typed. Returns what was done.
    @discardableResult
    public func deleteThat() -> InsertLedger.DeletePlan {
        let f = focus.focused()
        guard DictationTarget.decide(f, trusted: focus.trusted) == .type, let f, let e = ledger.last else { return .nothing }
        guard f.pid == e.pid else { ledger.clear(); return .nothing }
        if e.path == .ax, let before = focus.textBeforeCaret(), !before.hasSuffix(e.text) { ledger.clear(); return .nothing }
        let plan = ledger.planDelete()
        switch plan {
        case .nothing, .keys: return plan
        case .undo: keys.commandZ(); return .undo
        case .ax(let n):
            if focus.deleteBeforeCaretAX(n) { return .ax(n) }
            let k = InsertLedger.keyCount(n)
            keys.delete(k)
            return .keys(k)
        }
    }
}
