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
        let e = try end(#""status":"ok","reason":"completed","write_outcome":"verified","tools":["write_note"]"#)
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
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","tools":["open_folder"]"#),
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
        XCTAssertEqual(try end(#""status":"ok","reason":"completed","tools":["search_docs","see_region"]"#), .answerPanel)
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

    func testRunEndClearsStaleWorking() {
        var st = PanelState(); st.apply(.ready(protocol: 1)); st.submitted(); st.apply(.accepted(taskID: "cli-1"))
        st.apply(.event(taskID: "cli-1", kind: "tool_call_start", body: .object(["name": .string("browser_task")])))
        XCTAssertEqual(st.phase, .working)
        st.apply(.event(taskID: "cli-1", kind: "run_end", body: .object([:])))
        XCTAssertEqual(st.phase, .idle)
        XCTAssertNil(st.tool)
    }
}
