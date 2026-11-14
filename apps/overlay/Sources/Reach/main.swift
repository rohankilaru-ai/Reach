import AppKit
import ReachCore

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

if let prompt = argValue("--selftest") {
    // Headless: no panel, hotkeys or capture, so no permission prompt can fire.
    let timeout = Double(argValue("--timeout") ?? "") ?? 90
    MainActor.assumeIsolated {
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
    app.run()
} else {
    let delegate = MainActor.assumeIsolated { AppController(files: files) }
    app.delegate = delegate
    app.run()
}
