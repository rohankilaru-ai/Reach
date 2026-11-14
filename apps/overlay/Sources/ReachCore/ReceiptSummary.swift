import Foundation

/// The answer card's friendly footer: one plain line built from the receipt, e.g.
/// "Checked by Drex · opened a Chrome tab · 5 s". Never tokens, costs, probabilities or internal mode names;
/// the raw lines live behind the menu bar's "Show technical details" developer toggle.
public enum ReceiptSummary {
    /// A tool name in plain words, or nil for tools that are not worth mentioning.
    public static func phrase(tool: String) -> String? {
        switch tool {
        case "chrome_open_url": return "opened a Chrome tab"
        case "evidence_search": return "searched your documents"
        case "browser_task": return "used the browser"
        case "note_append": return "saved a note"
        case "see_screen", "see_region": return "looked at your screen"
        case "point_at": return "pointed at the screen"
        case "email_draft": return "opened an email draft"
        default:
            if tool.hasPrefix("ax_") { return "used an app" }
            if tool.hasPrefix("finder_") { return "opened a folder" }
            return nil
        }
    }

    /// Whole seconds, at least 1 ("<1 s" never shows: the line is for people, not for timing).
    public static func seconds(_ ms: Int) -> String { "\(max(1, Int((Double(ms) / 1000).rounded()))) s" }

    public static func line(_ r: Receipt) -> String {
        var parts: [String] = []
        if r.system1_asks > 0 { parts.append("Checked by Drex") }
        var seen = Set<String>()
        for t in r.tools ?? [] {
            if let p = phrase(tool: t), seen.insert(p).inserted { parts.append(p) }
        }
        parts.append(seconds(r.duration_ms))
        return parts.joined(separator: " · ")
    }
}
