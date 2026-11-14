import Foundation

/// Locates `.reach/` and reads the server's port and session token. The token is never logged.
public struct RunFiles: Sendable {
    public var reachDir: URL

    public init(reachDir: URL) { self.reachDir = reachDir }

    /// `--reach-dir`, then `REACH_DIR`, then the repo baked into the bundle, then a walk up from cwd and the binary.
    public static func resolve(arguments: [String], environment: [String: String], bundleRepo: String?) -> RunFiles {
        if let i = arguments.firstIndex(of: "--reach-dir"), i + 1 < arguments.count {
            return RunFiles(reachDir: URL(fileURLWithPath: arguments[i + 1]))
        }
        if let d = environment["REACH_DIR"], !d.isEmpty { return RunFiles(reachDir: URL(fileURLWithPath: d)) }
        if let repo = bundleRepo, !repo.isEmpty {
            return RunFiles(reachDir: URL(fileURLWithPath: repo).appendingPathComponent(".reach"))
        }
        let starts = [FileManager.default.currentDirectoryPath, (arguments.first ?? "")]
        for start in starts {
            var dir = URL(fileURLWithPath: start).standardizedFileURL
            for _ in 0..<8 {
                if FileManager.default.fileExists(atPath: dir.appendingPathComponent("backend/reach").path) {
                    return RunFiles(reachDir: dir.appendingPathComponent(".reach"))
                }
                dir.deleteLastPathComponent()
            }
        }
        return RunFiles(reachDir: URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(".reach"))
    }

    public var runDir: URL { reachDir.appendingPathComponent("run") }
    public var capturesDir: URL { reachDir.appendingPathComponent("captures") }

    public func read() -> (port: Int, token: String)? {
        guard let data = try? Data(contentsOf: runDir.appendingPathComponent("server.json")),
              let info = try? JSONDecoder().decode(JSONValue.self, from: data),
              let port = info["port"]?.number,
              let token = try? String(contentsOf: runDir.appendingPathComponent("token"), encoding: .utf8)
        else { return nil }
        let t = token.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : (Int(port), t)
    }
}

/// One WebSocket to `reach serve`. Callbacks arrive on the main queue.
public final class ReachClient: NSObject, @unchecked Sendable {
    public var onMessage: ((ServerMessage) -> Void)?
    public var onClose: ((String) -> Void)?
    public var log: ((String) -> Void)?

    private var task: URLSessionWebSocketTask?
    private lazy var session = URLSession(configuration: .default)
    private var closed = false

    public override init() {}

    public func connect(port: Int, token: String) {
        closed = false
        let ws = session.webSocketTask(with: URL(string: "ws://127.0.0.1:\(port)")!)
        ws.maximumMessageSize = 8 * 1024 * 1024
        task = ws
        ws.resume()
        log?("connecting to ws://127.0.0.1:\(port)")
        send(.hello(token: token))
        receive()
    }

    public func send(_ msg: ClientMessage) {
        guard let task, let text = try? msg.json() else { return }
        task.send(.string(text)) { [weak self] err in
            if let err { DispatchQueue.main.async { self?.finish("send failed: \(err.localizedDescription)") } }
        }
    }

    public func close() {
        closed = true
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
    }

    private func receive() {
        task?.receive { [weak self] result in
            guard let self else { return }
            DispatchQueue.main.async {
                switch result {
                case .failure(let err):
                    self.finish(self.closeReason(err))
                case .success(let frame):
                    var text: String?
                    switch frame {
                    case .string(let s): text = s
                    case .data(let d): text = String(data: d, encoding: .utf8)
                    @unknown default: break
                    }
                    if let text {
                        do { self.onMessage?(try ServerMessage.decode(text)) }
                        catch { self.log?("undecodable message: \(error)") }
                    }
                    self.receive()
                }
            }
        }
    }

    private func closeReason(_ err: Error) -> String {
        if let code = task?.closeCode.rawValue, code == 4401 { return "bad token (4401)" }
        return err.localizedDescription
    }

    private func finish(_ why: String) {
        guard !closed else { return }
        closed = true
        onClose?(why)
    }
}
