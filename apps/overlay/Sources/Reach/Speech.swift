import AVFoundation
import Foundation
import Speech

/// The result of one recognition: text plus the per-segment confidences of the final transcription.
struct Heard {
    var text: String
    var confidences: [Float]
    var final: Bool
}

enum SpeechAuth {
    static func describe(_ s: SFSpeechRecognizerAuthorizationStatus) -> String {
        switch s {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not determined"
        @unknown default: return "unknown"
        }
    }

    /// Asks once (macOS shows the prompt). Without the usage string in the bundle, macOS would abort the
    /// process, so refuse instead.
    static func request(_ done: @escaping (SFSpeechRecognizerAuthorizationStatus) -> Void) {
        guard Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") != nil else {
            log("speech: no NSSpeechRecognitionUsageDescription in this bundle; run from Reach.app (scripts/build-overlay.sh)")
            done(.restricted)
            return
        }
        let now = SFSpeechRecognizer.authorizationStatus()
        if now != .notDetermined { done(now); return }
        SFSpeechRecognizer.requestAuthorization { s in DispatchQueue.main.async { done(s) } }
    }
}

/// Microphone and file transcription through Apple's Speech framework. Audio stays in memory; nothing is saved.
/// On-device recognition is required whenever the recognizer supports it.
final class SpeechInput: @unchecked Sendable {
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    /// True when recognition runs on this Mac; false means Apple's server would be used.
    var onDevice: Bool { recognizer?.supportsOnDeviceRecognition ?? false }
    var available: Bool { recognizer?.isAvailable ?? false }

    /// Starts the mic tap. `onResult` is called on the main queue with partials, then once with the final.
    func start(onResult: @escaping (Heard) -> Void, onError: @escaping (String) -> Void) {
        guard let recognizer, recognizer.isAvailable else { onError("speech recognition unavailable"); return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { req.requiresOnDeviceRecognition = true }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { onError("no microphone input"); return }
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buf, _ in req.append(buf) }
        engine.prepare()
        do { try engine.start() } catch {
            input.removeTap(onBus: 0)
            onError("microphone failed: \(error.localizedDescription)")
            return
        }
        request = req
        task = recognizer.recognitionTask(with: req) { result, error in
            if let result {
                let heard = SpeechInput.heard(result)
                DispatchQueue.main.async { onResult(heard) }
            } else if let error {
                DispatchQueue.main.async { onError(error.localizedDescription) }
            }
        }
    }

    /// Stops the mic and lets the recognizer finish; the final result arrives through `onResult`.
    func finish() {
        stopEngine()
        request?.endAudio()
    }

    func cancel() {
        stopEngine()
        task?.cancel()
        task = nil
        request = nil
    }

    private func stopEngine() {
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
    }

    /// Transcribes an audio file (the selftest path; no microphone).
    func transcribe(file: URL, onDone: @escaping (Result<Heard, Error>) -> Void) {
        guard let recognizer else { onDone(.failure(SpeechError("no recognizer for en-US"))); return }
        let req = SFSpeechURLRecognitionRequest(url: file)
        req.shouldReportPartialResults = false
        if recognizer.supportsOnDeviceRecognition { req.requiresOnDeviceRecognition = true }
        task = recognizer.recognitionTask(with: req) { result, error in
            if let result, result.isFinal {
                let heard = SpeechInput.heard(result)
                DispatchQueue.main.async { onDone(.success(heard)) }
            } else if let error {
                DispatchQueue.main.async { onDone(.failure(error)) }
            }
        }
    }

    private static func heard(_ r: SFSpeechRecognitionResult) -> Heard {
        let t = r.bestTranscription
        return Heard(text: t.formattedString, confidences: t.segments.map(\.confidence), final: r.isFinal)
    }
}

struct SpeechError: Error, CustomStringConvertible {
    let description: String
    init(_ s: String) { description = s }
}

/// Microphone permission (macOS prompts on first request).
enum MicAuth {
    static func request(_ done: @escaping (Bool) -> Void) {
        guard Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil else {
            log("mic: no NSMicrophoneUsageDescription in this bundle; run from Reach.app")
            done(false)
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: done(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { ok in DispatchQueue.main.async { done(ok) } }
        default: done(false)
        }
    }
}
