import Foundation

/// Cloud voice (Whisper STT / DeepInfra TTS through the backend) with Apple's on-device speech as the fallback.
public enum VoiceMode: String, CaseIterable, Sendable {
    case cloud, onDevice = "on-device"
    public static let key = "voiceMode"
    public static func load(_ d: UserDefaults = .standard) -> VoiceMode {
        d.string(forKey: key).flatMap(VoiceMode.init(rawValue:)) ?? .cloud
    }
}

public enum CloudVoice {
    /// How long to wait for Whisper after key-up before using Apple's final transcript.
    public static let sttWait: TimeInterval = 2.5
    /// How long to wait for cloud audio before speaking with AVSpeechSynthesizer.
    public static let ttsWait: TimeInterval = 3.0

    public enum Pick: Equatable, Sendable {
        case cloud(String)
        case apple
    }

    /// Which transcript to use. Whisper's text wins when it arrived in time, without error, is non-empty, and Apple
    /// heard at least something (Whisper invents words on silence). Otherwise Apple's final goes through VoiceGate.
    public static func pick(cloudText: String?, cloudError: String?, elapsed: TimeInterval, appleText: String) -> Pick {
        guard cloudError == nil, elapsed <= sttWait,
              let t = cloudText?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty,
              !appleText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return .apple }
        return .cloud(t)
    }

    /// Cloud audio should be played only if it is for the current request and arrived within `ttsWait`.
    public static func playCloud(path: String, error: String?, id: String?, current: String?, elapsed: TimeInterval) -> Bool {
        error == nil && !path.isEmpty && id != nil && id == current && elapsed <= ttsWait
    }

    /// Paths the backend may hand back: only files under `<reachDir>/audio/`.
    public static func isAudioPath(_ path: String, reachDir: URL) -> Bool {
        let root = reachDir.appendingPathComponent("audio").standardizedFileURL.resolvingSymlinksInPath().path + "/"
        let p = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath().path
        return p.hasPrefix(root)
    }
}
