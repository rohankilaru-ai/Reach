import Testing
@testable import ReachCore

@Suite struct VoiceGateTests {
    @Test func sendsConfidentText() {
        #expect(VoiceGate.judge(text: " what is twelve ", confidences: [0.9, 0.8]) == .send("what is twelve"))
    }

    @Test func rejectsEmptyOrLowConfidence() {
        #expect(VoiceGate.judge(text: "   ", confidences: [0.9]) == .reject("didn't catch that"))
        #expect(VoiceGate.judge(text: "uh what", confidences: [0.3, 0.45]) == .reject("didn't catch that"))
    }

    @Test func thresholdIsInclusiveAtPointFour() {
        #expect(VoiceGate.judge(text: "yes", confidences: [0.4]) == .send("yes"))
        #expect(VoiceGate.judge(text: "yes", confidences: [0.39]) == .reject("didn't catch that"))
    }

    @Test func unreportedConfidencePassesNonEmptyText() {
        #expect(VoiceGate.average([]) == nil)
        #expect(VoiceGate.average([0, 0, 0]) == nil)
        #expect(VoiceGate.judge(text: "hello", confidences: [0, 0]) == .send("hello"))
        #expect(VoiceGate.judge(text: "", confidences: [0, 0]) == .reject("didn't catch that"))
    }
}

@Suite struct PushToTalkTests {
    @Test func pressReleaseFinish() {
        var p = PushToTalk()
        let r1 = p.press()
        #expect(r1 == .start(session: 1))
        #expect(p.phase == .listening && p.accepts(partialFrom: 1))
        let r2 = p.release()
        #expect(r2 == .finish(session: 1))
        #expect(p.phase == .finishing && !p.accepts(partialFrom: 1))
        let r3 = p.finished(session: 1)
        #expect(r3)
        #expect(p.phase == .idle)
    }

    @Test func repeatedPressAndStrayReleaseDoNothing() {
        var p = PushToTalk()
        let r4 = p.release()
        #expect(r4 == .none)
        _ = p.press()
        let r5 = p.press()
        #expect(r5 == .none)          // key auto-repeat
        _ = p.release()
        let r6 = p.press()
        #expect(r6 == .none)          // still finishing
        let r7 = p.release()
        #expect(r7 == .none)
    }

    @Test func cancelInEveryState() {
        var p = PushToTalk()
        let r8 = p.cancel()
        #expect(r8 == .none)
        _ = p.press()
        let r9 = p.cancel()
        #expect(r9 == .cancel(session: 1))
        #expect(p.phase == .idle)
        let r10 = p.release()
        #expect(r10 == .none)        // key up after Esc is ignored
        _ = p.press(); _ = p.release()
        let r11 = p.cancel()
        #expect(r11 == .cancel(session: 2))
        #expect(p.phase == .idle)
    }

    @Test func lateResultAfterCancelIsDropped() {
        var p = PushToTalk()
        _ = p.press(); _ = p.release(); _ = p.cancel()
        let r12 = p.finished(session: 1)
        #expect(!r12)
        _ = p.press()                         // session 2 listening
        let r13 = p.finished(session: 1)
        #expect(!r13)      // stale result from session 1
        #expect(!p.accepts(partialFrom: 1))
        _ = p.release()
        let r14 = p.finished(session: 2)
        #expect(r14)
    }
}
