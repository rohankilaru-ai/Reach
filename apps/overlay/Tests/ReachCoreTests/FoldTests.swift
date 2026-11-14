import CoreGraphics
import Testing
@testable import ReachCore

@Suite struct FoldTests {
    let panel = CGRect(x: 536, y: 700, width: 448, height: 220)

    @Test func plainAnswerFoldsAfterEightSeconds() {
        let c = FoldClock(now: 100)   // answer shown at 100
        #expect(c.decide(now: 107.9, expanded: true, running: false, speaking: false) == .stay)
        #expect(c.decide(now: 108, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func speechDoneStartsTheEightSeconds() {
        var c = FoldClock(now: 100)
        #expect(c.decide(now: 110, expanded: true, running: false, speaking: true) == .stay)
        c.speechFinished(now: 112)
        #expect(c.decide(now: 119, expanded: true, running: false, speaking: false) == .stay)
        #expect(c.decide(now: 120, expanded: true, running: false, speaking: false) == .hardMax)
        // without the hard max in the way: speech ends 3 s after the answer
        var d = FoldClock(now: 100)
        d.speechFinished(now: 103)
        #expect(d.decide(now: 110.5, expanded: true, running: false, speaking: false) == .stay)
        #expect(d.decide(now: 111, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func focusedIdleFieldDoesNotKeepItOpen() {
        // keyboard focus is not an input to the decision: nothing typed, so it folds at 8 s
        let c = FoldClock(now: 0)
        #expect(c.decide(now: 8, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func mouseParkedElsewhereOrInTheMarginDoesNotCount() {
        let margin = CGPoint(x: panel.minX - 10, y: panel.midY)   // the old 24 pt keep zone
        let tabBar = CGPoint(x: 200, y: 880)
        #expect(!FoldClock.hoverCounts(mouse: margin, last: CGPoint(x: margin.x - 30, y: margin.y), panel: panel))
        #expect(!FoldClock.hoverCounts(mouse: tabBar, last: CGPoint(x: 150, y: 880), panel: panel))
        let inside = CGPoint(x: panel.midX, y: panel.midY)
        #expect(!FoldClock.hoverCounts(mouse: inside, last: inside, panel: panel))   // parked inside: not interaction
        #expect(FoldClock.hoverCounts(mouse: inside, last: CGPoint(x: inside.x + 5, y: inside.y), panel: panel))
        // a parked cursor never refreshes the clock, so the panel still folds
        let c = FoldClock(now: 0)
        #expect(c.decide(now: 8, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func interactionInsideRestartsTheTimer() {
        var c = FoldClock(now: 0)
        c.interaction(now: 6)
        #expect(c.decide(now: 13, expanded: true, running: false, speaking: false) == .stay)
        #expect(c.decide(now: 14, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func typingKeepsItOpenForFiveSeconds() {
        var c = FoldClock(now: 0)
        c.typedKey(now: 2)
        #expect(c.decide(now: 6.9, expanded: true, running: false, speaking: false, hasDraft: true) == .stay)
        // a draft left alone holds only until the hard maximum
        #expect(c.decide(now: 21.9, expanded: true, running: false, speaking: false, hasDraft: true) == .stay)
        #expect(c.decide(now: 22, expanded: true, running: false, speaking: false, hasDraft: true) == .hardMax)
    }

    @Test func stuckSpeechFoldsAtTheHardMaximum() {
        let c = FoldClock(now: 0)
        #expect(c.decide(now: 19.9, expanded: true, running: false, speaking: true) == .stay)
        #expect(c.decide(now: 20, expanded: true, running: false, speaking: true) == .hardMax)
    }

    @Test func runningAskIsNeverFolded() {
        let c = FoldClock(now: 0)
        #expect(c.decide(now: 60, expanded: true, running: true, speaking: false) == .stay)
    }

    @Test func collapsedPanelIsLeftAlone() {
        let c = FoldClock(now: 0)
        #expect(c.decide(now: 60, expanded: false, running: false, speaking: false) == .stay)
    }

    @Test func reopeningResetsTheClocks() {
        var c = FoldClock(now: 0)
        c.typedKey(now: 1); c.speechFinished(now: 2)
        c.opened(now: 50)
        #expect(c.decide(now: 57, expanded: true, running: false, speaking: false) == .stay)
        #expect(c.decide(now: 58, expanded: true, running: false, speaking: false) == .idle)
    }

    @Test func expandedPanelStaysUnderTheMenuBar() {
        // the panel grows down from an anchor under the menu bar and is clamped inside the visible frame
        let visible = CGRect(x: 0, y: 0, width: 1512, height: 945)
        let size = CGSize(width: 448, height: 300)
        let o = Placement.origin(anchor: CGPoint(x: 756, y: 990), size: size, visible: visible)
        #expect(o.y + size.height <= visible.maxY)
    }
}
