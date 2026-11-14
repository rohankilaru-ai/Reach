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

    /// Cloud voice: while listening, the mic is ALSO written as 16 kHz mono 16-bit WAV here (nil: not recording).
    var recordTo: URL?
    private(set) var recorded: URL?
    private var wav: WavWriter?

    /// True when recognition runs on this Mac; false means Apple's server would be used.
    var onDevice: Bool { recognizer?.supportsOnDeviceRecognition ?? false }
    var available: Bool { recognizer?.isAvailable ?? false }

    /// Starts the mic tap. `onResult` is called on the main queue with partials, then once with the final.
    func start(onResult: @escaping (Heard) -> Void, onError: @escaping (String) -> Void,
               onLevel: ((Float) -> Void)? = nil) {
        guard let recognizer, recognizer.isAvailable else { onError("speech recognition unavailable"); return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { req.requiresOnDeviceRecognition = true }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { onError("no microphone input"); return }
        input.removeTap(onBus: 0)
        let level = onLevel
        recorded = nil
        wav = recordTo.flatMap { WavWriter(url: $0, from: format) }
        let writer = wav
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buf, _ in
            req.append(buf)
            writer?.append(buf)
            if let level, let ch = buf.floatChannelData?[0], buf.frameLength > 0 {
                var sum: Float = 0
                for i in 0..<Int(buf.frameLength) { sum += ch[i] * ch[i] }
                let rms = (sum / Float(buf.frameLength)).squareRoot()
                let v = min(1, max(0, (20 * log10(max(rms, 1e-5)) + 50) / 40))   // -50 dB..-10 dB -> 0..1
                DispatchQueue.main.async { level(v) }
            }
        }
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
        if let w = wav { recorded = w.close(); wav = nil }
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

/// Writes mic buffers as 16 kHz mono 16-bit PCM WAV (what Whisper and the backend's 60 s check expect).
final class WavWriter: @unchecked Sendable {
    private let file: AVAudioFile
    private let converter: AVAudioConverter
    private let out: AVAudioFormat
    private let lock = NSLock()
    private var open = true
    let url: URL

    init?(url: URL, from input: AVAudioFormat) {
        guard let out = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true),
              let conv = AVAudioConverter(from: input, to: out) else { return nil }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard let f = try? AVAudioFile(forWriting: url, settings: out.settings, commonFormat: .pcmFormatInt16,
                                       interleaved: true) else { return nil }
        self.url = url; self.file = f; self.converter = conv; self.out = out
    }

    func append(_ buf: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard open else { return }
        let cap = AVAudioFrameCount(Double(buf.frameLength) * out.sampleRate / buf.format.sampleRate) + 16
        guard let dst = AVAudioPCMBuffer(pcmFormat: out, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        converter.convert(to: dst, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buf
        }
        if err == nil, dst.frameLength > 0 { try? file.write(from: dst) }
    }

    /// Closes the file (AVAudioFile finalizes the header when released) and returns its URL.
    func close() -> URL {
        lock.lock(); open = false; lock.unlock()
        return url
    }
}
