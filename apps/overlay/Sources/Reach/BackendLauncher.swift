import Foundation
import ReachCore

/// Starts `reach serve` for the user when no backend is reachable, so Reach works right after it is opened.
/// The repo is the folder that holds `.reach/` (found by RunFiles); server options come from `.reach/server.env`
/// (plain KEY=VALUE lines such as REACH_DO_MODE=1; never keys).
@MainActor
final class BackendLauncher {
    private let files: RunFiles
    private var lastSpawn: Date?
    private(set) var process: Process?

    init(files: RunFiles) { self.files = files }

    private var repo: URL { files.reachDir.deletingLastPathComponent() }

    /// True when the run files name a port that accepts a TCP connection right now.
    func serverReachable() -> Bool {
        guard let info = files.read() else { return false }
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(UInt16(truncatingIfNeeded: info.port)).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        return withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
        }
    }

    static func parseEnv(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = String(line[..<eq]).trimmingCharacters(in: .whitespaces)
            let value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
            // Only Reach's own switches: never let this file carry keys or arbitrary variables.
            if key.hasPrefix("REACH_"), !key.contains("KEY"), !key.contains("TOKEN") { out[key] = value }
        }
        return out
    }

    private func uvPath() -> String? {
        let candidates = ["/opt/homebrew/bin/uv", "/usr/local/bin/uv", NSHomeDirectory() + "/.local/bin/uv", NSHomeDirectory() + "/.cargo/bin/uv"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// Spawns the backend unless it is reachable or was started in the last 25 s. Returns true when a start was attempted.
    @discardableResult
    func ensureRunning() -> Bool {
        if serverReachable() { return false }
        if let t = lastSpawn, Date().timeIntervalSince(t) < 25 { return false }
        guard let uv = uvPath() else { log("autostart: uv not found; run `reach serve` yourself"); return false }
        let backend = repo.appendingPathComponent("backend")
        guard FileManager.default.fileExists(atPath: backend.appendingPathComponent("pyproject.toml").path) else {
            log("autostart: no backend folder at \(backend.path)"); return false
        }
        lastSpawn = Date()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: uv)
        p.arguments = ["run", "--quiet", "reach", "serve"]
        p.currentDirectoryURL = backend
        var env = ProcessInfo.processInfo.environment
        env["INTERN_HOME"] = repo.appendingPathComponent(".intern-home").path
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        if let text = try? String(contentsOf: files.reachDir.appendingPathComponent("server.env"), encoding: .utf8) {
            for (k, v) in Self.parseEnv(text) { env[k] = v }
        }
        p.environment = env
        let logURL = files.reachDir.appendingPathComponent("serve.log")
        try? FileManager.default.createDirectory(at: files.reachDir, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let h = try? FileHandle(forWritingTo: logURL) { p.standardOutput = h; p.standardError = h }
        do { try p.run(); process = p; log("autostart: started `reach serve` (pid \(p.processIdentifier))"); return true }
        catch { log("autostart: could not start the backend: \(error.localizedDescription)"); return false }
    }
}
