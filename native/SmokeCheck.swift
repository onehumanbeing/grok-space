import Foundation
import WebKit

/// Explicit, isolated validation of the real WKWebView loading path.
/// Reads only document metadata and geometry, never page text or application state.
@MainActor
final class SmokeCheck {
    private let reportPath: String
    private let startedAt = Date()
    private var timeoutTimer: Timer?
    private var completed = false
    private var inspecting = false
    private var finishHandler: ((Int32) -> Void)?

    static func fromEnvironment() -> SmokeCheck? {
        guard let path = ProcessInfo.processInfo.environment["GROK_SPACES_SMOKE_REPORT"] else { return nil }
        return SmokeCheck(reportPath: path)
    }

    private init(reportPath: String) { self.reportPath = reportPath }

    func start(onFinish: @escaping (Int32) -> Void) -> Bool {
        finishHandler = onFinish
        guard reportPath.hasPrefix("/"), reportPath != "/" else {
            finish(ok: false, stage: "configuration", error: "Smoke report path must be an absolute file path.")
            return false
        }
        let environment = ProcessInfo.processInfo.environment
        guard let dataPath = environment["GROK_SPACES_DATA_DIR"], dataPath.hasPrefix("/"),
              let portText = environment["GROK_SPACES_PORT"], let port = Int(portText),
              (1024...65535).contains(port) else {
            finish(ok: false, stage: "configuration", error: "Smoke mode requires an absolute GROK_SPACES_DATA_DIR and an explicit valid GROK_SPACES_PORT.")
            return false
        }
        timeoutTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.finish(ok: false, stage: "timeout", error: "The local root page did not become ready within 30 seconds.")
            }
        }
        return true
    }

    func pageLoaded(_ webView: WKWebView) {
        guard !completed, !inspecting else { return }
        inspecting = true
        Task { [weak self, weak webView] in
            while let self, let webView, !self.completed {
                do {
                    let result = try await webView.evaluateJavaScript(Self.metadataScript)
                    guard let metadata = result as? [String: Any],
                          let viewport = metadata["viewport"] as? [String: Any],
                          let body = metadata["body"] as? [String: Any]
                    else {
                        self.finish(ok: false, stage: "javascript", error: "WebKit returned invalid document metadata.")
                        return
                    }
                    let hasViewport = (viewport["width"] as? NSNumber)?.doubleValue ?? 0 > 0 &&
                        (viewport["height"] as? NSNumber)?.doubleValue ?? 0 > 0
                    let hasBody = (body["width"] as? NSNumber)?.doubleValue ?? 0 > 0 &&
                        (body["height"] as? NSNumber)?.doubleValue ?? 0 > 0
                    let appReady = metadata["appReady"] as? Bool
                    if metadata["readyState"] as? String == "complete", hasViewport, hasBody, appReady != false {
                        self.finish(ok: true, stage: "webkit", metadata: metadata)
                        return
                    }
                    try await Task.sleep(for: .milliseconds(150))
                } catch {
                    self.finish(ok: false, stage: "javascript", error: "Could not evaluate root document metadata in WebKit.")
                    return
                }
            }
        }
    }

    func fail(stage: String, error: String) {
        finish(ok: false, stage: stage, error: error)
    }

    private func finish(ok: Bool, stage: String, error: String? = nil, metadata: [String: Any]? = nil) {
        guard !completed else { return }
        completed = true
        timeoutTimer?.invalidate()
        timeoutTimer = nil
        var report: [String: Any] = [
            "format_version": 1, "app": "grok-spaces", "ok": ok, "stage": stage,
            "elapsed_ms": Int(Date().timeIntervalSince(startedAt) * 1000),
        ]
        if let error { report["error"] = error }
        if let metadata { report["document"] = metadata }
        var status: Int32 = ok ? 0 : 1
        do {
            guard reportPath.hasPrefix("/"), reportPath != "/" else {
                throw ReportError.invalidPath
            }
            let url = URL(fileURLWithPath: reportPath)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
            status = 1
            FileHandle.standardError.write(Data("Grok Spaces smoke report could not be written.\n".utf8))
        }
        finishHandler?(status)
    }

    private enum ReportError: Error { case invalidPath }

    private static let metadataScript = """
    (() => {
      const body = document.body;
      const rect = body ? body.getBoundingClientRect() : {width: 0, height: 0};
      const root = document.querySelector('[data-app-root], #app, .app-shell, main');
      const marker = window.__GROK_SPACES_READY__;
      const attribute = document.documentElement.dataset.appReady ?? root?.dataset.ready;
      const appReady = typeof marker === 'boolean' ? marker :
        (attribute === 'true' ? true : (attribute === 'false' ? false : null));
      return {
        title: document.title.slice(0, 128), readyState: document.readyState,
        viewport: {width: window.innerWidth, height: window.innerHeight},
        body: {width: Math.round(rect.width), height: Math.round(rect.height)},
        appRootPresent: Boolean(root), appReady,
        hostActive: typeof window.__GROK_SPACES_HOST_ACTIVE__ === 'boolean' ? window.__GROK_SPACES_HOST_ACTIVE__ : null
      };
    })()
    """
}
