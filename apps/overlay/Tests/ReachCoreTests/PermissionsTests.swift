import Foundation
import Testing
@testable import ReachCore

struct PermissionsTests {
    func all(_ st: PermissionStatus) -> [PermissionKind: PermissionStatus] {
        Dictionary(uniqueKeysWithValues: PermissionKind.allCases.map { ($0, st) })
    }

    @Test func requiredAndOptional() {
        #expect(PermissionKind.allCases.filter { $0.need == .required } == [.screenRecording, .microphone, .speechRecognition])
        #expect(PermissionKind.accessibility.need == .optional && PermissionKind.camera.need == .optional)
        #expect(PermissionKind.automation.need == .informational)
    }

    @Test func optionalNeverBlocks() {
        var s = all(.notYet)
        s[.screenRecording] = .granted; s[.microphone] = .granted; s[.speechRecognition] = .granted
        #expect(PermissionRules.allRequiredGranted(s))
        s[.microphone] = .denied
        #expect(!PermissionRules.allRequiredGranted(s))
        #expect(PermissionRules.missingRequired(s) == [.microphone])
    }

    @Test func screenRecordingRobustCheck() {
        #expect(ScreenRecordingCheck.decide(preflight: true, captureWorks: nil, askedThisRun: false) == .granted)
        #expect(ScreenRecordingCheck.decide(preflight: false, captureWorks: true, askedThisRun: false) == .granted)
        #expect(ScreenRecordingCheck.decide(preflight: false, captureWorks: false, askedThisRun: false) == .notYet)
        #expect(ScreenRecordingCheck.decide(preflight: false, captureWorks: false, askedThisRun: true) == .needsRelaunch)
    }

    @Test func grantOpensSettingsWhenOnlySettingsCan() {
        #expect(PermissionRules.opensSettings(.screenRecording, .notYet))
        #expect(PermissionRules.opensSettings(.accessibility, .notYet))
        #expect(!PermissionRules.opensSettings(.microphone, .notYet))
        #expect(PermissionRules.opensSettings(.microphone, .denied))
        #expect(PermissionKind.speechRecognition.settingsURL.hasSuffix("Privacy_SpeechRecognition"))
    }

    @Test func allSetFiresOnTheEdgeOnly() {
        var t = AllSetTracker()
        var s = all(.notYet)
        let a = t.update(s)
        s[.screenRecording] = .granted; s[.microphone] = .granted
        let b = t.update(s)
        s[.speechRecognition] = .granted
        let c = t.update(s)
        let d = t.update(s)   // once
        var fresh = AllSetTracker()
        let e = fresh.update(s)   // already green when opened: no auto-close
        #expect(!a && !b && c && !d && !e)
    }

    @Test func screenNoteIsOneSentenceWithFix() {
        let p = PlainState.of(note: "allow Screen Recording for Reach")
        #expect(p.action == .openScreenSettings && p.actionLabel == "Fix")
        #expect(p.detail?.contains("minus button") == true)
        #expect(PlainState.of(note: "relaunch Reach").action == .relaunch)
    }

    @Test func pillVisibility() {
        #expect(!PillVisibility.shouldShow(mode: .auto, expanded: false, busy: false))
        #expect(PillVisibility.shouldShow(mode: .auto, expanded: false, busy: true))
        #expect(PillVisibility.shouldShow(mode: .auto, expanded: true, busy: false))
        #expect(PillVisibility.shouldShow(mode: .always, expanded: false, busy: false))
        #expect(!PillVisibility.shouldShow(mode: .hidden, expanded: false, busy: false))
        #expect(!PillMode.hidden.hoverSummons && PillMode.auto.hoverSummons)
        #expect(PillMode.load(UserDefaults(suiteName: "reach-test-\(UUID())")!) == .auto)
    }
}
