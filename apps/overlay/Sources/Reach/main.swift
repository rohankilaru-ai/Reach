import AppKit
import ReachCore
import Speech

setvbuf(stdout, nil, _IOLBF, 0)

let args = CommandLine.arguments

func argValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}

let files = RunFiles.resolve(
    arguments: args,
    environment: ProcessInfo.processInfo.environment,
    bundleRepo: Bundle.main.object(forInfoDictionaryKey: "ReachRepo") as? String
)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

if args.contains("--replay") {
    log("replay mode: expecting `reach serve --replay fixtures/events/ask.jsonl` under \(files.reachDir.path)")
}

/// Headless ask: no panel, hotkeys or capture, so no permission prompt can fire. Prints `[reach]` lines, exits.
@MainActor func runSelftest(_ prompt: String) {
    let timeout = Double(argValue("--timeout") ?? "") ?? 90
    let model = OverlayModel(files: files)
    model.onPhase = { phase in
        switch phase {
        case .done:
            log("answer: \(model.state.answer.replacingOccurrences(of: "\n", with: " ").prefix(300))")
            log("decisions: \(model.state.decisions.map(\.line).joined(separator: " | "))")
            log("selftest PASS")
            exit(0)
        case .failed:
            log("selftest FAIL: \(model.state.note ?? "")")
            exit(1)
        default: break
        }
    }
    log("selftest: \(prompt)")
    model.connect(waitSeconds: 10)
    model.submit(prompt)
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
        log("selftest FAIL: timeout after \(Int(timeout)) s in state \(model.state.phase.rawValue)")
        exit(2)
    }
    // A missing server ends in needs-you; give up then.
    DispatchQueue.main.asyncAfter(deadline: .now() + 11) {
        if !model.state.connected { log("selftest FAIL: never connected"); exit(3) }
    }
}

/// Headless voice check: transcribes an audio FILE (no microphone), gates it like the hotkey path does, and with
/// `--selftest-submit` sends it as an ask through `runSelftest`.
@MainActor func runVoiceSelftest(_ path: String) {
    let speech = SpeechInput()
    let submit = args.contains("--selftest-submit")
    let url = URL(fileURLWithPath: path)
    log("voice selftest: \(url.path)")
    let timeout = DispatchWorkItem {
        log("voice selftest FAIL: timeout after 60 s (speech authorization: \(SpeechAuth.describe(SFSpeechRecognizer.authorizationStatus())))")
        exit(2)
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 60, execute: timeout)
    SpeechAuth.request { status in
        log("speech authorization: \(SpeechAuth.describe(status))")
        guard status == .authorized else {
            log("voice selftest BLOCKED: Speech Recognition is not allowed for Reach. Allow it when macOS asks, or in "
                + "System Settings > Privacy & Security > Speech Recognition, then run again.")
            exit(4)
        }
        log("recognizer available: \(speech.available), on-device: \(speech.onDevice ? "yes" : "no (Apple server would be used)")")
        speech.transcribe(file: url) { result in
            switch result {
            case .failure(let e):
                log("voice selftest FAIL: \(e.localizedDescription)")
                exit(1)
            case .success(let heard):
                timeout.cancel()   // the ask (if any) has its own timeout
                log("transcript: \(heard.text)")
                let avg = VoiceGate.average(heard.confidences).map { String(format: "%.2f", $0) } ?? "none"
                log("confidences: \(heard.confidences.map { String(format: "%.2f", $0) }.joined(separator: " ")) (avg \(avg))")
                switch VoiceGate.judge(text: heard.text, confidences: heard.confidences) {
                case .reject(let why):
                    log("gate: not sent (\(why))")
                    log("voice selftest FAIL")
                    exit(1)
                case .send(let text):
                    log("gate: send")
                    if submit { runSelftest(text) } else { log("voice selftest PASS"); exit(0) }
                }
            }
        }
    }
}

/// Headless relay check (no panel, no capture): waits for a hand `selection` relayed by the server, prints the chip
/// state, then asks `prompt` with that selection attached, like a typed ask would. PASS when the ask finishes.
@MainActor func runRelaySelftest(_ prompt: String) {
    let model = OverlayModel(files: files)
    var pointers = 0
    model.onPointer = { _, _, _, byHand in if byHand { pointers += 1 } }
    model.onHandSelection = { _ in
        let chip = model.state.handChip
        log("chip: \(chip?.title ?? "none") · \"\(chip?.line ?? "")\" · thumbnail \(chip?.thumbnailPath ?? "none")"
            + " (exists: \(chip?.thumbnailPath.map { FileManager.default.fileExists(atPath: $0) } ?? false))")
        log("hand pointers received so far: \(pointers)")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { runSelftestWith(model, prompt) }
    }
    log("relay selftest: waiting up to 40 s for a hand selection")
    model.connect(waitSeconds: 10)
    DispatchQueue.main.asyncAfter(deadline: .now() + 40) {
        if model.state.handSelection == nil { log("relay selftest FAIL: no selection relayed"); exit(5) }
    }
}

@MainActor func runSelftestWith(_ model: OverlayModel, _ prompt: String) {
    model.onPhase = { phase in
        switch phase {
        case .done:
            log("answer: \(model.state.answer.replacingOccurrences(of: "\n", with: " ").prefix(200))")
            log("chip after ask: \(model.state.handChip?.line ?? "none") (kept for the next ask)")
            log("receipt selection_source: see .reach/logs/relay.jsonl")
            log("relay selftest PASS")
            exit(0)
        case .failed:
            log("relay selftest FAIL: \(model.state.note ?? "")")
            exit(1)
        default: break
        }
    }
    model.submit(prompt)
    DispatchQueue.main.asyncAfter(deadline: .now() + 30) { log("relay selftest FAIL: ask timeout"); exit(2) }
}

/// The circle-select state machine on synthetic strokes: no windows, no capture, no input events.
@MainActor func runLassoSelftest() -> Int32 {
    func loop(_ cx: CGFloat, _ cy: CGFloat, _ r: CGFloat, n: Int = 300) -> [CGPoint] {
        (0..<n).map { i in let a = CGFloat(i) / CGFloat(n) * 2 * .pi; return CGPoint(x: cx + r * cos(a), y: cy + r * sin(a)) }
    }
    var ok = true
    for (name, pts, cancel) in [("circle r=90", loop(600, 500, 90), false), ("tiny r=12", loop(600, 500, 12), false),
                                ("esc mid-stroke", loop(600, 500, 90), true)] {
        var rec = LassoRecorder()
        var states = [rec.state.rawValue]
        rec.begin(pts[0]); states.append(rec.state.rawValue)
        pts.dropFirst().forEach { rec.add($0) }
        let outcome: LassoRecorder.Outcome
        if cancel { rec.cancel(); outcome = .cancelled } else { outcome = rec.end() }
        states.append(rec.state.rawValue)
        var detail = ""
        if case .closed(let path, let box) = outcome {
            let top: CGFloat = 982
            detail = " path \(pts.count)->\(path.count) pts, region \(Lasso.region(box, primaryTop: top).map { Int($0) }), "
                + "centre inside \(Lasso.contains(path, CGPoint(x: 600, y: 500)))"
            ok = ok && path.count <= Lasso.maxPoints
        }
        let want: LassoRecorder.State = cancel ? .cancelled : (name.hasPrefix("tiny") ? .tooSmall : .closed)
        ok = ok && rec.state == want
        log("lasso selftest \(name): \(states.joined(separator: " -> "))\(detail)")
    }
    var k = TapOrHold()
    let t = k.down(at: 0); let tap = k.up(at: 0.1); _ = k.holdCheck(token: t, at: 0.25)
    let t2 = k.down(at: 1); let hold = k.holdCheck(token: t2, at: 1.25); let end = k.up(at: 3)
    log("key selftest: tap 0.1 s -> \(tap), hold -> \(hold) ... \(end)")
    ok = ok && tap == .tap && hold == .startHold && end == .endHold
    log("lasso selftest \(ok ? "PASS" : "FAIL")")
    return ok ? 0 : 1
}


/// The hub's gesture, verdicts, hover and zoom driven headlessly (no windows, no input events, no network). Each
/// scenario feeds the same ReachCore machines the app runs and prints one PASS/FAIL line.
@MainActor func runHubSelftest() -> Int32 {
    var allOK = true
    func line(_ name: String, _ ok: Bool, _ detail: String) {
        allOK = allOK && ok
        log("hub selftest \(ok ? "PASS" : "FAIL") \(name): \(detail)")
    }
    let circle = Selection(text: "f(x) = x^2 + 1", region: [600, 300, 180, 120], image_path: "/tmp/circle-1.png",
                           path: [[600, 300], [780, 300], [780, 420], [600, 420]])
    /// What the app does with a resolution: asks are encoded exactly as they would be sent.
    func run(_ steps: (inout HubGesture, inout [HubGesture.Resolution]) -> Void) -> (asks: [String], other: [HubGesture.Resolution]) {
        var g = HubGesture(); var out: [HubGesture.Resolution] = []
        steps(&g, &out)
        var asks: [String] = [], other: [HubGesture.Resolution] = []
        for r in out {
            if case .ask(let t, let sel) = r { asks.append((try? ClientMessage.ask(text: t, selection: sel).json()) ?? "?") }
            else { other.append(r) }
        }
        return (asks, other)
    }
    func push(_ r: HubGesture.Resolution?, _ out: inout [HubGesture.Resolution]) { if let r { out.append(r) } }

    var r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 1); push(g.circleCaptured(gen: 1, circle), &out)
        g.released(); push(g.heard(.send("explain this")), &out)
    }
    line("hold -> circle -> release", r.asks.count == 1 && r.asks[0].contains("\"explain this\"") && r.asks[0].contains("\"path\"")
         && r.asks[0].contains("circle-1.png") && r.asks[0].contains("x^2"), "\(r.asks.count) ask: \(r.asks.first ?? "none")")

    r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 2); g.released(); push(g.heard(.send("what is this")), &out)
        push(g.circleCaptured(gen: 2, circle), &out)
    }
    line("release before capture finishes", r.asks.count == 1 && r.asks[0].contains("\"path\""), "\(r.asks.count) ask, after the capture")

    r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 3); push(g.circleCaptured(gen: 3, circle), &out)
        g.released(); push(g.heard(.reject("didn't catch that")), &out)
    }
    line("circle without speech", r.asks.isEmpty && r.other == [.typeWithChip(circle)], "no ask; typing box opens with the circle chip")

    r = run { g, out in _ = g.holdStarted(); g.released(); push(g.heard(.send("what time is it in Tokyo")), &out) }
    line("speech without circle", r.asks.count == 1 && !r.asks[0].contains("selection"), "plain voice ask: \(r.asks.first ?? "none")")

    r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 4); push(g.circleCaptured(gen: 4, circle), &out); g.released()
        push(g.heard(.send("explain this")), &out)
        _ = g.holdStarted(); g.released(); push(g.heard(.send("thanks, and the next step?")), &out)
    }
    line("next hold has no stale circle", r.asks.count == 2 && r.asks[0].contains("\"path\"") && !r.asks[1].contains("selection"),
         "2 asks, only the first carries the circle")

    r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 5); push(g.cancel(), &out)
        push(g.circleCaptured(gen: 5, circle), &out); push(g.heard(.send("late words")), &out)
    }
    line("Esc cancels everything", r.asks.isEmpty && r.other == [.cancelled], "no ask; late capture and transcript ignored")

    r = run { g, out in
        _ = g.holdStarted(); g.circleDrawn(gen: 6); push(g.circleCaptured(gen: 6, circle), &out); g.released()
        push(g.heard(.send("Zoom.")), &out)
    }
    let s1 = ZoomLens.scrolled(3.8, deltaY: 40), s2 = ZoomLens.pinched(2.1, magnification: -0.6)
    line("zoom chip / spoken zoom", r.asks.isEmpty && r.other == [.zoom(circle)] && s1 == 4 && s2 == 2,
         "lens opens locally, no ask; scale clamps to \(s1)x / \(s2)x")

    // hover: mouse moves up to the notch, rests, then leaves
    var hover = Hover.Tracker()
    let anchor = CGPoint(x: 756, y: 939), pill = CGSize(width: 104, height: 36)
    let hot = Hover.hotZone(anchor: anchor, pill: pill, screenTop: 982)
    let panelFrame = CGRect(x: 536, y: 560, width: 440, height: 379)
    var expanded = false, trace: [String] = []
    let path: [(Double, CGPoint)] = [(0, CGPoint(x: 756, y: 500)), (0.1, CGPoint(x: 760, y: 970)), (0.3, CGPoint(x: 760, y: 970)),
                                     (1.0, CGPoint(x: 700, y: 700)), (2.0, CGPoint(x: 100, y: 100)), (2.7, CGPoint(x: 100, y: 100))]
    for (t, m) in path {
        switch hover.update(mouse: m, now: t, expanded: expanded, hot: hot, panel: panelFrame, busy: false) {
        case .expand: expanded = true; trace.append("expand@\(t)")
        case .collapse: expanded = false; trace.append("collapse@\(t)")
        case .none: break
        }
    }
    line("hover expand / collapse", trace == ["expand@0.3", "collapse@2.7"], trace.joined(separator: " "))
    hover.collapsed()   // e.g. Esc with the mouse parked at the notch: no instant reopen
    let parked = (0..<10).map { hover.update(mouse: CGPoint(x: 760, y: 970), now: 3 + Double($0) * 0.1, expanded: false, hot: hot,
                                             panel: panelFrame, busy: false) }
    line("hover stays closed after Esc until the mouse leaves", !parked.contains(.expand), "no reopen while parked at the notch")

    // receipts: the verdict is read from status, reason, write_outcome and outcome
    func verdict(_ fields: String) -> (Verdict?, Phase) {
        var st = PanelState(); st.apply(.ready(protocol: 1)); st.submitted(); st.apply(.accepted(taskID: "t"))
        if let m = try? ServerMessage.decode(#"{"type":"receipt","task_id":"t","system1_asks":1,"tokens":9,"duration_ms":5,"# + fields + "}") {
            st.apply(m)
        }
        return (st.verdict, st.phase)
    }
    let failed = verdict(#""status":"failed","reason":"note write failed","write_outcome":"failed","final_text":"Saved the note!""#)
    line("failed write shows Failed", failed.1 == .failed && failed.0?.title.hasPrefix("Failed") == true, failed.0?.title ?? "none")
    let unv = verdict(#""status":"ok","reason":"completed","write_outcome":"unverified""#)
    line("unverified write is not Done", unv.1 == .failed && unv.0?.title.hasPrefix("Unverified") == true, unv.0?.title ?? "none")
    let needs = verdict(#""status":"ok","reason":"needs_selection""#)
    line("needs_selection asks for a circle", needs.1 == .needsYou && needs.0?.title == "Needs you: Circle the thing you mean",
         needs.0?.title ?? "none")
    let ok = verdict(#""status":"ok","reason":"completed","write_outcome":"verified""#)
    line("verified write is Done", ok.1 == .done && ok.0?.title == "Done", ok.0?.title ?? "none")

    let spoken = SpokenText.clean("**Rent** is due on the 1st [E3]. See [the lease](file:///l.pdf).")
    line("spoken text", spoken == "Rent is due on the 1st. See the lease.", "\"\(spoken)\"")

    // calm panel: one progress line, one sentence + one action, short pointer labels, lens never covers
    let prog = ProgressLine.of(tool: "see_region")
    line("progress is one line", prog == "Looking at your screen…" && ProgressLine.of(tool: "browser_task") == "Using the browser…", prog)
    let plain = PlainState.of(verdict: .failed("the note write failed; the answer text claims success"))
    line("failure is one sentence + one action", plain == PlainState(sentence: "The note wasn't saved.", action: .retry),
         "\(plain?.sentence ?? "none") [\(plain?.actionLabel ?? "")]")
    let shown = PlainText.display("**Rent** is due [E3].\n- pay online")
    line("answer shows as plain text", shown == "Rent is due.\npay online", shown.replacingOccurrences(of: "\n", with: " / "))
    let label = PointerLabel.short("Click the blue Save button in the lower right corner of the window") ?? ""
    line("pointer label is short", label.count <= 30, "\"\(label)\"")
    let lensO = LensPlacement.place(size: CGSize(width: 400, height: 300), avoid: [panelFrame, CGRect(x: 600, y: 300, width: 180, height: 120)],
                                    near: CGRect(x: 600, y: 300, width: 180, height: 120), visible: CGRect(x: 0, y: 0, width: 1512, height: 945))
    let lensR = CGRect(origin: lensO, size: CGSize(width: 400, height: 300))
    line("lens covers neither panel nor circle", !lensR.intersects(panelFrame) && !lensR.intersects(CGRect(x: 600, y: 300, width: 180, height: 120)),
         "lens at \(Int(lensO.x)),\(Int(lensO.y))")

    // quiet status: tool runs end as one line in the pill, questions in the answer panel
    func ended(_ fields: String) -> (TaskEnd, PanelState) {
        var st = PanelState(); st.apply(.ready(protocol: 1)); st.submitted(); st.apply(.accepted(taskID: "t"))
        guard let m = try? ServerMessage.decode(#"{"type":"receipt","task_id":"t","system1_asks":1,"tokens":9,"duration_ms":5,"# + fields + "}"),
              case .receipt(let rc) = m else { return (.quiet, st) }
        st.apply(m)
        return (TaskEnd.of(rc, verdict: st.verdict ?? .done), st)
    }
    func compactLine(_ e: TaskEnd) -> String {
        switch e {
        case .compact(let c): return "pill \(c.kind.rawValue): \"\(c.line)\"\(c.retry ? " [Retry]" : "") sound \(c.sound.name)"
        case .answerPanel: return "answer panel"
        case .quiet: return "quiet"
        }
    }
    let note = ended(#""status":"ok","reason":"completed","write_outcome":"verified","tools":["note_append"]"#)
    line("note saved -> pill Done, no Retry", note.0 == .compact(CompactResult(kind: .done, line: "Done · Note saved")), compactLine(note.0))
    let web = ended(#""status":"ok","reason":"completed","outcome":"not_done","tools":["browser_task"]"#)
    line("browser not done -> pill Couldn't finish + Retry", web.0 == .compact(CompactResult(kind: .failed, line: "Couldn't finish")),
         compactLine(web.0))
    let need = ended(#""status":"ok","reason":"needs_selection","tools":["see_region"]"#)
    line("needs you -> amber pill + Retry", need.0 == .compact(CompactResult(kind: .needsYou, line: "Needs you: Circle the thing you mean")),
         compactLine(need.0))
    let q = ended(#""status":"ok","reason":"completed","tools":["evidence_search","see_screen"],"final_text":"Rent is due on the 1st.""#)
    line("plain question -> answer panel, run ended", q.0 == .answerPanel && q.1.phase == .done && PlainState.of(verdict: .done) == nil,
         "\(compactLine(q.0)); phase \(q.1.phase.rawValue), no Try again next to Done")
    let stop = ended(#""status":"aborted","reason":"user","tools":["browser_task"]"#)
    line("stopped run -> nothing shown", stop.0 == .quiet && stop.1.phase != .working, compactLine(stop.0))

    // the expanded card is never empty; a run that ends elsewhere never leaves a stale "working"
    let empty = PanelContent()
    line("expanded with nothing to show -> input row", empty.showsField && !PanelContent(hasAnswer: true).showsField,
         "no answer, no state: 'Ask Reach…' row shows")
    var cli = PanelState(); cli.apply(.ready(protocol: 1)); cli.submitted(); cli.apply(.accepted(taskID: "cli-7"))
    cli.apply(.event(taskID: "cli-7", kind: "tool_call_start", body: .object(["name": .string("browser_task")])))
    cli.apply(.event(taskID: "cli-7", kind: "run_end", body: .object([:])))
    if let m = try? ServerMessage.decode(#"{"type":"receipt","task_id":"cli-7","status":"ok","reason":"completed","outcome":"done","tools":["browser_task"],"system1_asks":1,"tokens":9,"duration_ms":5}"#) { cli.apply(m) }
    line("receipt after run_end ends working", cli.phase == .done && cli.taskID == nil, "phase \(cli.phase.rawValue), no stale working")
    let auto = PillVisibility.shouldShow(mode: .auto, expanded: false, busy: false)
    line("Auto mode idle -> pill hidden", !auto, "nothing drawn when idle")

    log("hub selftest \(allOK ? "PASS" : "FAIL")")
    return allOK ? 0 : 1
}

/// TTS -> STT loop through the backend, no microphone and no playback: synthesize `sentence` (saved under
/// .reach/audio/), send it back through `stt`, print the transcript and timings, compare Apple on-device on the same
/// audio when Speech Recognition is already allowed (never asks), then delete the file.
@MainActor func runCloudVoiceSelftest(_ sentence: String) {
    let client = ReachClient()
    var t0 = Date(), ttsMs = 0
    var file: URL?
    func done(_ code: Int32) {
        if let f = file { try? FileManager.default.removeItem(at: f) }
        exit(code)
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 30) { log("cloud voice selftest FAIL: timeout"); done(2) }
    client.onMessage = { msg in
        MainActor.assumeIsolated {
            switch msg {
            case .ready:
                t0 = Date()
                client.send(.tts(text: sentence, id: "selftest-tts"))
            case .ttsAudio(let path, let ms, let err, _):
                if let err { log("cloud voice selftest FAIL: tts \(err)"); done(1); return }
                guard CloudVoice.isAudioPath(path, reachDir: files.reachDir) else { log("cloud voice selftest FAIL: path"); done(1); return }
                file = URL(fileURLWithPath: path)
                ttsMs = Int(Date().timeIntervalSince(t0) * 1000)
                log("cloud voice selftest tts: \(ms) ms server, \(ttsMs) ms round trip, saved \(path)")
                t0 = Date()
                client.send(.stt(audioPath: path, id: "selftest-stt"))
            case .sttResult(let text, let ms, let err, _):
                let rt = Int(Date().timeIntervalSince(t0) * 1000)
                if let err { log("cloud voice selftest FAIL: stt \(err)"); done(1); return }
                log("cloud voice selftest stt (whisper): \"\(text)\" \(ms) ms server, \(rt) ms round trip")
                guard SFSpeechRecognizer.authorizationStatus() == .authorized, let f = file else {
                    log("cloud voice selftest: on-device comparison skipped (Speech Recognition not allowed for this binary)")
                    log("cloud voice selftest PASS"); done(text.isEmpty ? 1 : 0); return
                }
                let speech = SpeechInput()
                let a0 = Date()
                speech.transcribe(file: f) { r in
                    let ams = Int(Date().timeIntervalSince(a0) * 1000)
                    switch r {
                    case .success(let h): log("cloud voice selftest stt (on-device): \"\(h.text)\" \(ams) ms")
                    case .failure(let e): log("cloud voice selftest on-device failed: \(e.localizedDescription)")
                    }
                    log("cloud voice selftest PASS"); done(text.isEmpty ? 1 : 0)
                }
            case .error(let m, _): log("cloud voice selftest server error: \(m)")
            default: break
            }
        }
    }
    guard let info = files.read() else { log("cloud voice selftest FAIL: no backend run files under \(files.reachDir.path)"); exit(3) }
    client.connect(port: info.port, token: info.token)
}

/// Headless permission status: reads only (no prompts, no ScreenCaptureKit), prints one line per permission, exits.
@MainActor func runPermissionsSelftest() {
    Task { @MainActor in
        let s = await PermissionProbe.read(probeCapture: false)
        for k in PermissionKind.allCases {
            let st = s[k] ?? .notYet
            let note = k == .screenRecording ? " (preflight only; the app also tries a 1x1 capture)" : ""
            log("permissions selftest \(k.rawValue) [\(k.need.rawValue)]: \(st.rawValue)\(note)")
        }
        let missing = PermissionRules.missingRequired(s)
        log("permissions selftest required missing: \(missing.isEmpty ? "none" : missing.map(\.rawValue).joined(separator: ", "))")
        log("permissions selftest done")
        exit(0)
    }
}

/// Hands-free logic, headless: fake recognizer text and fake `addressed` replies. No mic, no network, no prompts.
@MainActor func runHandsFreeSelftest() -> Int32 {
    var ok = true
    func check(_ name: String, _ pass: Bool, _ detail: String = "") {
        log("handsfree selftest \(pass ? "PASS" : "FAIL") \(name)\(detail.isEmpty ? "" : ": \(detail)")")
        ok = ok && pass
    }
    var seg = Segmenter()
    _ = seg.feed("hey reach scroll", now: 0)
    _ = seg.feed("hey reach scroll down", now: 0.4)
    let early = seg.tick(now: 1.0)
    let s1 = seg.tick(now: 1.4)
    check("segment by pause", early == nil && s1?.ask == "scroll down" && s1?.wakeMatched == true, "\(s1?.ask ?? "nil")")
    _ = seg.feed("okay", now: 2)
    check("short side talk dropped", seg.tick(now: 3) == nil)
    let s2 = seg.feed("can you pass the salt.", now: 4)
    check("sentence end cuts", s2?.wakeMatched == false && s2?.ask == "can you pass the salt.")
    var p = PendingUtterance()
    p.sent(text: "Reach, what is this", ask: "what is this", at: 10)
    let d1 = p.reply(AddressedReply(text: "Reach, what is this", pAddressed: 0.99, addressed: true), at: 10.3)
    check("addressed -> ask", d1 == .ask("what is this", chip: nil))
    p.sent(text: "can you pass the salt.", ask: "can you pass the salt.", at: 20)
    let d2 = p.reply(AddressedReply(text: "can you pass the salt.", pAddressed: 0.1, addressed: false), at: 20.2)
    check("not addressed -> ignore", d2 == .ignore && p.pending == nil)
    p.sent(text: "what is this thing", ask: "what is this thing", at: 30)
    check("timeout 1.5 s -> ignore", p.expire(at: 31.0) == nil && p.expire(at: 31.6) == .ignore)
    p.sent(text: "Reach, stop", ask: "stop", at: 40)
    let d3 = p.reply(AddressedReply(text: "Reach, stop", addressed: true, command: "stop"), at: 40.2)
    check("stop -> local abort", d3 == .localStop(chip: "stop"))
    check("stop listening is local", WakeWord.isStopListening("Reach, stop listening."))
    var echo = EchoGate()
    echo.speakingChanged(true, now: 50)
    let a = echo.mayListen(now: 50.2)
    echo.speakingChanged(false, now: 51)
    check("echo pause + 0.5 s resume", !a && !echo.mayListen(now: 51.3) && echo.mayListen(now: 51.6))
    var pinch = PinchSignal()
    let pp = pinch.update(state: "pinch", now: 60), pr = pinch.update(state: "hover", now: 60.5)
    check("pinch press/release", pp == .press && pr == .release)
    var t = HandsFreeToggle()
    check("starts off", !t.active(now: 0))
    t.toggle(); t.pause(minutes: 5, now: 0)
    check("pause 5 min", t.on && !t.active(now: 100) && t.active(now: 301))
    log("handsfree selftest \(ok ? "PASS" : "FAIL")")
    return ok ? 0 : 1
}

if args.contains("--dictation-selftest") {
    exit(MainActor.assumeIsolated { runDictationSelftest() })
} else if args.contains("--handsfree-selftest") {
    exit(MainActor.assumeIsolated { runHandsFreeSelftest() })
} else if args.contains("--permissions-selftest") {
    MainActor.assumeIsolated { runPermissionsSelftest() }
    app.run()
} else if let sentence = argValue("--cloud-voice-selftest") {
    MainActor.assumeIsolated { runCloudVoiceSelftest(sentence) }
    app.run()
} else if args.contains("--hub-selftest") {
    exit(MainActor.assumeIsolated { runHubSelftest() })
} else if args.contains("--lasso-selftest") {
    exit(MainActor.assumeIsolated { runLassoSelftest() })
} else if let prompt = argValue("--relay-selftest") {
    MainActor.assumeIsolated { runRelaySelftest(prompt) }
    app.run()
} else if let audio = argValue("--voice-selftest") {
    MainActor.assumeIsolated { runVoiceSelftest(audio) }
    app.run()
} else if let prompt = argValue("--selftest") {
    MainActor.assumeIsolated { runSelftest(prompt) }
    app.run()
} else if args.contains("--panel-selftest") {
    // Real windows, no input events: pill -> expanded -> pill, logging frames, then exit.
    let delegate = MainActor.assumeIsolated { AppController(files: files) }
    app.delegate = delegate
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
        MainActor.assumeIsolated { delegate.expand(focus: false, why: "selftest") }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
        MainActor.assumeIsolated { delegate.collapse(why: "selftest") }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 4.5) { log("panel selftest done"); exit(0) }
    app.run()
} else {
    let delegate = MainActor.assumeIsolated { AppController(files: files) }
    app.delegate = delegate
    app.run()
}
