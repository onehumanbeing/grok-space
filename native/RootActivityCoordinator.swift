import AppKit
import WebKit

/// Sends host visibility only to the trusted main page, never to authored iframes.
@MainActor
final class RootActivityCoordinator: NSObject {
    private weak var window: NSWindow?
    private weak var webView: WKWebView?
    private let port: Int
    private let suppressed: Bool
    private let report = LifecycleReport.fromEnvironment()
    private var lastActive: Bool?
    private var inFlightActive: Bool?
    private var generation = 0
    private var pendingUpdate: Task<Void, Never>?
    private var pendingForce = false

    init(window: NSWindow, webView: WKWebView, port: Int, suppressed: Bool) {
        self.window = window
        self.webView = webView
        self.port = port
        self.suppressed = suppressed
        super.init()
    }

    func start() {
        guard !suppressed else { return }
        let workspace = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didActivateApplicationNotification, NSWorkspace.didHideApplicationNotification,
                     NSWorkspace.didUnhideApplicationNotification, NSWorkspace.didTerminateApplicationNotification] {
            workspace.addObserver(self, selector: #selector(activityChanged), name: name, object: nil)
        }
        let center = NotificationCenter.default
        for name in [NSApplication.didBecomeActiveNotification, NSApplication.didResignActiveNotification,
                     NSApplication.didHideNotification, NSApplication.didUnhideNotification] {
            center.addObserver(self, selector: #selector(activityChanged), name: name, object: nil)
        }
        for name in [NSWindow.didMiniaturizeNotification, NSWindow.didDeminiaturizeNotification,
                     NSWindow.didChangeOcclusionStateNotification] {
            center.addObserver(self, selector: #selector(activityChanged), name: name, object: window)
        }
    }

    func stop() {
        pendingUpdate?.cancel()
        pendingUpdate = nil
        generation += 1
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func activityChanged(_ notification: Notification) { publish() }

    func publish(force: Bool = false) {
        pendingForce = pendingForce || force
        guard pendingUpdate == nil else { return }
        pendingUpdate = Task { @MainActor [weak self] in
            // Workspace/AppKit notifications can precede the final foreground
            // state. Re-read it after the callback and once after settling.
            await Task.yield()
            guard let self, !Task.isCancelled else { return }
            let force = self.pendingForce
            self.pendingForce = false
            self.apply(force: force)
            do { try await Task.sleep(for: .milliseconds(150)) }
            catch { return }
            guard !Task.isCancelled else { return }
            self.apply(force: self.pendingForce)
            self.pendingForce = false
            self.pendingUpdate = nil
        }
    }

    private func apply(force: Bool) {
        guard let window, let webView else { return }
        let foreground: String
        if suppressed { foreground = "test" }
        else {
            let bundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
            if bundle == "com.anysphere.sand" { foreground = "grok" }
            else if let ownBundle = Bundle.main.bundleIdentifier, bundle == ownBundle { foreground = "own" }
            else { foreground = bundle == nil ? "none" : "other" }
        }
        let visible = window.isVisible && window.occlusionState.contains(.visible)
        let active = !suppressed && visible && !window.isMiniaturized && !NSApp.isHidden &&
            (foreground == "own" || foreground == "grok")
        let snapshot = LifecycleReport.Snapshot(foreground: foreground, visible: visible,
                                                minimized: window.isMiniaturized, hidden: NSApp.isHidden,
                                                desiredActive: active)
        guard let url = webView.url, url.scheme == "http", url.host == "127.0.0.1", url.port == port,
              url.path == "/" else {
            report?.record(phase: "awaiting-root", snapshot: snapshot)
            return
        }
        guard force || (lastActive != active && inFlightActive != active) else {
            report?.record(phase: "unchanged", snapshot: snapshot, observedActive: lastActive)
            return
        }
        generation += 1
        let sentGeneration = generation
        inFlightActive = active
        let value = active ? "true" : "false"
        let script = """
        (() => {
          window.__GROK_SPACES_HOST_ACTIVE__ = \(value);
          window.dispatchEvent(new CustomEvent('spaces-native-visibility', {detail: {active: \(value)}}));
          return window.__GROK_SPACES_HOST_ACTIVE__;
        })()
        """
        report?.record(phase: "sent", snapshot: snapshot)
        webView.evaluateJavaScript(script) { [weak self] result, error in
            guard let self, self.generation == sentGeneration else { return }
            self.inFlightActive = nil
            let observed = result as? Bool
            if error == nil && observed == active { self.lastActive = active }
            self.report?.record(phase: error == nil && observed == active ? "acknowledged" : "unconfirmed",
                                snapshot: snapshot, observedActive: observed)
        }
    }
}
