import CoreGraphics
import ReachCore

/// `--fold-selftest`: the answer panel's fold rules driven headlessly over scripted timelines (no windows, no
/// input events, no network). One PASS/FAIL line per scenario.
@MainActor func runFoldSelftest() -> Int32 {
    var allOK = true
    func line(_ name: String, _ ok: Bool, _ detail: String) {
        allOK = allOK && ok
        log("fold selftest \(ok ? "PASS" : "FAIL") \(name): \(detail)")
    }
    let panel = CGRect(x: 536, y: 700, width: 448, height: 220)
    /// Ticks once a second like the app's idle timer; returns the first second the panel folds, and why.
    func firstFold(_ c: FoldClock, from t0: Double, to t1: Double, running: (Double) -> Bool = { _ in false },
                   speaking: (Double) -> Bool = { _ in false }, draft: Bool = false) -> (Double, String)? {
        var t = t0
        while t <= t1 {
            let v = c.decide(now: t, expanded: true, running: running(t), speaking: speaking(t), hasDraft: draft)
            if v != .stay { return (t - t0, v.rawValue) }
            t += 1
        }
        return nil
    }
    func show(_ r: (Double, String)?) -> String { r.map { "folds at \(Int($0.0)) s (\($0.1))" } ?? "never folds" }

    var r = firstFold(FoldClock(now: 0), from: 0, to: 60)
    line("plain answer, no speech", r?.0 == 8, show(r))

    var c = FoldClock(now: 0)
    c.speechFinished(now: 4)
    r = firstFold(c, from: 0, to: 60, speaking: { $0 < 4 })
    line("answer spoken for 4 s", r?.0 == 12, show(r))

    r = firstFold(FoldClock(now: 0), from: 0, to: 60)   // focus is not an input: same as no focus
    line("field focused but idle", r?.0 == 8, show(r))

    // cursor parked in the old 24 pt margin and over the tab bar: never counts as interaction
    c = FoldClock(now: 0)
    var last: CGPoint? = nil
    for t in 0..<60 {
        let m = t % 2 == 0 ? CGPoint(x: panel.minX - 10, y: panel.midY) : CGPoint(x: 300, y: 905)
        if FoldClock.hoverCounts(mouse: m, last: last, panel: panel) { c.interaction(now: Double(t)) }
        last = m
    }
    r = firstFold(c, from: 0, to: 60)
    line("mouse resting elsewhere", r?.0 == 8, show(r))

    c = FoldClock(now: 0)
    c.typedKey(now: 3)
    r = firstFold(c, from: 0, to: 60, draft: true)
    line("typed then stopped with a draft", r?.0 == 23, show(r))

    c = FoldClock(now: 0)
    c.interaction(now: 5)   // scrolled the answer
    r = firstFold(c, from: 0, to: 60)
    line("scroll inside restarts", r?.0 == 13, show(r))

    r = firstFold(FoldClock(now: 0), from: 0, to: 60, speaking: { _ in true })
    line("speech flag stuck on", r?.0 == 20, show(r))

    r = firstFold(FoldClock(now: 0), from: 0, to: 60, running: { _ in true })
    line("ask running 60 s", r == nil, show(r))

    log("fold selftest \(allOK ? "PASS" : "FAIL")")
    return allOK ? 0 : 1
}
