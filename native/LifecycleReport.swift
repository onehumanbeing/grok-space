import Foundation

/// Opt-in diagnostics: no app names, URLs, page content, Space data or credentials.
@MainActor
final class LifecycleReport {
    struct Snapshot: Sendable {
        let foreground: String
        let visible: Bool
        let minimized: Bool
        let hidden: Bool
        let desiredActive: Bool
    }
    private let url: URL
    private var events: [[String: Any]] = []

    static func fromEnvironment() -> LifecycleReport? {
        guard let path = ProcessInfo.processInfo.environment["GROK_SPACES_LIFECYCLE_REPORT"],
              path.hasPrefix("/"), path != "/" else { return nil }
        return LifecycleReport(url: URL(fileURLWithPath: path))
    }

    private init(url: URL) { self.url = url }

    func record(phase: String, snapshot: Snapshot, observedActive: Bool? = nil) {
        events.append([
            "timestamp_ms": Int(Date().timeIntervalSince1970 * 1000), "phase": phase,
            "foreground": snapshot.foreground, "window_visible": snapshot.visible,
            "window_minimized": snapshot.minimized, "app_hidden": snapshot.hidden,
            "desired_active": snapshot.desiredActive,
            "observed_js_active": observedActive.map { $0 as Any } ?? NSNull(),
        ])
        if events.count > 128 { events.removeFirst(events.count - 128) }
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let data = try JSONSerialization.data(withJSONObject: ["app": "grok-spaces",
                                                                    "process_id": ProcessInfo.processInfo.processIdentifier,
                                                                    "events": events],
                                                  options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
            // Diagnostics must not interrupt the application or echo local paths.
        }
    }
}
