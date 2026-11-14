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
    DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
        log("voice selftest FAIL: timeout after 60 s (speech authorization: \(SpeechAuth.describe(SFSpeechRecognizer.authorizationStatus())))")
        exit(2)
    }
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

if let prompt = argValue("--relay-selftest") {
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
