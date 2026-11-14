import Testing
@testable import ReachCore

final class FakeFocus: DictationFocus {
    var trusted = true
    var info: FocusInfo? = FocusInfo(role: "AXTextArea", bundleID: "com.apple.TextEdit", pid: 42)
    var text = ""
    var axWorks = true
    var axDeleteWorks = true
    var pidAfterFirstLook: Int32?
    private var looks = 0

    func focused() -> FocusInfo? {
        looks += 1
        if looks > 1, let p = pidAfterFirstLook { info?.pid = p }
        return info
    }
    func textBeforeCaret() -> String? { axWorks ? text : nil }
    func insertAX(_ t: String) -> Bool { guard axWorks else { return false }; text += t; return true }
    func deleteBeforeCaretAX(_ n: Int) -> Bool { guard axDeleteWorks else { return false }; text.removeLast(min(n, text.count)); return true }
}

final class FakePasteboard: DictationPasteboard {
    var contents = "owner's clipboard"
    var changes = 0
    func save() -> String { contents }
    func put(_ t: String) -> Int { contents = t; changes += 1; return changes }
    func restore(_ s: String, ifChangeCount c: Int) { if c == changes { contents = s; changes += 1 } }
}

final class FakeKeys: DictationKeys {
    var log: [String] = []
    func commandV() { log.append("cmd-v") }
    func commandZ() { log.append("cmd-z") }
    func delete(_ n: Int) { log.append("delete x\(n)") }
}

func makeInserter(_ f: FakeFocus, _ p: FakePasteboard, _ k: FakeKeys) -> DictationInserter<FakePasteboard> {
    DictationInserter(focus: f, pasteboard: p, keys: k, later: { _, run in run() })
}

@Suite struct DictationTests {
    @Test func spokenPunctuationAndCapitals() {
        #expect(DictationFormat.format("hello world period how are you question mark", previous: nil)
                == "Hello world. How are you?")
        #expect(DictationFormat.format("one comma two new line three", previous: nil) == "One, two\nThree")
        #expect(DictationFormat.format("new paragraph next", previous: ".") == "\n\nNext")
        #expect(DictationFormat.format("new line", previous: "x") == "\n")
    }

    @Test func leadingSpaceOnlyAfterNonWhitespace() {
        #expect(DictationFormat.format("and more", previous: "d") == " and more")
        #expect(DictationFormat.format("and more", previous: " ") == "and more")
        #expect(DictationFormat.format("next one", previous: ".") == " Next one")
        #expect(DictationFormat.format("comma then", previous: "d") == ", then")
        #expect(DictationFormat.previousSignificant("Done. ") == "\n")
        #expect(DictationFormat.previousSignificant("word ") == " ")
        #expect(DictationFormat.previousSignificant("") == nil)
    }

    @Test func segmentCap() {
        let long = Array(repeating: "word", count: 400).joined(separator: " ")
        #expect(DictationFormat.format(long, previous: nil).count == Dictation.maxSegment)
    }

    @Test func commandsAreExact() {
        #expect(DictationCommand.match("Delete that.") == .deleteThat)
        #expect(DictationCommand.match("new line") == .newLine)
        #expect(DictationCommand.match("Reach, stop dictating") == .stopDictating)
        #expect(DictationCommand.match("please delete that file") == nil)
        #expect(DictationCommand.match("stop dictating now") == nil)
        #expect(DictationCommand.wakeToggle("Reach, start dictating.") == true)
        #expect(DictationCommand.wakeToggle("hey reach stop dictating") == false)
        #expect(DictationCommand.wakeToggle("start dictating") == nil)
    }

    @Test func refusesSecureDeniedAndNonText() {
        let ok = FocusInfo(role: "AXTextField", bundleID: "com.apple.Notes")
        #expect(DictationTarget.decide(ok, trusted: true) == .type)
        #expect(DictationTarget.decide(ok, trusted: false) == .noAccessibility)
        #expect(DictationTarget.decide(nil, trusted: true) == .noTextBox)
        #expect(DictationTarget.decide(FocusInfo(role: "AXSecureTextField", bundleID: "com.apple.Safari"), trusted: true) == .secure)
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextField", subrole: "AXSecureTextField", bundleID: "x"), trusted: true) == .secure)
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextArea", bundleID: "com.apple.Terminal"), trusted: true)
                == .deniedApp("com.apple.Terminal"))
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextField", bundleID: "com.example.PasswordVault"), trusted: true)
                == .deniedApp("com.example.PasswordVault"))
        #expect(DictationTarget.decide(FocusInfo(role: "AXButton", bundleID: "com.apple.Notes"), trusted: true) == .noTextBox)
        #expect(DictationTarget.decide(FocusInfo(role: "AXGroup", bundleID: "com.google.Chrome", hasValue: true,
                                                 hasSelectedRange: true), trusted: true) == .type)
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextField", bundleID: "x", isReach: true), trusted: true) == .reachPanel)
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextField", bundleID: "x", editable: false), trusted: true) == .noTextBox)
    }

    @Test func toggleAndSilenceTimeout() {
        var t = DictationToggle()
        #expect(!t.on)
        t.flip(now: 0)
        #expect(t.on)
        t.heard(now: 30)
        #expect(t.tick(now: 89) == .none)
        #expect(t.tick(now: 90) == .timedOut)
        #expect(!t.on)
        #expect(t.tick(now: 200) == .none)
    }

    @Test func cutterWaitsForPause() {
        var c = DictationCutter()
        c.feed("hello", now: 0)
        c.feed("hello there", now: 0.5)
        #expect(c.tick(now: 1.2) == nil)
        #expect(c.tick(now: 1.6) == "hello there")
        #expect(c.tick(now: 3) == nil)
    }

    @Test func axInsertThenDeleteThat() {
        let f = FakeFocus(), p = FakePasteboard(), k = FakeKeys()
        let ins = makeInserter(f, p, k)
        f.text = "Note:"
        #expect(ins.insert("hello from reach", startedIn: 42) == .inserted(.ax, " hello from reach"))
        #expect(f.text == "Note: hello from reach")
        #expect(ins.deleteThat() == .ax(17))
        #expect(f.text == "Note:")
        #expect(ins.deleteThat() == .nothing)
        #expect(k.log.isEmpty && p.contents == "owner's clipboard")
    }

    @Test func pasteFallbackRestoresClipboardAndUndoes() {
        let f = FakeFocus(), p = FakePasteboard(), k = FakeKeys()
        f.axWorks = false
        let ins = makeInserter(f, p, k)
        #expect(ins.insert("hi there", startedIn: 42) == .inserted(.paste, "Hi there"))
        #expect(k.log == ["cmd-v"])
        #expect(p.contents == "owner's clipboard")
        #expect(ins.deleteThat() == .undo)
        #expect(k.log == ["cmd-v", "cmd-z"])
    }

    @Test func deleteKeysCapped() {
        let f = FakeFocus(), p = FakePasteboard(), k = FakeKeys()
        f.axDeleteWorks = false
        let ins = makeInserter(f, p, k)
        _ = ins.insert(Array(repeating: "abcdefghij", count: 30).joined(separator: " "), startedIn: 42)
        #expect(ins.deleteThat() == .keys(200))
        #expect(k.log == ["delete x200"])
    }

    @Test func deleteThatRefusesAfterFocusMoves() {
        let f = FakeFocus(), p = FakePasteboard(), k = FakeKeys()
        let ins = makeInserter(f, p, k)
        _ = ins.insert("hello", startedIn: 42)
        f.info?.pid = 77   // another app now
        #expect(ins.deleteThat() == .nothing)
        f.info?.pid = 42
        #expect(ins.deleteThat() == .nothing)   // ledger cleared: nothing stale is deleted later
        _ = ins.insert("again", startedIn: 42)
        f.text += " typed by hand"
        #expect(ins.deleteThat() == .nothing)   // caret no longer right after our text
        #expect(k.log.isEmpty)
        #expect(DictationTarget.decide(FocusInfo(role: "AXTextField", bundleID: "com.example.ok",
                                                 frontBundleID: "com.apple.keychainaccess"), trusted: true)
                == .deniedApp("com.apple.keychainaccess"))
    }

    @Test func refusesAndStopsOnFocusChange() {
        let f = FakeFocus(), p = FakePasteboard(), k = FakeKeys()
        let ins = makeInserter(f, p, k)
        #expect(ins.insert("hello", startedIn: 7) == .focusChanged)
        f.info = FocusInfo(role: "AXSecureTextField", bundleID: "com.apple.Safari", pid: 42)
        #expect(ins.insert("hunter two", startedIn: 42) == .refused(.secure))
        f.info = FocusInfo(role: "AXTextField", bundleID: "com.apple.Safari", pid: 42)
        f.axWorks = false
        f.pidAfterFirstLook = 99   // switched apps between the check and the paste
        #expect(ins.insert("hello", startedIn: 42) == .focusChanged)
        #expect(k.log.isEmpty)
    }
}
