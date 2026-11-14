import Foundation

/// Every macOS permission the "Set up Reach" checklist shows, in display order.
public enum PermissionKind: String, CaseIterable, Sendable {
    case screenRecording, microphone, speechRecognition, accessibility, camera, automation

    public enum Need: String, Sendable { case required, optional, informational }

    public var need: Need {
        switch self {
        case .screenRecording, .microphone, .speechRecognition: return .required
        case .accessibility, .camera: return .optional
        case .automation: return .informational
        }
    }

    public var title: String {
        switch self {
        case .screenRecording: return "Screen Recording"
        case .microphone: return "Microphone"
        case .speechRecognition: return "Speech Recognition"
        case .accessibility: return "Accessibility"
        case .camera: return "Camera"
        case .automation: return "Automation (Chrome, Finder, Notes, TextEdit)"
        }
    }

    public var why: String {
        switch self {
        case .screenRecording: return "Required: to see what you circle."
        case .microphone: return "Required: to hear you while you hold Option-Space."
        case .speechRecognition: return "Required: to turn what you say into text."
        case .accessibility: return "Optional: Do mode only."
        case .camera: return "Optional: hand tracking only."
        case .automation: return "macOS asks the first time a task uses each app."
        }
    }

    /// The Privacy & Security pane for this permission.
    public var settingsURL: String {
        let base = "x-apple.systempreferences:com.apple.preference.security?"
        switch self {
        case .screenRecording: return base + "Privacy_ScreenCapture"
        case .microphone: return base + "Privacy_Microphone"
        case .speechRecognition: return base + "Privacy_SpeechRecognition"
        case .accessibility: return base + "Privacy_Accessibility"
        case .camera: return base + "Privacy_Camera"
        case .automation: return base + "Privacy_Automation"
        }
    }

    /// macOS only lets the user switch these in System Settings, so Grant also opens the pane.
    public var settingsOnly: Bool { self == .screenRecording || self == .accessibility }
}

public enum PermissionStatus: String, Equatable, Sendable {
    case granted, notYet, denied
    /// Switched on in Settings but this process still can't use it (Screen Recording applies after a relaunch).
    case needsRelaunch
    /// Not checked up front (Automation): macOS asks per app on first use.
    case informational

    public var isGranted: Bool { self == .granted }
}

public enum PermissionRules {
    /// Grant raises the native prompt and, for settings-only or already-denied permissions, also opens the pane.
    public static func opensSettings(_ kind: PermissionKind, _ status: PermissionStatus) -> Bool {
        kind.settingsOnly || status == .denied || status == .needsRelaunch
    }

    /// Only required permissions block; optional and informational ones never do.
    public static func allRequiredGranted(_ s: [PermissionKind: PermissionStatus]) -> Bool {
        PermissionKind.allCases.filter { $0.need == .required }.allSatisfy { s[$0] == .granted }
    }

    public static func missingRequired(_ s: [PermissionKind: PermissionStatus]) -> [PermissionKind] {
        PermissionKind.allCases.filter { $0.need == .required && s[$0] != .granted }
    }
}

/// The robust Screen Recording decision. `preflight` is CGPreflightScreenCaptureAccess; `captureWorks` is a 1x1
/// ScreenCaptureKit capture of the main display, tried only when preflight says no (nil: not tried).
public enum ScreenRecordingCheck {
    public static func decide(preflight: Bool, captureWorks: Bool?, askedThisRun: Bool) -> PermissionStatus {
        if preflight || captureWorks == true { return .granted }
        // Asked (or sent to Settings) during this run and still blocked: it may already be on, pending a relaunch.
        return askedThisRun ? .needsRelaunch : .notYet
    }

    public static let missingSentence = "Reach can't see your screen yet. Switch on Reach under Screen Recording."
    public static let missingHint = "If Reach is already switched on there, remove it with the minus button, add it with "
        + "the plus button, then reopen Reach."
    public static let relaunchSentence = "Switched it on? Relaunch Reach so macOS applies it."
}

/// Edge detector for the checklist: fires once when the required set goes from incomplete to complete.
public struct AllSetTracker: Equatable, Sendable {
    private var last: Bool?
    public init() {}

    /// True only on the not-all -> all change; a window that opens with everything already green does not fire.
    public mutating func update(_ s: [PermissionKind: PermissionStatus]) -> Bool {
        let now = PermissionRules.allRequiredGranted(s)
        defer { last = now }
        return last == false && now
    }
}

// MARK: - pill visibility

public enum PillMode: String, CaseIterable, Sendable {
    case auto, always, hidden
    public static let key = "pillMode"

    public var title: String {
        switch self {
        case .auto: return "Auto"
        case .always: return "Always"
        case .hidden: return "Hidden (hotkey only)"
        }
    }

    public static func load(_ d: UserDefaults = .standard) -> PillMode {
        d.string(forKey: key).flatMap(PillMode.init(rawValue:)) ?? .auto
    }

    /// Hover at the top-centre may summon the pill only in Auto and Always.
    public var hoverSummons: Bool { self != .hidden }
}

public enum PillVisibility {
    /// Whether the collapsed pill should be on screen. An expanded panel or any activity always shows it.
    public static func shouldShow(mode: PillMode, expanded: Bool, busy: Bool) -> Bool {
        mode == .always || expanded || busy
    }
}
