import CoreGraphics
import Foundation

// The hub's decisions as pure types: the hold-circle-speak gesture, receipt verdicts, spoken text, the zoom lens
// scale, hover hit-testing and the panel's height budget. The app only executes what these return.

// MARK: - one gesture: hold Option-Space, circle, speak, release

/// Hold Option-Space to talk; while holding, a freehand circle may be drawn. On release the transcript and the circle
/// resolve into exactly one outcome. The circle capture is asynchronous, so the release can come first.
public struct HubGesture: Equatable, Sendable {
    public enum Phase: String, Equatable, Sendable { case idle, holding, resolving }

    /// What one hold turned into.
    public enum Resolution: Equatable, Sendable {
        /// One ask: the transcript plus the circle (when one was drawn).
        case ask(text: String, selection: Selection?)
        /// Circle drawn, nothing (usable) said: open the typing box with the circle attached as a chip.
        case typeWithChip(Selection)
        /// The whole command was "zoom": open the lens locally, no ask.
        case zoom(Selection)
        /// Nothing to do; `why` is shown under the field.
        case nothing(why: String)
        case cancelled
    }

    enum CircleState: Equatable, Sendable { case none, capturing(Int), ready(Selection), failed }

    public private(set) var phase: Phase = .idle
    public private(set) var hold = 0
    var circle: CircleState = .none
    var verdict: VoiceGate.Verdict?

    public init() {}

    public var hasCircle: Bool { if case .none = circle { return false }; return true }

    /// The key has been held past the tap threshold: listening starts and the screen becomes ready to circle.
    public mutating func holdStarted() -> Int {
        hold += 1
        phase = .holding
        circle = .none
        verdict = nil
        return hold
    }

    /// A closed loop was drawn during the hold; its capture (`gen`) is now running. A newer loop replaces it.
    public mutating func circleDrawn(gen: Int) {
        guard phase != .idle else { return }
        circle = .capturing(gen)
    }

    /// The capture for `gen` finished (or failed with a region-only selection). Stale generations are ignored.
    public mutating func circleCaptured(gen: Int, _ sel: Selection) -> Resolution? {
        guard case .capturing(let g) = circle, g == gen else { return nil }
        circle = .ready(sel)
        return resolveIfReady()
    }

    /// Key up: stop listening and wait for the final transcript (and any capture still running).
    public mutating func released() {
        guard phase == .holding else { return }
        phase = .resolving
    }

    /// The final transcript was judged (or none arrived: pass `.reject`).
    public mutating func heard(_ v: VoiceGate.Verdict) -> Resolution? {
        guard phase != .idle else { return nil }
        verdict = v
        return resolveIfReady()
    }

    /// Esc: drop everything from this hold.
    public mutating func cancel() -> Resolution? {
        guard phase != .idle else { return nil }
        reset()
        return .cancelled
    }

    mutating func reset() { phase = .idle; circle = .none; verdict = nil }

    mutating func resolveIfReady() -> Resolution? {
        guard phase == .resolving, let verdict else { return nil }
        if case .capturing = circle { return nil }   // the release beat the capture: wait for it
        let sel: Selection? = { if case .ready(let s) = circle { return s }; return nil }()
        reset()
        switch verdict {
        case .send(let text):
            if ZoomCommand.isZoom(text) {
                if let sel { return .zoom(sel) }
                return .nothing(why: "circle something first, then say zoom")
            }
            return .ask(text: text, selection: sel)
        case .reject(let why):
            if let sel { return .typeWithChip(sel) }
            return .nothing(why: why)
        }
    }
}

public enum ZoomCommand {
    /// True when the whole command is the word "zoom" (any case, trailing punctuation, "zoom in").
    public static func isZoom(_ text: String) -> Bool {
        let t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
        return t == "zoom" || t == "zoom in"
    }
}

// MARK: - receipt verdict

public enum Verdict: Equatable, Sendable {
    case done
    case needsYou(String)
    case failed(String)
    case unverified(String)
    case stopped(String)

    public var title: String {
        switch self {
        case .done: return "Done"
        case .needsYou(let s): return "Needs you: \(s)"
        case .failed(let s): return "Failed: \(s)"
        case .unverified(let s): return "Unverified: \(s)"
        case .stopped: return "Stopped"
        }
    }

    public var phase: Phase {
        switch self {
        case .done: return .done
        case .needsYou: return .needsYou
        case .failed, .unverified: return .failed
        case .stopped: return .idle
        }
    }

    /// Reads `status`, `reason`, `write_outcome` and `outcome`; a write or browser task that did not complete is
    /// never shown as done, whatever the status says.
    public static func of(_ r: Receipt) -> Verdict {
        if r.status == "aborted" { return .stopped(r.reason) }
        if r.reason == "needs_selection" { return .needsYou("Circle the thing you mean") }
        switch r.write_outcome {
        case "failed": return .failed("the note write failed")
        case "unverified": return .unverified("the note write could not be read back")
        default: break
        }
        if let o = r.outcome, o != "done" { return .failed(o == "not_done" ? "the browser task did not finish" : o) }
        if r.status == "ok" { return .done }
        return .failed(r.reason.isEmpty ? "unknown error" : r.reason)
    }
}

// MARK: - spoken replies

public enum SpokenText {
    public static let limit = 400

    /// The answer as it should be spoken: no citation brackets, markdown, links or code fences; at most `limit`
    /// characters, cut at a sentence (or word) boundary.
    public static func clean(_ answer: String, limit: Int = limit) -> String {
        var s = answer
        s = s.replacingOccurrences(of: "```[\\s\\S]*?```", with: " ", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\[([^\\]]+)\\]\\([^)]*\\)", with: "$1", options: .regularExpression)  // [t](u)
        s = s.replacingOccurrences(of: "\\[(?:[A-Z]?\\d+(?:[,;\\s]+[A-Z]?\\d+)*|source[^\\]]*|p\\.? ?\\d+)\\]",
                                   with: "", options: [.regularExpression, .caseInsensitive])                  // [E3] [1, 2]
        s = s.replacingOccurrences(of: "(?m)^\\s{0,3}(#{1,6}|[-*+]|\\d+\\.|>)\\s+", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "[*_`~]+", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        s = s.replacingOccurrences(of: " ([.,;:!?])", with: "$1", options: .regularExpression)
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard s.count > limit else { return s }
        let chars = Array(s.prefix(limit + 1))
        var cut: Int?
        for i in stride(from: min(limit, chars.count) - 1, through: limit / 3, by: -1)
        where ".!?".contains(chars[i]) && (i + 1 == chars.count || chars[i + 1] == " ") {
            cut = i + 1; break
        }
        if let cut { return String(chars[..<cut]) }
        let head = String(s.prefix(limit))
        if let sp = head.lastIndex(of: " ") { return String(head[..<sp]) + "…" }
        return head
    }

    /// Voice asks are spoken automatically when the toggle is on; typed asks only on request.
    public static func autoSpeak(fromVoice: Bool, toggle: Bool) -> Bool { fromVoice && toggle }
}

// MARK: - zoom lens

public enum ZoomLens {
    public static let minScale: Double = 2
    public static let maxScale: Double = 4

    public static func clamp(_ s: Double) -> Double {
        guard s.isFinite else { return minScale }
        return min(max(s, minScale), maxScale)
    }

    /// Scroll wheel: up zooms in (0.02 per point of delta). Pinch: multiply by (1 + magnification).
    public static func scrolled(_ s: Double, deltaY: Double) -> Double { clamp(s + deltaY * 0.02) }
    public static func pinched(_ s: Double, magnification: Double) -> Double { clamp(s * (1 + magnification)) }

    /// The lens window: the magnified image, no larger than `maxSide` on either side.
    public static func lensSize(image: CGSize, scale: Double, maxSide: CGFloat = 560) -> CGSize {
        let z = CGFloat(clamp(scale))
        return CGSize(width: min(max(image.width * z, 160), maxSide), height: min(max(image.height * z, 120), maxSide))
    }
}

// MARK: - hover: move up to the top-centre to expand

public enum Hover {
    /// The hot zone: the strip above the pill up to the top of the screen (the menu bar / notch), centred on the
    /// anchor, at least `minWidth` wide. AppKit coordinates.
    public static func hotZone(anchor: CGPoint, pill: CGSize, screenTop: CGFloat, minWidth: CGFloat = 220) -> CGRect {
        let w = max(pill.width + 40, minWidth)
        let bottom = anchor.y - pill.height - 4
        return CGRect(x: anchor.x - w / 2, y: bottom, width: w, height: max(screenTop - bottom, pill.height))
    }

    /// The expanded panel keeps open while the mouse is inside it or within `margin` of it.
    public static func keepZone(panel: CGRect, margin: CGFloat = 24) -> CGRect { panel.insetBy(dx: -margin, dy: -margin) }

    public enum Action: Equatable, Sendable { case none, expand, collapse }

    /// Hover state over time: dwell 0.12 s in the hot zone to expand; leave the panel for 0.6 s to collapse, but
    /// only a panel the hover opened, and never while busy.
    public struct Tracker: Equatable, Sendable {
        public static let dwell = 0.12
        public static let grace = 0.6
        public private(set) var openedByHover = false
        /// After a collapse the mouse must leave the hot zone once before hover can open the panel again.
        public private(set) var armed = true
        var enteredAt: Double?
        var leftAt: Double?

        public init() {}

        /// The panel was opened another way (key, voice, answer): hover must not close it.
        public mutating func openedElsewhere() { openedByHover = false; enteredAt = nil; leftAt = nil }
        public mutating func collapsed() { openedByHover = false; enteredAt = nil; leftAt = nil; armed = false }

        public mutating func update(mouse: CGPoint, now: Double, expanded: Bool, hot: CGRect, panel: CGRect,
                                    busy: Bool) -> Action {
            if !expanded {
                guard hot.contains(mouse) else { enteredAt = nil; armed = true; return .none }
                guard armed else { return .none }
                let t = enteredAt ?? now
                enteredAt = t
                if now - t >= Self.dwell - 1e-9 { enteredAt = nil; openedByHover = true; leftAt = nil; return .expand }
                return .none
            }
            enteredAt = nil
            guard openedByHover else { return .none }
            if busy || Hover.keepZone(panel: panel).contains(mouse) || hot.contains(mouse) { leftAt = nil; return .none }
            let t = leftAt ?? now
            leftAt = t
            if now - t >= Self.grace - 1e-9 { openedByHover = false; leftAt = nil; return .collapse }
            return .none
        }
    }
}

// MARK: - layout budget

public enum PanelBudget {
    /// Height the answer may take: about 60% of the visible screen, and never more than what is left below the
    /// anchor after the rest of the panel (`chrome`). At least 60 pt so a line always shows.
    public static func answerMax(visibleHeight: CGFloat, spaceBelowAnchor: CGFloat, chrome: CGFloat) -> CGFloat {
        let cap = visibleHeight * 0.6
        return max(60, min(cap, spaceBelowAnchor - chrome - 8))
    }
}

// MARK: - status dot

public enum HubStatus: String, Equatable, Sendable {
    case idle, listening, working, done, failed, needsYou = "needs you"

    public static func of(phase: Phase, listening: Bool) -> HubStatus {
        if listening { return .listening }
        switch phase {
        case .idle: return .idle
        case .working: return .working
        case .done: return .done
        case .failed: return .failed
        case .needsYou: return .needsYou
        }
    }

    public var pulses: Bool { self == .listening || self == .working }
}

// MARK: - calm panel: plain text, one-line progress, one sentence + one action

public enum PlainText {
    /// The answer for display: markdown stripped (bold, headings, links, code ticks, citation brackets, leading
    /// bullets), numbered steps kept, blank-line runs collapsed.
    public static func display(_ answer: String) -> String {
        var s = answer
        s = s.replacingOccurrences(of: "```[a-zA-Z]*\\n?", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\[([^\\]]+)\\]\\([^)]*\\)", with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\s?\\[(?:[A-Z]?\\d+(?:[,;\\s]+[A-Z]?\\d+)*|p\\.? ?\\d+)\\]", with: "",
                                   options: [.regularExpression, .caseInsensitive])
        s = s.replacingOccurrences(of: "(?m)^[ \\t]{0,3}(#{1,6}|>)[ \\t]+", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?m)^[ \\t]*[-*+•][ \\t]+", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\*\\*|__|(?<![\\w*])\\*(?=\\S)|(?<=\\S)\\*(?![\\w*])|`", with: "", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\n{3,}", with: "\n\n", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

public enum ProgressLine {
    /// One short line of progress from the running tool's name; never a log.
    public static func of(tool: String?) -> String {
        let t = (tool ?? "").lowercased()
        if t.isEmpty { return "Thinking…" }
        if t.contains("browser") { return "Using the browser…" }
        if t.contains("region") || t.contains("screen") || t.contains("capture") || t.contains("see") {
            return "Looking at your screen…"
        }
        if t.contains("note") || t.contains("write") || t.contains("save") { return "Writing your note…" }
        if t.contains("search") || t.contains("doc") || t.contains("ground") || t.contains("pdf") || t.contains("retriev")
            || t.contains("passage") { return "Searching your documents…" }
        if t.contains("web") { return "Searching the web…" }
        if t.contains("point") || t.contains("guide") { return "Showing you where…" }
        return "Working on it…"
    }
}

/// What an end state says: one plain sentence and at most one action.
public struct PlainState: Equatable, Sendable {
    public enum Action: String, Equatable, Sendable { case none, retry, circle, openScreenSettings, openMicSettings, startServer }
    public var sentence: String
    public var action: Action

    public init(sentence: String, action: Action) { self.sentence = sentence; self.action = action }

    public var actionLabel: String? {
        switch action {
        case .none: return nil
        case .retry: return "Try again"
        case .circle: return "Hold Option-Space and circle it"
        case .openScreenSettings, .openMicSettings: return "Open Settings"
        case .startServer: return "Retry"
        }
    }

    public static func of(verdict: Verdict) -> PlainState? {
        switch verdict {
        case .done, .stopped: return nil
        case .needsYou: return PlainState(sentence: "Circle the thing you mean.", action: .circle)
        case .unverified: return PlainState(sentence: "I couldn't confirm the note was saved.", action: .retry)
        case .failed(let why):
            if why.contains("note write") { return PlainState(sentence: "The note wasn't saved.", action: .retry) }
            if why.contains("browser") { return PlainState(sentence: "The browser task didn't finish.", action: .retry) }
            return PlainState(sentence: "That didn't work.", action: .retry)
        }
    }

    /// Needs-you notes raised locally (permissions, no server).
    public static func of(note: String) -> PlainState {
        let n = note.lowercased()
        if n.contains("screen recording") { return PlainState(sentence: "Reach needs Screen Recording to see what you circle.", action: .openScreenSettings) }
        if n.contains("microphone") || n.contains("speech") { return PlainState(sentence: "Reach needs the microphone to hear you.", action: .openMicSettings) }
        if n.contains("reach serve") || n.contains("connection") || n.contains("server") || n.contains("token") {
            return PlainState(sentence: "Reach's helper isn't running.", action: .startServer)
        }
        if n.contains("circle") { return PlainState(sentence: "Circle the thing you mean.", action: .circle) }
        return PlainState(sentence: "Something needs you.", action: .none)
    }
}

public enum PointerLabel {
    public static let maxLength = 30
    /// Guide pointer labels stay short: at most 30 characters, cut at a word with an ellipsis.
    public static func short(_ s: String?) -> String? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        guard t.count > maxLength else { return t }
        let head = String(t.prefix(maxLength - 1))
        if let sp = head.lastIndex(of: " "), head.distance(from: head.startIndex, to: sp) > 10 { return String(head[..<sp]) + "…" }
        return head + "…"
    }
}

public enum LensPlacement {
    /// Where the lens goes: never over the panel or the circled area, inside the visible frame. Tries below the
    /// circle, above it, then to its right and left; falls back to the corner farthest from both.
    public static func place(size: CGSize, avoid: [CGRect], near target: CGRect, visible: CGRect, gap: CGFloat = 16) -> CGPoint {
        let cands = [
            CGPoint(x: target.midX - size.width / 2, y: target.minY - gap - size.height),
            CGPoint(x: target.midX - size.width / 2, y: target.maxY + gap),
            CGPoint(x: target.maxX + gap, y: target.midY - size.height / 2),
            CGPoint(x: target.minX - gap - size.width, y: target.midY - size.height / 2),
        ]
        for c in cands {
            let o = Placement.clamp(c, size: size, in: visible)
            let r = CGRect(origin: o, size: size)
            if !avoid.contains(where: { $0.intersects(r) }) { return o }
        }
        let corners = [CGPoint(x: visible.minX, y: visible.minY), CGPoint(x: visible.maxX - size.width, y: visible.minY)]
        return corners.max { a, b in
            avoid.map { hypot($0.midX - a.x, $0.midY - a.y) }.min() ?? 0 < avoid.map { hypot($0.midX - b.x, $0.midY - b.y) }.min() ?? 0
        } ?? corners[0]
    }
}
