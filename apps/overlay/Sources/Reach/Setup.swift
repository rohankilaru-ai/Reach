import AppKit
import ApplicationServices
import AVFoundation
import ReachCore
import ScreenCaptureKit
import Speech
import SwiftUI

/// Reads and requests the macOS permissions Reach uses. `read` never raises a prompt; `request` does.
@MainActor
enum PermissionProbe {
    /// Set once Reach has asked for Screen Recording (prompt or Settings) during this run.
    static var askedScreenThisRun = false
    private static var captureProbe: (at: Date, ok: Bool)?

    /// Status without prompts. `probeCapture` allows one 1x1 ScreenCaptureKit capture when preflight says no
    /// (cached for 5 s); headless selftests pass false.
    static func read(probeCapture: Bool) async -> [PermissionKind: PermissionStatus] {
        var s: [PermissionKind: PermissionStatus] = [:]
        let pre = CGPreflightScreenCaptureAccess()
        var works: Bool?
        if !pre, probeCapture {
            if let c = captureProbe, Date().timeIntervalSince(c.at) < 5 { works = c.ok }
            else { let ok = await tinyCaptureWorks(); captureProbe = (Date(), ok); works = ok }
        }
        s[.screenRecording] = ScreenRecordingCheck.decide(preflight: pre, captureWorks: works, askedThisRun: askedScreenThisRun)
        s[.microphone] = av(AVCaptureDevice.authorizationStatus(for: .audio))
        s[.camera] = av(AVCaptureDevice.authorizationStatus(for: .video))
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: s[.speechRecognition] = .granted
        case .denied, .restricted: s[.speechRecognition] = .denied
        default: s[.speechRecognition] = .notYet
        }
        s[.accessibility] = AXIsProcessTrusted() ? .granted : .notYet
        s[.automation] = .informational
        return s
    }

    private static func av(_ a: AVAuthorizationStatus) -> PermissionStatus {
        switch a {
        case .authorized: return .granted
        case .denied, .restricted: return .denied
        default: return .notYet
        }
    }

    /// One real 1x1 capture of the main display: if it works, Screen Recording is usable whatever preflight says.
    static func tinyCaptureWorks() async -> Bool {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let mainID = CGMainDisplayID()
            guard let d = content.displays.first(where: { $0.displayID == mainID }) ?? content.displays.first else { return false }
            let config = SCStreamConfiguration()
            config.sourceRect = CGRect(x: 0, y: 0, width: 1, height: 1)
            config.width = 1
            config.height = 1
            config.showsCursor = false
            let img = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(display: d, excludingWindows: []),
                                                                 configuration: config)
            return img.width >= 1
        } catch {
            return false
        }
    }

    /// Raises the native prompt and, where macOS only allows switching it in Settings, opens the exact pane too.
    static func request(_ kind: PermissionKind, current: PermissionStatus) {
        switch kind {
        case .screenRecording:
            askedScreenThisRun = true
            captureProbe = nil
            _ = CGRequestScreenCaptureAccess()
        case .microphone: AVCaptureDevice.requestAccess(for: .audio) { _ in }
        case .camera: AVCaptureDevice.requestAccess(for: .video) { _ in }
        case .speechRecognition: SFSpeechRecognizer.requestAuthorization { _ in }
        case .accessibility:
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
        case .automation: break
        }
        if PermissionRules.opensSettings(kind, current), let url = URL(string: kind.settingsURL) {
            NSWorkspace.shared.open(url)
        }
        log("setup: grant \(kind.rawValue) (was \(current.rawValue))")
    }

    /// Opens this app's own bundle again and quits, so a fresh process picks up a new Screen Recording grant.
    static var canRelaunch: Bool { Bundle.main.bundleURL.pathExtension == "app" }

    static func relaunch() {
        guard canRelaunch else { log("setup: relaunch needs the .app bundle"); return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: cfg) { _, err in
            DispatchQueue.main.async {
                if let err { log("setup: relaunch failed: \(err.localizedDescription)"); return }
                NSApp.terminate(nil)
            }
        }
    }
}

// MARK: - "Set up Reach" checklist

@MainActor
final class SetupModel: ObservableObject {
    @Published var statuses: [PermissionKind: PermissionStatus] = [:]
    @Published var allSet = false
    var onAllSet: (() -> Void)?
    private var tracker = AllSetTracker()
    private var reading = false

    func refresh() {
        guard !reading else { return }
        reading = true
        Task { @MainActor in
            let s = await PermissionProbe.read(probeCapture: true)
            reading = false
            statuses = s
            if tracker.update(s) { allSet = true; onAllSet?() }
        }
    }
}

struct SetupView: View {
    @ObservedObject var model: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Set up Reach").font(.system(size: 18, weight: .semibold))
            Text("Reach needs the first three. The rest are optional.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
            ForEach(PermissionKind.allCases, id: \.self) { kind in row(kind) }
            if model.allSet {
                Label("All set", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    .font(.system(size: 14, weight: .semibold))
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    @ViewBuilder private func row(_ kind: PermissionKind) -> some View {
        let st = model.statuses[kind] ?? .notYet
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(st.isGranted ? Color.green : Color.gray.opacity(0.5)).frame(width: 10, height: 10).padding(.top, 4)
            VStack(alignment: .leading, spacing: 3) {
                Text(kind.title).font(.system(size: 13, weight: .medium))
                Text(kind.why).font(.system(size: 11)).foregroundStyle(.secondary)
                if kind == .screenRecording, st == .notYet || st == .denied || st == .needsRelaunch {
                    if st == .needsRelaunch {
                        Text(ScreenRecordingCheck.relaunchSentence).font(.system(size: 11))
                    } else {
                        Text(ScreenRecordingCheck.missingSentence).font(.system(size: 11))
                    }
                    Text(ScreenRecordingCheck.missingHint).font(.system(size: 11)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            if kind.need == .informational {
                Text("Asked the first time it is used").font(.system(size: 11)).foregroundStyle(.secondary)
            } else if !st.isGranted {
                VStack(alignment: .trailing, spacing: 4) {
                    Button("Grant") { PermissionProbe.request(kind, current: st); model.refresh() }
                    if kind == .screenRecording, st == .needsRelaunch, PermissionProbe.canRelaunch {
                        Button("Relaunch Reach") { PermissionProbe.relaunch() }
                    }
                }
            }
        }
    }
}

/// The checklist window. Polls every second while open; closes itself 1.5 s after the required set turns green.
@MainActor
final class SetupWindow {
    private var window: NSWindow?
    private var timer: Timer?
    let model = SetupModel()

    var isOpen: Bool { window?.isVisible == true }

    func open() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 420), styleMask: [.titled, .closable],
                             backing: .buffered, defer: false)
            w.title = "Set up Reach"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: SetupView(model: model))
            w.center()
            window = w
        }
        model.allSet = false
        model.onAllSet = { [weak self] in
            log("setup: all required permissions granted")
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self?.close() }
        }
        model.refresh()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.window?.isVisible != true { self.timer?.invalidate(); self.timer = nil; return }
                self.model.refresh()
            }
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        log("setup: checklist open")
    }

    func close() {
        timer?.invalidate(); timer = nil
        window?.orderOut(nil)
    }
}
