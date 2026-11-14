import Foundation
import Testing
@testable import ReachCore

@Suite struct HandsModeTests {
    @Test func toggleFollowsServerTruthAndNeedsAccessibility() {
        #expect(HandsMode.toggle(currentOn: false, accessibility: true) == .send(on: true))
        #expect(HandsMode.toggle(currentOn: false, accessibility: false) == .needsAccessibility)
        #expect(HandsMode.toggle(currentOn: true, accessibility: false) == .send(on: false))
    }

    @Test func stateSyncHintOnceAndOffOnExit() {
        var s = HandsModeState()
        #expect(s.apply(on: false, pid: nil, reason: nil) == .none)        // connect: already off
        #expect(s.apply(on: true, pid: 7, reason: "started") == .showHint)
        #expect(s.on && s.pid == 7)
        #expect(s.apply(on: true, pid: 7, reason: nil) == .none)           // a no-op repeat
        #expect(s.apply(on: false, pid: nil, reason: "exited") == .showOff(reason: "exited"))
        #expect(!s.on && s.pid == nil)
        #expect(s.apply(on: true, pid: 8, reason: "started") == .none)     // the hint is one-time
    }

    @Test func handsStateDecodes() throws {
        #expect(try ServerMessage.decode(#"{"type":"hands_state","on":true,"pid":12}"#)
                == .handsState(on: true, pid: 12, reason: nil))
        #expect(try ServerMessage.decode(#"{"type":"hands_state","on":false,"pid":null,"reason":"stopped"}"#)
                == .handsState(on: false, pid: nil, reason: "stopped"))
    }

    @Test func phaseStreamsAndLooks() throws {
        var s = HandsModeState()
        _ = s.apply(on: true, pid: 1, reason: "started")
        #expect(s.apply(on: true, pid: 1, reason: nil, phase: "dragging") == .none && s.phase == "dragging")
        _ = s.apply(on: false, pid: nil, reason: "open palm")
        #expect(s.phase == nil)
        #expect(HandsMode.look(phase: "pointing").tint == .blue)
        #expect(HandsMode.look(phase: "pinching").tint == .green && HandsMode.look(phase: "pinching").pulse)
        #expect(HandsMode.look(phase: "paused").tint == .amber)
        #expect(HandsMode.look(phase: "no_hand").tint == .grey)
        #expect(try ServerMessage.decode(#"{"type":"hands_state","on":true,"pid":3,"phase":"pointing"}"#)
                == .handsState(on: true, pid: 3, reason: nil, phase: "pointing"))
    }

    @Test func spokenPhrase() {
        #expect(HandsMode.phrase("Reach, hands mode on") == true)
        #expect(HandsMode.phrase("Reach, hands mode off.") == false)
        #expect(HandsMode.phrase("turn hands mode on") == nil)
        #expect(HandsMode.phrase("Reach, what is hands mode") == nil)
    }
}

@Suite struct ReceiptSummaryTests {
    func receipt(asks: Int, tools: [String]?, ms: Int) -> Receipt {
        try! JSONDecoder().decode(Receipt.self, from: Data("""
        {"task_id":"t","status":"ok","reason":"completed","system1_asks":\(asks),"tokens":5056,"duration_ms":\(ms),
         "est_cost_usd":0.0001,"tools":\(tools.map { "[" + $0.map { "\"\($0)\"" }.joined(separator: ",") + "]" } ?? "null")}
        """.utf8))
    }

    @Test func toolNamesInPlainWords() {
        #expect(ReceiptSummary.phrase(tool: "chrome_open_url") == "opened a Chrome tab")
        #expect(ReceiptSummary.phrase(tool: "evidence_search") == "searched your documents")
        #expect(ReceiptSummary.phrase(tool: "browser_task") == "used the browser")
        #expect(ReceiptSummary.phrase(tool: "note_append") == "saved a note")
        #expect(ReceiptSummary.phrase(tool: "see_screen") == "looked at your screen")
        #expect(ReceiptSummary.phrase(tool: "see_region") == "looked at your screen")
        #expect(ReceiptSummary.phrase(tool: "point_at") == "pointed at the screen")
        #expect(ReceiptSummary.phrase(tool: "email_draft") == "opened an email draft")
        #expect(ReceiptSummary.phrase(tool: "ax_press") == "used an app")
        #expect(ReceiptSummary.phrase(tool: "finder_reveal") == "opened a folder")
        #expect(ReceiptSummary.phrase(tool: "wrap_up") == nil)
    }

    @Test func summaryLineHasNoInternals() {
        let line = ReceiptSummary.line(receipt(asks: 1, tools: ["chrome_open_url", "see_screen", "see_region"], ms: 5245))
        #expect(line == "Checked by Drex · opened a Chrome tab · looked at your screen · 5 s")
        for bad in ["tok", "$", "ms", "noul", "supervise", "wrap_up", "completed"] { #expect(!line.contains(bad)) }
        #expect(ReceiptSummary.line(receipt(asks: 0, tools: nil, ms: 200)) == "1 s")
    }
}
