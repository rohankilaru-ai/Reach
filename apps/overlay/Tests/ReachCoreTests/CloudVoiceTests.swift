import Foundation
import Testing
@testable import ReachCore

@Suite struct CloudVoiceTests {
    @Test func whisperWinsInTime() {
        #expect(CloudVoice.pick(cloudText: " Add 17 apples ", cloudError: nil, elapsed: 0.8, appleText: "add seventeen apples") == .cloud("Add 17 apples"))
    }

    @Test func fallsBackToApple() {
        #expect(CloudVoice.pick(cloudText: nil, cloudError: nil, elapsed: 2.6, appleText: "hi") == .apple)          // timeout
        #expect(CloudVoice.pick(cloudText: "hi", cloudError: nil, elapsed: 2.51, appleText: "hi") == .apple)        // late
        #expect(CloudVoice.pick(cloudText: "hi", cloudError: "HTTP 500", elapsed: 0.5, appleText: "hi") == .apple)  // error
        #expect(CloudVoice.pick(cloudText: "  ", cloudError: nil, elapsed: 0.5, appleText: "hi") == .apple)         // empty
        #expect(CloudVoice.pick(cloudText: "Thank you.", cloudError: nil, elapsed: 0.5, appleText: "") == .apple)   // silence
    }

    @Test func ttsPlaysOnlyCurrentAndInTime() {
        #expect(CloudVoice.playCloud(path: "/r/audio/a.wav", error: nil, id: "t2", current: "t2", elapsed: 1))
        #expect(!CloudVoice.playCloud(path: "/r/audio/a.wav", error: nil, id: "t1", current: "t2", elapsed: 1))  // stale
        #expect(!CloudVoice.playCloud(path: "/r/audio/a.wav", error: nil, id: "t2", current: nil, elapsed: 1))   // Esc
        #expect(!CloudVoice.playCloud(path: "/r/audio/a.wav", error: nil, id: "t2", current: "t2", elapsed: 3.2))
        #expect(!CloudVoice.playCloud(path: "", error: "HTTP 500", id: "t2", current: "t2", elapsed: 1))
    }

    @Test func audioPathsStayUnderReachAudio() {
        let r = URL(fileURLWithPath: "/tmp/x/.reach")
        #expect(CloudVoice.isAudioPath("/tmp/x/.reach/audio/tts-1.wav", reachDir: r))
        #expect(!CloudVoice.isAudioPath("/tmp/x/.reach/audio/../run/token", reachDir: r))
        #expect(!CloudVoice.isAudioPath("/etc/passwd", reachDir: r))
    }

    @Test func modeDefaultsToCloud() {
        let d = UserDefaults(suiteName: "cv-test-\(UUID().uuidString)")!
        #expect(VoiceMode.load(d) == .cloud)
        d.set("on-device", forKey: VoiceMode.key)
        #expect(VoiceMode.load(d) == .onDevice)
    }

    @Test func wireRoundTrip() throws {
        let j = try ClientMessage.stt(audioPath: "/r/audio/a.wav", id: "s1").json()
        #expect(j == #"{"audio_path":"\/r\/audio\/a.wav","format":"wav","id":"s1","type":"stt"}"#)
        let t = try ClientMessage.tts(text: "hi", id: "t1").json()
        #expect(t == #"{"id":"t1","text":"hi","type":"tts"}"#)
        #expect(try ServerMessage.decode(#"{"type":"stt_result","text":"hi","ms":5,"error":null,"id":"s1"}"#)
                == .sttResult(text: "hi", ms: 5, error: nil, id: "s1"))
        #expect(try ServerMessage.decode(#"{"type":"tts_audio","path":"/r/audio/a.wav","format":"wav","ms":9,"id":"t1"}"#)
                == .ttsAudio(path: "/r/audio/a.wav", ms: 9, error: nil, id: "t1"))
    }
}
