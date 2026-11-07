import Foundation

@MainActor
final class ServerProcess {
    let port: Int
    let dataDirectory: URL
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var usesDefaultDataDirectory: Bool {
        dataDirectory.standardizedFileURL.resolvingSymlinksInPath() == Self.defaultDataDirectory.standardizedFileURL.resolvingSymlinksInPath()
    }
    private static var defaultDataDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Grok Spaces", isDirectory: true)
    }
    private var process: Process?
    private var logHandle: FileHandle?
    private let session: URLSession

    init() {
        let network = URLSessionConfiguration.ephemeral
        network.httpCookieStorage = nil
        network.urlCredentialStorage = nil
        network.connectionProxyDictionary = [:]
        session = URLSession(configuration: network, delegate: LocalSessionDelegate(), delegateQueue: nil)
        let requestedPort = ProcessInfo.processInfo.environment["GROK_SPACES_PORT"].flatMap(Int.init)
        port = requestedPort.flatMap { (1024...65535).contains($0) ? $0 : nil } ?? 8789
        if let path = ProcessInfo.processInfo.environment["GROK_SPACES_DATA_DIR"], !path.isEmpty {
            dataDirectory = URL(fileURLWithPath: path, isDirectory: true)
        } else {
            dataDirectory = Self.defaultDataDirectory
        }
    }

    func start() async throws {
        try FileManager.default.createDirectory(
            at: dataDirectory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dataDirectory.path)
        switch await healthStatus() {
        case .compatible: return
        case .otherDataDirectory:
            throw StartupError.message("Port \(port) runs Grok Spaces with a different or unverified data folder. Set GROK_SPACES_PORT to an unused port.")
        case .occupied:
            throw StartupError.message("Port \(port) is already occupied by another service. Set GROK_SPACES_PORT to an unused port.")
        case .unreachable: break
        }
        if let process, process.isRunning { process.terminate() }
        process = nil
        logHandle?.closeFile()

        let script = try serverScript()
        let logURL = dataDirectory.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            guard FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
                throw StartupError.message("Could not create the local server log.")
            }
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: logURL.path)
        let handle = try FileHandle(forWritingTo: logURL)
        try handle.seekToEnd()
        logHandle = handle

        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        child.arguments = ["-E", "-s", "-B", "-u", script.path, "--port", String(port), "--data-dir", dataDirectory.path]
        child.currentDirectoryURL = script.deletingLastPathComponent().deletingLastPathComponent()
        child.standardOutput = handle
        child.standardError = handle
        // Do not pass account tokens, proxy variables, PYTHONPATH or user-site
        // startup configuration into this local, dependency-free service.
        child.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                             "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                             "LANG": "en_US.UTF-8"]
        try child.run()
        process = child

        do {
            for _ in 0..<40 {
                if await healthStatus() == .compatible { return }
                guard child.isRunning else {
                    throw StartupError.message("The local server exited. See server.log in the data folder.")
                }
                try await Task.sleep(for: .milliseconds(150))
            }
            throw StartupError.message("The local server did not become ready on port \(port). See server.log in the data folder.")
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        // A server discovered through /health belongs to its original owner.
        if let process, process.isRunning { process.terminate() }
        process = nil
        logHandle?.closeFile()
        logHandle = nil
    }

    private enum HealthStatus { case compatible, otherDataDirectory, occupied, unreachable }

    private func healthStatus() async -> HealthStatus {
        var request = URLRequest(url: baseURL.appendingPathComponent("health"))
        request.timeoutInterval = 0.6
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, response) = try await session.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return .occupied }
            guard object["app"] as? String == "grok-spaces" else { return .occupied }
            guard let path = object["data_dir"] as? String else { return .otherDataDirectory }
            let received = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath()
            let expected = dataDirectory.standardizedFileURL.resolvingSymlinksInPath()
            return received == expected ? .compatible : .otherDataDirectory
        } catch let error as URLError {
            return error.code == .cannotConnectToHost ? .unreachable : .occupied
        } catch { return .occupied }
    }

    private func serverScript() throws -> URL {
        if let sourceRoot = ProcessInfo.processInfo.environment["GROK_SPACES_ROOT"] {
            guard sourceRoot.hasPrefix("/") else {
                throw StartupError.message("GROK_SPACES_ROOT must be an absolute project path.")
            }
            let script = URL(fileURLWithPath: sourceRoot, isDirectory: true).appendingPathComponent("bridge/server.py")
            guard FileManager.default.fileExists(atPath: script.path) else {
                throw StartupError.message("The selected GROK_SPACES_ROOT does not contain bridge/server.py.")
            }
            return script
        }
        if let resources = Bundle.main.resourceURL {
            let script = resources.appendingPathComponent("bridge/server.py")
            if FileManager.default.fileExists(atPath: script.path) { return script }
        }
        throw StartupError.message("bridge/server.py is missing. Rebuild the app or set GROK_SPACES_ROOT to an absolute source project path.")
    }

    enum StartupError: LocalizedError {
        case message(String)
        var errorDescription: String? {
            switch self { case .message(let text): text }
        }
    }
}

private final class LocalSessionDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        // A different service occupying the port cannot redirect the health probe.
        completionHandler(nil)
    }
}
