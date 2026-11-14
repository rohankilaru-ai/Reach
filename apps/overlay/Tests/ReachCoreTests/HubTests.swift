import CoreGraphics
import Foundation
import Testing
@testable import ReachCore

@Suite struct HubGestureTests {
    let circle = Selection(text: "x = 3", region: [10, 20, 100, 80], image_path: "/c.png", path: [[10, 20], [110, 20], [60, 100]])

    @Test func holdCircleReleaseIsOneAskWithBoth() {
        var g = HubGesture()
        _ = g.holdStarted()
        g.circleDrawn(gen: 1)
        #expect(g.circleCaptured(gen: 1, circle) == nil)       // still holding
        g.released()
        #expect(g.heard(.send("explain this")) == .ask(text: "explain this", selection: circle))
        #expect(g.phase == .idle && !g.hasCircle)
    }

    @Test func releaseBeforeCaptureWaitsForIt() {
        var g = HubGesture()
        _ = g.holdStarted()
        g.circleDrawn(gen: 4)
        g.released()
        #expect(g.heard(.send("what is this")) == nil)          // capture still running
        #expect(g.circleCaptured(gen: 3, circle) == nil)         // stale generation ignored
        #expect(g.circleCaptured(gen: 4, circle) == .ask(text: "what is this", selection: circle))
    }

    @Test func newerCircleReplacesOlder() {
        var g = HubGesture()
        _ = g.holdStarted()
        g.circleDrawn(gen: 1)
        g.circleDrawn(gen: 2)
        g.released()
        _ = g.heard(.send("q"))
        #expect(g.circleCaptured(gen: 1, circle) == nil)
        var other = circle; other.text = "newer"
        #expect(g.circleCaptured(gen: 2, other) == .ask(text: "q", selection: other))
    }

    @Test func speechWithoutCircleIsPlainAsk() {
        var g = HubGesture()
        _ = g.holdStarted(); g.released()
        #expect(g.heard(.send("what time is it")) == .ask(text: "what time is it", selection: nil))
    }

    @Test func circleWithoutSpeechOpensTypingWithChip() {
        var g = HubGesture()
        _ = g.holdStarted(); g.circleDrawn(gen: 1); _ = g.circleCaptured(gen: 1, circle); g.released()
        #expect(g.heard(.reject("didn't catch that")) == .typeWithChip(circle))
    }

    @Test func nothingAtAll() {
        var g = HubGesture()
        _ = g.holdStarted(); g.released()
        #expect(g.heard(.reject("didn't catch that")) == .nothing(why: "didn't catch that"))
    }

    @Test func escCancelsAndLateResultsAreIgnored() {
        var g = HubGesture()
        _ = g.holdStarted(); g.circleDrawn(gen: 1)
        #expect(g.cancel() == .cancelled)
        #expect(g.circleCaptured(gen: 1, circle) == nil)
        #expect(g.heard(.send("late")) == nil)
        #expect(g.cancel() == nil)
    }

    @Test func eachHoldStartsClean() {
        var g = HubGesture()
        _ = g.holdStarted(); g.circleDrawn(gen: 1); _ = g.circleCaptured(gen: 1, circle); g.released()
        _ = g.heard(.send("explain this"))
        _ = g.holdStarted(); g.released()
        #expect(g.heard(.send("and now?")) == .ask(text: "and now?", selection: nil))   // no stale circle
    }

    @Test func zoomWordIsLocal() {
        #expect(ZoomCommand.isZoom("Zoom.") && ZoomCommand.isZoom(" zoom in ") && !ZoomCommand.isZoom("zoom out of this"))
        var g = HubGesture()
        _ = g.holdStarted(); g.circleDrawn(gen: 1); _ = g.circleCaptured(gen: 1, circle); g.released()
        #expect(g.heard(.send("Zoom")) == .zoom(circle))
        _ = g.holdStarted(); g.released()
        #expect(g.heard(.send("zoom")) == .nothing(why: "circle something first, then say zoom"))
    }
}

@Suite struct VerdictTests {
    func receipt(_ extra: String) throws -> Receipt {
        let m = try ServerMessage.decode(#"{"type":"receipt","task_id":"t","system1_asks":0,"tokens":0,"duration_ms":1,"# + extra + "}")
        guard case .receipt(let r) = m else { throw SpeechTextError() }
        return r
    }
    struct SpeechTextError: Error {}

    @Test func mapsEveryOutcome() throws {
        #expect(Verdict.of(try receipt(#""status":"ok","reason":"completed""#)) == .done)
        #expect(Verdict.of(try receipt(#""status":"ok","reason":"needs_selection""#)) == .needsYou("Circle the thing you mean"))
        #expect(Verdict.of(try receipt(#""status":"failed","reason":"note write failed","write_outcome":"failed""#)).title
                == "Failed: the note write failed")
        #expect(Verdict.of(try receipt(#""status":"ok","reason":"completed","write_outcome":"unverified""#)).phase == .failed)
        #expect(Verdict.of(try receipt(#""status":"ok","reason":"completed","write_outcome":"verified""#)) == .done)
        #expect(Verdict.of(try receipt(#""status":"ok","reason":"completed","outcome":"not_done""#))
                == .failed("the browser task did not finish"))
        #expect(Verdict.of(try receipt(#""status":"aborted","reason":"aborted""#)).phase == .idle)
        #expect(Verdict.of(try receipt(#""status":"failed","reason":"error: boom""#)).title == "Failed: error: boom")
    }

    @Test func panelStateShowsNeedsYouForNeedsSelection() throws {
        var s = PanelState()
        s.apply(.ready(protocol: 1)); s.submitted(); s.apply(.accepted(taskID: "t"))
        s.apply(try ServerMessage.decode(#"{"type":"receipt","task_id":"t","status":"ok","reason":"needs_selection","system1_asks":0,"tokens":0,"duration_ms":1}"#))
        #expect(s.phase == .needsYou && s.note == "Circle the thing you mean")
    }
}

@Suite struct SpokenTextTests {
    @Test func stripsCitationsAndMarkdown() {
        let a = "## Answer\n**Rent** is due on the *1st* [E3]. See [the lease](file:///x.pdf) [1, 2].\n- Late fee: `$50` [p. 4]"
        #expect(SpokenText.clean(a) == "Answer Rent is due on the 1st. See the lease. Late fee: $50")
    }

    @Test func capsAtSentenceBoundary() {
        let s = String(repeating: "This is one sentence. ", count: 40)
        let c = SpokenText.clean(s)
        #expect(c.count <= 400 && c.hasSuffix("."))
        let words = String(repeating: "word ", count: 200)
        let w = SpokenText.clean(words)
        #expect(w.count <= 401 && w.hasSuffix("…"))
    }

    @Test func autoSpeakOnlyForVoice() {
        #expect(SpokenText.autoSpeak(fromVoice: true, toggle: true))
        #expect(!SpokenText.autoSpeak(fromVoice: false, toggle: true))
        #expect(!SpokenText.autoSpeak(fromVoice: true, toggle: false))
    }
}

@Suite struct ZoomAndHoverTests {
    @Test func zoomClamps() {
        #expect(ZoomLens.clamp(1) == 2 && ZoomLens.clamp(9) == 4 && ZoomLens.clamp(3) == 3 && ZoomLens.clamp(.nan) == 2)
        #expect(ZoomLens.scrolled(3.9, deltaY: 50) == 4 && ZoomLens.pinched(2.2, magnification: -0.5) == 2)
        #expect(ZoomLens.lensSize(image: CGSize(width: 100, height: 50), scale: 3) == CGSize(width: 300, height: 150))
        #expect(ZoomLens.lensSize(image: CGSize(width: 400, height: 400), scale: 4) == CGSize(width: 560, height: 560))
    }

    @Test func hotZoneCoversNotchStrip() {
        let hot = Hover.hotZone(anchor: CGPoint(x: 756, y: 939), pill: CGSize(width: 96, height: 30), screenTop: 982)
        #expect(hot.contains(CGPoint(x: 756, y: 975)))     // in the notch / menu bar
        #expect(hot.contains(CGPoint(x: 756, y: 920)))     // on the pill
        #expect(!hot.contains(CGPoint(x: 756, y: 880)))    // below it
        #expect(!hot.contains(CGPoint(x: 300, y: 975)))    // far left in the menu bar
    }

    @Test func hoverExpandsAfterDwellAndCollapsesAfterGrace() {
        var t = Hover.Tracker()
        let hot = CGRect(x: 0, y: 900, width: 200, height: 80)
        let panel = CGRect(x: 0, y: 600, width: 400, height: 300)
        let inHot = CGPoint(x: 100, y: 950), away = CGPoint(x: 900, y: 100)
        #expect(t.update(mouse: inHot, now: 0, expanded: false, hot: hot, panel: panel, busy: false) == .none)
        #expect(t.update(mouse: inHot, now: 0.15, expanded: false, hot: hot, panel: panel, busy: false) == .expand)
        #expect(t.update(mouse: CGPoint(x: 200, y: 700), now: 1, expanded: true, hot: hot, panel: panel, busy: false) == .none)
        #expect(t.update(mouse: away, now: 2, expanded: true, hot: hot, panel: panel, busy: true) == .none)  // busy
        #expect(t.update(mouse: away, now: 3, expanded: true, hot: hot, panel: panel, busy: false) == .none)
        #expect(t.update(mouse: away, now: 3.7, expanded: true, hot: hot, panel: panel, busy: false) == .collapse)
    }

    @Test func hoverDisarmsAfterCollapseUntilTheMouseLeaves() {
        var t = Hover.Tracker()
        let hot = CGRect(x: 0, y: 900, width: 200, height: 80), panel = CGRect(x: 0, y: 600, width: 400, height: 300)
        let inHot = CGPoint(x: 100, y: 950)
        t.collapsed()   // Esc / tap / idle with the mouse parked at the notch
        #expect(t.update(mouse: inHot, now: 0, expanded: false, hot: hot, panel: panel, busy: false) == .none)
        #expect(t.update(mouse: inHot, now: 5, expanded: false, hot: hot, panel: panel, busy: false) == .none)
        _ = t.update(mouse: CGPoint(x: 900, y: 100), now: 6, expanded: false, hot: hot, panel: panel, busy: false)
        _ = t.update(mouse: inHot, now: 7, expanded: false, hot: hot, panel: panel, busy: false)
        #expect(t.update(mouse: inHot, now: 7.2, expanded: false, hot: hot, panel: panel, busy: false) == .expand)
    }

    @Test func hoverNeverClosesAKeyOpenedPanel() {
        var t = Hover.Tracker()
        t.openedElsewhere()
        let r = t.update(mouse: CGPoint(x: 5000, y: 0), now: 10, expanded: true, hot: .zero,
                         panel: CGRect(x: 0, y: 0, width: 10, height: 10), busy: false)
        #expect(r == .none)
    }

    @Test func answerBudget() {
        #expect(PanelBudget.answerMax(visibleHeight: 1000, spaceBelowAnchor: 1000, chrome: 200) == 600)
        #expect(PanelBudget.answerMax(visibleHeight: 1000, spaceBelowAnchor: 500, chrome: 200) == 292)
        #expect(PanelBudget.answerMax(visibleHeight: 300, spaceBelowAnchor: 100, chrome: 200) == 60)
        #expect(HubStatus.of(phase: .working, listening: true) == .listening && HubStatus.listening.pulses)
        #expect(HubStatus.of(phase: .failed, listening: false) == .failed)
    }
}

@Suite struct CalmPanelTests {
    @Test func plainTextStripsMarkdownKeepsNumberedSteps() {
        let a = "## Rent\n**Rent** is due on the *1st* [E3].\n- late fee applies\n1. Open the app\n2. Tap Pay"
        #expect(PlainText.display(a) == "Rent\nRent is due on the 1st.\nlate fee applies\n1. Open the app\n2. Tap Pay")
    }

    @Test func progressFromToolNames() {
        #expect(ProgressLine.of(tool: "see_region") == "Looking at your screen…")
        #expect(ProgressLine.of(tool: "browser_task") == "Using the browser…")
        #expect(ProgressLine.of(tool: "reach_search_docs") == "Searching your documents…")
        #expect(ProgressLine.of(tool: nil) == "Thinking…")
    }

    @Test func endStatesAreOneSentenceOneAction() {
        #expect(PlainState.of(verdict: .needsYou("x")) == PlainState(sentence: "Circle the thing you mean.", action: .circle))
        #expect(PlainState.of(verdict: .failed("the note write failed"))?.sentence == "The note wasn't saved.")
        #expect(PlainState.of(verdict: .failed("error: Boom 42"))?.sentence == "That didn't work.")
        #expect(PlainState.of(verdict: .done) == nil)
        #expect(PlainState.of(note: "allow Screen Recording for Reach").action == .openScreenSettings)
        #expect(PlainState.of(note: "start `reach serve`").action == .startServer)
    }

    @Test func pointerLabelsStayShort() {
        #expect(PointerLabel.short("Click Save") == "Click Save")
        let s = PointerLabel.short("Click the blue Save button in the lower right corner")!
        #expect(s.count <= 30 && s.hasSuffix("…"))
        #expect(PointerLabel.short("  ") == nil)
    }

    @Test func lensAvoidsPanelAndCircle() {
        let vis = CGRect(x: 0, y: 0, width: 1512, height: 945)
        let panel = CGRect(x: 536, y: 560, width: 440, height: 379)
        let circle = CGRect(x: 600, y: 300, width: 180, height: 120)
        let size = CGSize(width: 400, height: 300)
        let o = LensPlacement.place(size: size, avoid: [panel, circle], near: circle, visible: vis)
        let r = CGRect(origin: o, size: size)
        #expect(!r.intersects(panel) && !r.intersects(circle) && vis.contains(r))
    }
}
