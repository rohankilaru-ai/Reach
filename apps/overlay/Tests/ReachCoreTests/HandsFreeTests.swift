import Testing
@testable import ReachCore

@Suite struct HandsFreeTests {
    @Test func wakeWordStrict() {
        #expect(WakeWord.match("Reach, scroll down") == .init(matched: true, stripped: "scroll down"))
        #expect(WakeWord.match("hey REACH! open this").stripped == "open this")
        #expect(!WakeWord.match("which one is it").matched)
        #expect(!WakeWord.match("rich people").matched)
        #expect(!WakeWord.match("did Reach save my note").matched)
        #expect(WakeWord.isStopListening("Reach, stop listening."))
    }

    @Test func segmentsByPauseAndDropsShort() {
        var s = Segmenter()
        _ = s.feed("what does this", now: 0)
        _ = s.feed("what does this mean", now: 0.5)
        let r1 = s.tick(now: 1.2)
        #expect(r1 == nil)
        let r2 = s.tick(now: 1.5)
        #expect(r2?.ask == "what does this mean")
        _ = s.feed("yeah", now: 2)
        let r3 = s.tick(now: 3)
        #expect(r3 == nil)
        _ = s.feed("Reach", now: 4)
        let w = s.tick(now: 5)
        #expect(w == nil)   // wake word alone: empty ask
        #expect(Segmenter.accept("reach next")?.wakeMatched == true)
    }

    @Test func decisions() {
        #expect(AddressedDecision.decide(reply: .init(text: "x", addressed: true), ask: "x", timedOut: false) == .ask("x", chip: nil))
        #expect(AddressedDecision.decide(reply: .init(text: "x", addressed: false), ask: "x", timedOut: false) == .ignore)
        #expect(AddressedDecision.decide(reply: .init(text: "x", addressed: true), ask: "x", timedOut: true) == .ignore)
        #expect(AddressedDecision.decide(reply: nil, ask: "x", timedOut: false) == .ignore)
        #expect(AddressedDecision.decide(reply: .init(text: "x", addressed: true, command: "scroll_down"), ask: "x", timedOut: false)
                == .ask("x", chip: "scroll down"))
        for c in ["stop", "cancel", "pause"] {
            #expect(AddressedDecision.decide(reply: .init(text: "x", addressed: true, command: c), ask: "x", timedOut: false)
                    == .localStop(chip: c))
        }
    }

    @Test func pendingMatchesAndExpires() {
        var p = PendingUtterance()
        p.sent(text: "a b", ask: "a b", at: 0)
        let stale = p.reply(.init(text: "other", addressed: true), at: 0.1)
        #expect(stale == nil)
        let r4 = p.expire(at: 1.6)
        #expect(r4 == .ignore)
        #expect(p.pending == nil)
    }

    @Test func echoGate() {
        var e = EchoGate()
        #expect(e.mayListen(now: 0))
        e.speakingChanged(true, now: 1)
        #expect(!e.mayListen(now: 2))
        e.speakingChanged(false, now: 3)
        #expect(!e.mayListen(now: 3.4))
        #expect(e.mayListen(now: 3.5))
    }

    @Test func pinchSignal() {
        var p = PinchSignal()
        let r5 = p.update(state: "pinch", now: 0)
        #expect(r5 == .press)
        let r6 = p.update(state: "pinch", now: 0.1)
        #expect(r6 == .none)
        let r7 = p.tick(now: 0.5)
        #expect(r7 == .none)
        let r8 = p.tick(now: 0.8)
        #expect(r8 == .release)
        let r9 = p.update(state: "pinch", now: 1)
        #expect(r9 == .press)
        let r10 = p.update(state: "hover", now: 1.1)
        #expect(r10 == .release)
    }

    @Test func toggle() {
        var t = HandsFreeToggle()
        #expect(!t.on)
        let r11 = t.toggle()
        #expect(r11)
        #expect(t.active(now: 0))
        t.pause(minutes: 5, now: 0)
        #expect(!t.active(now: 299))
        #expect(t.active(now: 300))
        let off = t.toggle()
        #expect(!off)
    }

    @Test func handsModeWire() throws {
        #expect(try ClientMessage.hands(on: true).json() == #"{"on":true,"type":"hands"}"#)
        #expect(try ClientMessage.hands(on: false).json() == #"{"on":false,"type":"hands"}"#)
    }

    @Test func wire() throws {
        let j = try ClientMessage.utterance(text: "Reach, next", wakeMatched: true).json()
        #expect(j.contains("\"source\":\"always_listening\"") && j.contains("\"wake_matched\":true"))
        let m = try ServerMessage.decode(#"{"type":"addressed","text":"t","p_addressed":0.9,"addressed":true,"command":"next","reason":"r"}"#)
        #expect(m == .addressed(.init(text: "t", pAddressed: 0.9, addressed: true, command: "next", reason: "r")))
        let p = try ServerMessage.decode(#"{"type":"pointer","x":1,"y":2,"state":"pinch"}"#)
        #expect(p == .pointer(taskID: nil, x: 1, y: 2, label: nil, state: "pinch"))
    }
}
