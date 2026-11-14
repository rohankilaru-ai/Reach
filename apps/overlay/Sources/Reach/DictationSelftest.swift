import Foundation
import ReachCore

/// `--dictation-selftest`: dictation logic with a fake focused field, fake pasteboard and fake keys.
/// No microphone, no Accessibility calls, no real typing, no network.
private final class SelftestFocus: DictationFocus {
    var trusted = true
    var info: FocusInfo? = FocusInfo(role: "AXTextArea", bundleID: "com.example.notes", pid: 1)
    var text = ""
    var axWorks = true
    func focused() -> FocusInfo? { info }
    func textBeforeCaret() -> String? { axWorks ? text : nil }
    func insertAX(_ t: String) -> Bool { guard axWorks else { return false }; text += t; return true }
    func deleteBeforeCaretAX(_ n: Int) -> Bool { guard axWorks else { return false }; text.removeLast(min(n, text.count)); return true }
}

private final class SelftestPasteboard: DictationPasteboard {
    var contents = "saved clipboard"
    var changes = 0
    func save() -> String { contents }
    func put(_ t: String) -> Int { contents = t; changes += 1; return changes }
    func restore(_ s: String, ifChangeCount c: Int) { if c == changes { contents = s } }
}

private final class SelftestKeys: DictationKeys {
    var sent: [String] = []
    func commandV() { sent.append("cmd-v") }
    func commandZ() { sent.append("cmd-z") }
    func delete(_ n: Int) { sent.append("delete x\(n)") }
}

@MainActor func runDictationSelftest() -> Int32 {
    var ok = true
    func check(_ name: String, _ pass: Bool, _ detail: String = "") {
        log("dictation selftest \(pass ? "PASS" : "FAIL") \(name)\(detail.isEmpty ? "" : ": \(detail)")")
        ok = ok && pass
    }
    let f = SelftestFocus(), p = SelftestPasteboard(), k = SelftestKeys()
    let ins = DictationInserter(focus: f, pasteboard: p, keys: k, later: { _, run in run() })

    let s1 = DictationFormat.format("hello world period how are you question mark", previous: nil)
    check("spoken punctuation + capitals", s1 == "Hello world. How are you?", s1)
    check("commands exact", DictationCommand.match("Delete that.") == .deleteThat
          && DictationCommand.match("delete that file") == nil && DictationCommand.match("stop dictating") == .stopDictating)
    check("wake phrases", DictationCommand.wakeToggle("Reach, start dictating") == true
          && DictationCommand.wakeToggle("Reach, stop dictating") == false)

    let o1 = ins.insert("hello from reach", startedIn: 1)
    _ = ins.insert("and more", startedIn: 1)
    check("AX insert with spacing", o1 == .inserted(.ax, "Hello from reach") && f.text == "Hello from reach and more", f.text)
    check("delete that (AX)", ins.deleteThat() == .ax(9) && f.text == "Hello from reach", f.text)

    f.axWorks = false
    let o2 = ins.insert("pasted text", startedIn: 1)
    check("paste fallback + clipboard restored", o2 == .inserted(.paste, "Pasted text") && k.sent == ["cmd-v"]
          && p.contents == "saved clipboard", "\(k.sent) \(p.contents)")
    check("delete that after paste = one cmd-z", ins.deleteThat() == .undo && k.sent == ["cmd-v", "cmd-z"])
    _ = ins.insert("other", startedIn: 1)
    f.info = FocusInfo(role: "AXTextArea", bundleID: "com.example.mail", pid: 5)
    check("delete that refused after focus moved", ins.deleteThat() == .nothing && k.sent == ["cmd-v", "cmd-z", "cmd-v"], "\(k.sent)")
    f.info = FocusInfo(role: "AXTextArea", bundleID: "com.example.notes", pid: 1)

    let before = k.sent
    f.axWorks = true
    f.info = FocusInfo(role: "AXSecureTextField", bundleID: "com.example.browser", pid: 1)
    check("secure field refused", ins.insert("secret", startedIn: 1) == .refused(.secure))
    f.info = FocusInfo(role: "AXTextArea", bundleID: "com.apple.Terminal", pid: 1)
    check("deny-listed app refused", ins.insert("ls", startedIn: 1) == .refused(.deniedApp("com.apple.Terminal")))
    f.info = FocusInfo(role: "AXButton", bundleID: "com.example.notes", pid: 1)
    check("no text box", ins.insert("hi", startedIn: 1) == .refused(.noTextBox) && TargetDecision.noTextBox.line == Dictation.noTextBox)
    f.info = FocusInfo(role: "AXTextField", bundleID: "com.example.notes", pid: 1, isReach: true)
    check("never types into Reach", ins.insert("hi", startedIn: 1) == .refused(.reachPanel))
    f.info = FocusInfo(role: "AXTextField", bundleID: "com.example.notes", pid: 2)
    check("focus changed -> stop", ins.insert("hi", startedIn: 1) == .focusChanged)
    f.trusted = false
    check("no Accessibility -> needs you", ins.insert("hi", startedIn: 2) == .refused(.noAccessibility))
    check("no keys sent while refused", k.sent == before, "\(k.sent)")

    var t = DictationToggle()
    t.flip(now: 0)
    let early = t.tick(now: 59)
    check("60 s silence -> off", t.on == false ? false : (early == .none && t.tick(now: 60.1) == .timedOut && !t.on))
    log("dictation selftest \(ok ? "PASS" : "FAIL")")
    return ok ? 0 : 1
}
