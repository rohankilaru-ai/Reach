import Foundation
import Testing
@testable import ReachCore

@Suite struct WireTests {
    @Test func decodesEventDecisionAndReceipt() throws {
        let ev = try ServerMessage.decode(#"{"type":"event","task_id":"t1","event":{"type":"text_delta","text":"Hi"}}"#)
        guard case .event(let id, let kind, let body) = ev else { Issue.record("not an event"); return }
        #expect(id == "t1" && kind == "text_delta" && body["text"]?.string == "Hi")

        let r = try ServerMessage.decode(#"{"type":"receipt","task_id":"t1","status":"ok","reason":"completed","tools":["reach_now"],"refused":[],"system1_asks":3,"tokens":5376,"est_cost_usd":0.0,"duration_ms":6653,"final_text":"391","ndi_calls":0}"#)
        guard case .receipt(let rec) = r else { Issue.record("not a receipt"); return }
        #expect(rec.line == "completed · Drex asks 3 · 5376 tok · 6653 ms")
    }

    @Test func decodesEvidenceWithMissingFields() throws {
        let m = try ServerMessage.decode(#"{"type":"evidence","task_id":"t1","source":"lease.pdf","quote":"Rent is due","id":"E3","file":"/tmp/lease.pdf","page":4,"level":2,"locator":{"page":4}}"#)
        guard case .evidence(let c) = m else { Issue.record("not evidence"); return }
        #expect(c.title == "lease.pdf · p. 4" && c.id == "E3")
        let bare = try ServerMessage.decode(#"{"type":"evidence","quote":"x"}"#)
        guard case .evidence(let b) = bare else { Issue.record("not evidence"); return }
        #expect(b.page == nil && b.file == nil)
    }

    @Test func unknownTypesDoNotThrow() throws {
        #expect(try ServerMessage.decode(#"{"type":"something_new","x":1}"#) == .unknown(type: "something_new"))
    }

    @Test func drexDecisionReadsEitherAnswerShape() {
        let a = DrexDecision(event: try! JSONDecoder().decode(JSONValue.self, from: Data(#"{"purpose":"supervise","duration_ms":254,"ok":true,"answers":{"mode":{"choice":"advancing","probabilities":{"advancing":0.49}}}}"#.utf8)))
        #expect(a.line == "supervise: mode=advancing (254 ms)")
        let b = DrexDecision(event: try! JSONDecoder().decode(JSONValue.self, from: Data(#"{"purpose":"memory_maps","duration_ms":256,"answers":{"m0":{"noul":0.0075}}}"#.utf8)))
        #expect(b.line == "memory_maps: noul 0.01 (256 ms)")
    }

    @Test func askEncodesSelectionRegion() throws {
        let json = try ClientMessage.ask(text: "explain this", selection: Selection(text: "abc", region: [1, 2, 3, 4], image_path: "/x.png")).json()
        let v = try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
        #expect(v["type"]?.string == "ask")
        #expect(v["selection"]?["region"] == .array([.number(1), .number(2), .number(3), .number(4)]))
        #expect(v["selection"]?["image_path"]?.string == "/x.png")
        #expect(try ClientMessage.abort(taskID: "t9").json() == #"{"task_id":"t9","type":"abort"}"#)
    }
}

@Suite struct StateMachineTests {
    func ev(_ id: String, _ body: String) throws -> ServerMessage {
        try ServerMessage.decode(#"{"type":"event","task_id":""# + id + #"","event":"# + body + "}")
    }
    func receipt(_ id: String, _ status: String, _ reason: String) throws -> ServerMessage {
        try ServerMessage.decode(#"{"type":"receipt","task_id":""# + id + #"","status":""# + status + #"","reason":""# + reason + #"","system1_asks":1,"tokens":10,"est_cost_usd":0,"duration_ms":5}"#)
    }

    @Test func happyPathReachesDone() throws {
        var s = PanelState()
        s.apply(.ready(protocol: 1))
        #expect(s.connected && s.phase == .idle)
        s.submitted()
        #expect(s.phase == .working)
        s.apply(.accepted(taskID: "a"))
        s.apply(try ev("a", #"{"type":"text_delta","text":"39"}"#))
        s.apply(try ev("a", #"{"type":"text_delta","text":"1"}"#))
        s.apply(try ev("other", #"{"type":"text_delta","text":"NOISE"}"#))
        #expect(s.answer == "391")
        for i in 0..<5 { s.apply(try ev("a", #"{"type":"decision","purpose":"p\#(i)","answers":{}}"#)) }
        #expect(s.decisions.map(\.purpose) == ["p2", "p3", "p4"])
        s.apply(.transcript(taskID: "a", role: "assistant", text: "It is 391."))
        #expect(s.answer == "It is 391.")
        s.apply(try receipt("a", "ok", "completed"))
        #expect(s.phase == .done && s.receipt?.reason == "completed" && s.taskID == nil)
    }

    @Test func abortAndFailure() throws {
        var s = PanelState()
        s.apply(.ready(protocol: 1))
        s.submitted(); s.apply(.accepted(taskID: "a"))
        s.apply(try receipt("a", "aborted", "aborted"))
        #expect(s.phase == .idle && s.note == "stopped (aborted)")
        s.submitted(); s.apply(.accepted(taskID: "b"))
        s.apply(try receipt("b", "failed", "error"))
        #expect(s.phase == .failed)
        s.submitted()
        s.disconnected("closed")
        #expect(s.phase == .failed && !s.connected)
    }

    @Test func needsYouClearsOnReady() {
        var s = PanelState()
        s.needsUser("start `reach serve`")
        #expect(s.phase == .needsYou && s.stateLine == "needs you · start `reach serve`")
        s.apply(.ready(protocol: 1))
        #expect(s.phase == .idle)
    }
}

@Suite struct GestureFeedTests {
    @Test func parsesGestureDecisionLines() {
        let ok = GestureDecision(jsonLine: Data(#"{"task_id":"gesture","type":"decision","purpose":"gesture_route","ok":true,"error":null,"answers":{"gesture":{"choice":"click","probabilities":{"idle":0.05,"point":0.04,"click":0.91}}},"accepted":true,"latency_ms":241}"#.utf8))
        #expect(ok?.line == "hand: click 0.91 · 241 ms")
        let low = GestureDecision(jsonLine: Data(#"{"purpose":"gesture_route","ok":true,"answers":{"gesture":{"choice":"idle","probabilities":{"idle":0.5}}},"accepted":false,"latency_ms":250}"#.utf8))
        #expect(low?.line == "hand: idle 0.50 · 250 ms · not confident, mode kept")
        let failed = GestureDecision(jsonLine: Data(#"{"purpose":"gesture_route","ok":false,"error":"HTTP 503","answers":{"gesture":{"choice":"idle","probabilities":{}}},"accepted":false}"#.utf8))
        #expect(failed?.line == "hand: drex failed, local rules decided (HTTP 503)")
        #expect(GestureDecision(jsonLine: Data(#"{"purpose":"supervise","answers":{}}"#.utf8)) == nil)
    }

    @Test func tailReadsOnlyNewCompleteLines() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("drex-\(UUID().uuidString).jsonl")
        defer { try? FileManager.default.removeItem(at: url) }
        let line = { (c: String) in #"{"purpose":"gesture_route","answers":{"gesture":{"choice":"\#(c)","probabilities":{"\#(c)":0.9}}},"latency_ms":200}"# + "\n" }
        try Data(line("idle").utf8).write(to: url)
        let tail = DrexLogTail(url: url)                  // starts at the end: the old line is skipped
        #expect(tail.poll().isEmpty)
        let h = try FileHandle(forWritingTo: url)
        try h.seekToEnd()
        try h.write(contentsOf: Data((line("point") + #"{"purpose":"supervise"}"# + "\n").utf8))
        let half = line("click")
        try h.write(contentsOf: Data(half.prefix(30).utf8))    // a partial line is held back
        #expect(tail.poll().map(\.choice) == ["point"])
        try h.write(contentsOf: Data(half.dropFirst(30).utf8))
        try h.close()
        #expect(tail.poll().map(\.choice) == ["click"])
    }

    @Test func panelStateKeepsLatestGesture() {
        var s = PanelState()
        let g = GestureDecision(jsonLine: Data(#"{"purpose":"gesture_route","answers":{"gesture":{"choice":"point","probabilities":{"point":0.8}}}}"#.utf8))!
        s.gestureDecided(g)
        #expect(s.gesture?.choice == "point" && s.phase == .idle)
    }
}

@Suite struct HandSelectionTests {
    @Test func decodesRelayedSelectionAndHandPointer() throws {
        let m = try ServerMessage.decode(#"{"type":"selection","selection":{"text":"\n  First line\nsecond","region":[10,20,300,40],"image_path":"/r/.reach/captures/a.png","source":"pinch"}}"#)
        guard case .selection(let sel) = m else { Issue.record("not a selection"); return }
        #expect(sel.source == "pinch" && sel.region == [10, 20, 300, 40])
        let p = try ServerMessage.decode(#"{"type":"pointer","task_id":"","x":100.5,"y":200,"state":"pinch","source":"hand"}"#)
        #expect(p == .pointer(taskID: nil, x: 100.5, y: 200, label: nil, state: "pinch"))
        let bad = try ServerMessage.decode(#"{"type":"pointer","task_id":"","y":2}"#)
        #expect(bad == .unknown(type: "pointer"))
    }

    @Test func chipStateFollowsSelectionAndClear() throws {
        var s = PanelState()
        #expect(s.handChip == nil)
        s.apply(try ServerMessage.decode(#"{"type":"selection","selection":{"text":"\n  First line\nsecond","image_path":"/x.png","source":"pinch"}}"#))
        #expect(s.handChip?.line == "First line" && s.handChip?.thumbnailPath == "/x.png")
        #expect(s.handChip?.title == "Selected by hand")
        s.submitted()  // kept for the next ask
        #expect(s.handChip != nil)
        var t = s
        t.clearHandSelection()
        #expect(t.handChip == nil && t != s)
        s.apply(.selection(Selection(region: [0, 0, 480, 160])))
        #expect(s.handChip?.line == "region 480×160 · no text read" && s.handChip?.thumbnailPath == nil)
        s.apply(.selection(Selection()))  // an empty relay clears
        #expect(s.handChip == nil)
    }

    @Test func helloSaysOverlayAndEmptySelectEncodesEmpty() throws {
        let h = try JSONDecoder().decode(JSONValue.self, from: Data(ClientMessage.hello(token: "t").json().utf8))
        #expect(h["role"]?.string == "overlay" && h["token"]?.string == "t")
        let c = try JSONDecoder().decode(JSONValue.self, from: Data(ClientMessage.select(Selection()).json().utf8))
        #expect(c["selection"] == .object([:]))
    }
}
