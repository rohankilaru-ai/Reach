import XCTest
@testable import ReachCore

final class QuietStatusTests: XCTestCase {
    private func receipt(_ fields: String) throws -> Receipt {
        let m = try ServerMessage.decode(#"{"type":"receipt","task_id":"t","system1_asks":1,"tokens":9,"duration_ms":5,"# + fields + "}")
        guard case .receipt(let r) = m else { throw XCTSkip("not a receipt") }
        return r
    }
    private func end(_ fields: String) throws -> TaskEnd {
        let r = try receipt(fields)
        return TaskEnd.of(r, verdict: Verdict.of(r))
    }

    func testNoteSavedIsCompactDone() throws {
        let e = try end(#""status":"ok","reason":"completed","write_outcome":"verified","tools":["note_append"]"#)
        XCTAssertEqual(e, .compact(CompactResult(kind: .done, line: "Done · Note saved")))
        if case .compact(let c) = e { XCTAssertFalse(c.retry); XCTAssertEqual(c.sound.name, "Tink") }
    }

    func testBrowserNotDoneIsCompactFailedWithRetry() throws {
        let e = try end(#""status":"ok","reason":"completed","outcome":"not_done","tools":["browser_task"]"#)
        XCTAssertEqual(e, .compact(CompactResult(kind: .failed, line: "Couldn't finish")))
        if case .compact(let c) = e { XCTAssertTrue(c.retry); XCTAssertEqual(c.sound.name, "Basso") }
    }

    func testBrowserDone() throws {
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","outcome":"done","tools":["browser_task"]"#),
                       .compact(CompactResult(kind: .done, line: "Done · Browser task done")))
    }

    func testFolderOpenAndPointerSteps() throws {
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","tools":["finder_open"]"#),
                       .compact(CompactResult(kind: .done, line: "Done · Opened")))
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","tools":["point_at"]"#),
                       .compact(CompactResult(kind: .done, line: "Done · Steps shown")))
    }

    func testNeedsYouIsCompactWithRetry() throws {
        let e = try end(#""status":"ok","reason":"needs_selection""#)
        XCTAssertEqual(e, .compact(CompactResult(kind: .needsYou, line: "Needs you: Circle the thing you mean")))
        if case .compact(let c) = e { XCTAssertTrue(c.retry) }
    }

    func testPlainQuestionGoesToPanel() throws {
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","final_text":"391""#), .answerPanel)
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","tools":["evidence_search","see_region","open_source","reach_now"]"#), .answerPanel)
        XCTAssertEqual(try end(#""status":"failed","reason":"model error""#), .answerPanel)
    }

    func testStoppedIsQuiet() throws {
        XCTAssertEqual(try end(#""status":"aborted","reason":"user","tools":["browser_task"]"#), .quiet)
    }

    func testShortSentence() {
        XCTAssertEqual(TaskEnd.shortSentence("Circle the thing you mean. Then ask again."), "Circle the thing you mean")
        XCTAssertLessThanOrEqual(TaskEnd.shortSentence(String(repeating: "word ", count: 30)).count, 48)
    }

    func testExpandedWithNoContentShowsInputRow() {
        XCTAssertTrue(PanelContent().showsField)   // expanded && no content => the input row
        XCTAssertFalse(PanelContent(hasAnswer: true).showsField)
        XCTAssertTrue(PanelContent(hasAnswer: true, typing: true).showsField)
        XCTAssertFalse(PanelContent(listening: true).showsField)
        XCTAssertTrue(PanelContent(listening: true).hasBody)
    }

    func testRunEndThenReceiptStillEndsTheRun() throws {
        var st = PanelState(); st.apply(.ready(protocol: 1)); st.submitted(); st.apply(.accepted(taskID: "t"))
        st.apply(.event(taskID: "t", kind: "tool_call_start", body: .object(["name": .string("browser_task")])))
        st.apply(.event(taskID: "t", kind: "run_end", body: .object([:])))
        XCTAssertEqual(st.phase, .working)   // the receipt, which follows run_end, ends the run
        st.apply(try ServerMessage.decode(#"{"type":"receipt","task_id":"t","status":"ok","reason":"completed","outcome":"done","tools":["browser_task"],"system1_asks":1,"tokens":9,"duration_ms":5}"#))
        XCTAssertEqual(st.phase, .done)
        let r = try receipt(#""status":"ok","reason":"completed","outcome":"done","tools":["browser_task"]"#)
        XCTAssertEqual(TaskEnd.of(r, verdict: st.verdict ?? .done), .compact(CompactResult(kind: .done, line: "Done · Browser task done")))
    }

    func testRealToolNames() {
        for t in ["ax_press", "ax_set_text", "chrome_open_url", "finder_reveal", "table_to_note", "shelf_to_note", "point_at"] {
            XCTAssertTrue(TaskEnd.actingTools.contains(t), t)
        }
        for t in ["ax_describe", "chrome_list_tabs", "finder_list", "evidence_search", "open_source", "see_screen", "reach_now"] {
            XCTAssertFalse(TaskEnd.actingTools.contains(t), t)
        }
    }
}
