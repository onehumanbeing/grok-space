import AppKit
import WebKit
import Darwin

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    private let server = ServerProcess()
    private let smoke = SmokeCheck.fromEnvironment()
    private var window: NSWindow!
    private var webView: WKWebView!
    private var companion: CompanionCoordinator!
    private var rootActivity: RootActivityCoordinator!
    private var ownWindow: OwnWindowCoordinator!
    private var followMenuItem: NSMenuItem!
    private var loading = false
    private var exporting = false
    private var serverReady = false
    private var pendingSelection: SpaceLink?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(smoke == nil ? .regular : .accessory)
        buildMenu()
        let configuration = WKWebViewConfiguration()
        if smoke != nil { configuration.websiteDataStore = .nonPersistent() }
        configuration.userContentController.addUserScript(WKUserScript(
            source: "window.__GROK_SPACES_HOST_ACTIVE__ = false;",
            injectionTime: .atDocumentStart, forMainFrameOnly: true
        ))
        configuration.userContentController.add(self, name: "spacesNative")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 760),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Grok Spaces"
        window.minSize = NSSize(width: 320, height: 280)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = webView
        window.center()
        companion = CompanionCoordinator(window: window)
        rootActivity = RootActivityCoordinator(window: window, webView: webView, port: server.port, suppressed: smoke != nil)
        ownWindow = OwnWindowCoordinator(window: window, companion: companion)
        ownWindow.onChange = { [weak self] in self?.publishWindowState() }
        if let smoke {
            followMenuItem.state = .off
            window.title = "Grok Spaces — Isolated smoke check"
            guard smoke.start(onFinish: { [weak self] status in
                self?.shutdown()
                Darwin.exit(status)
            }) else { return }
        } else {
            window.makeKeyAndOrderFront(nil)
            companion.start()
            rootActivity.start()
        }
        Task { await loadPage() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        shutdown()
    }

    private func shutdown() {
        companion?.stop()
        rootActivity?.stop()
        ownWindow?.stop()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "spacesNative")
        server.stop()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard smoke == nil else { return false }
        if !flag { showOwnWindow() }
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard smoke == nil, server.usesDefaultDataDirectory,
              let selection = urls.compactMap({ SpaceLink(url: $0) }).last else { return }
        pendingSelection = selection
        // LaunchServices can deliver the URL before didFinishLaunching.
        if serverReady { loadCurrentSelection() }
        if window != nil { showOwnWindow() }
    }

    private func showOwnWindow() {
        guard let window else { return }
        if !window.isVisible && !window.isMiniaturized {
            // An explicit open must remain usable when Grok is hidden.
            companion.followsGrok = false
            followMenuItem.state = .off
        }
        if window.isMiniaturized { window.deminiaturize(nil) }
        NSApp.unhide(nil)
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        publishWindowState()
    }

    private func loadCurrentSelection() {
        let url = pendingSelection?.localURL(baseURL: server.baseURL) ?? server.baseURL
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
        if pendingSelection != nil { showOwnWindow() }
    }

    private func buildMenu() {
        let main = NSMenu()
        let appRoot = NSMenuItem()
        let appMenu = NSMenu(title: "Grok Spaces")
        appMenu.addItem(withTitle: "About Grok Spaces", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Grok Spaces", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appRoot.submenu = appMenu
        main.addItem(appRoot)

        let spaceRoot = NSMenuItem()
        spaceRoot.title = "Space"
        let spaceMenu = NSMenu(title: "Space")
        followMenuItem = NSMenuItem(title: "Follow Grok", action: #selector(toggleFollow), keyEquivalent: "f")
        followMenuItem.target = self
        followMenuItem.state = .on
        spaceMenu.addItem(followMenuItem)
        let standalone = spaceMenu.addItem(withTitle: "Standalone", action: #selector(makeStandalone), keyEquivalent: "")
        standalone.target = self
        spaceMenu.addItem(.separator())
        let reload = spaceMenu.addItem(withTitle: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        let folder = spaceMenu.addItem(withTitle: "Open Data Folder", action: #selector(openDataFolder), keyEquivalent: "")
        folder.target = self
        spaceRoot.submenu = spaceMenu
        main.addItem(spaceRoot)

        let editRoot = NSMenuItem()
        editRoot.title = "Edit"
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editRoot.submenu = edit
        main.addItem(editRoot)
        NSApp.mainMenu = main
    }

    @objc private func toggleFollow() {
        ownWindow.request(action: "mode", value: companion.followsGrok ? "standalone" : "companion") { [weak self] _, _ in
            self?.publishWindowState()
        }
    }

    @objc private func makeStandalone() {
        ownWindow.request(action: "mode", value: "standalone") { [weak self] _, _ in self?.publishWindowState() }
    }

    @objc private func openDataFolder() {
        NSWorkspace.shared.open(server.dataDirectory)
    }

    @objc private func reloadPage() {
        Task { await loadPage() }
    }

    private func loadPage() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            try await server.start()
            serverReady = true
            loadCurrentSelection()
        } catch {
            if let smoke {
                smoke.fail(stage: "server", error: "The isolated local service failed to start.")
                return
            }
            let message = error.localizedDescription
                .replacingOccurrences(of: "&", with: "&amp;")
                .replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;")
            webView.loadHTMLString("""
            <!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
            <style>body{font:15px -apple-system;margin:32px;color:#111;background:#fff}h1{font-size:22px}p{line-height:1.5;color:#555}</style>
            <h1>Grok Spaces could not start</h1><p>\(message)</p><p>Use Space → Reload to try again, or Space → Open Data Folder to inspect the local log.</p>
            """, baseURL: nil)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        let isLocal = url.scheme == "http" && url.host == "127.0.0.1" && url.port == server.port
        let internalDocument = url.absoluteString == "about:blank" || url.absoluteString == "about:srcdoc"
        let inlineData = url.scheme == "data"
        decisionHandler(isLocal || internalDocument || inlineData ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let url = webView.url, url.scheme == "http", url.host == "127.0.0.1",
              url.port == server.port, url.path == "/" else { return }
        rootActivity.publish(force: true)
        publishWindowState()
        smoke?.pageLoaded(webView)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        if smoke != nil, navigationResponse.isForMainFrame {
            let response = navigationResponse.response
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  response.mimeType == "text/html" else {
                decisionHandler(.cancel)
                smoke?.fail(stage: "response", error: "The local root response was not successful HTML.")
                return
            }
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        smoke?.fail(stage: "navigation", error: "The local root page failed to load in WebKit.")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        smoke?.fail(stage: "navigation", error: "The local root page failed to begin loading in WebKit.")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        smoke?.fail(stage: "webkit", error: "The WebKit content process terminated.")
    }

    func windowDidFailToEnterFullScreen(_ window: NSWindow) { ownWindow?.fullscreenFailed() }
    func windowDidFailToExitFullScreen(_ window: NSWindow) { ownWindow?.fullscreenFailed() }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard smoke == nil else { return }
        let origin = message.frameInfo.securityOrigin
        guard message.name == "spacesNative", message.frameInfo.isMainFrame,
              origin.protocol == "http", origin.host == "127.0.0.1", origin.port == server.port,
              message.webView === webView,
              let body = message.body as? [String: Any],
              let type = body["type"] as? String, ["copy", "export", "window"].contains(type)
        else { return }
        let id = (body["id"] as? String).map { String($0.prefix(200)) } ?? ""
        if type == "window" {
            guard let action = body["action"] as? String else {
                nativeResponse(type: type, id: id, ok: false, error: "窗口选项无效。")
                return
            }
            ownWindow.request(action: action, value: body["value"] as? String) { [weak self] ok, error in
                guard let self else { return }
                self.publishWindowState()
                self.nativeResponse(type: type, id: id, ok: ok, error: error, windowState: self.ownWindow.state)
            }
            return
        }
        guard let text = body["text"] as? String, text.utf8.count <= 2 * 1024 * 1024 else {
            nativeResponse(type: type, id: id, ok: false, error: "Expected text no larger than 2 MiB.")
            return
        }
        if type == "copy" {
            NSPasteboard.general.clearContents()
            let copied = NSPasteboard.general.setString(text, forType: .string)
            nativeResponse(type: type, id: id, ok: copied, error: copied ? nil : "Could not write to the clipboard.")
            return
        }
        guard !exporting else {
            nativeResponse(type: type, id: id, ok: false, error: "An export dialog is already open.")
            return
        }
        let suppliedName = body["filename"] as? String ?? "space-export.json"
        let name = (suppliedName as NSString).lastPathComponent
            .components(separatedBy: .controlCharacters).joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let panel = NSSavePanel()
        panel.title = "Export Space"
        panel.nameFieldStringValue = name.isEmpty || name == "." || name == ".." ? "space-export.json" : name
        panel.canCreateDirectories = true
        exporting = true
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self else { return }
            self.exporting = false
            guard response == .OK, let url = panel.url else {
                self.nativeResponse(type: type, id: id, ok: false, cancelled: true)
                return
            }
            do {
                try text.write(to: url, atomically: true, encoding: .utf8)
                self.nativeResponse(type: type, id: id, ok: true)
            } catch {
                self.nativeResponse(type: type, id: id, ok: false, error: "Could not save the export file.")
            }
        }
    }

    private func nativeResponse(type: String, id: String, ok: Bool, cancelled: Bool = false, error: String? = nil,
                                windowState: [String: Any]? = nil) {
        var payload: [String: Any] = ["type": type, "id": id, "ok": ok]
        if cancelled { payload["cancelled"] = true }
        if let error { payload["error"] = error }
        if let windowState { payload["window"] = windowState }
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('spaces-native-result', {detail: \(json)}));", completionHandler: nil)
    }

    private func publishWindowState() {
        followMenuItem.state = companion.followsGrok ? .on : .off
        guard smoke == nil, let url = webView.url, url.scheme == "http", url.host == "127.0.0.1",
              url.port == server.port, url.path == "/",
              let data = try? JSONSerialization.data(withJSONObject: ownWindow.state),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.__GROK_SPACES_WINDOW__ = \(json); window.dispatchEvent(new CustomEvent('spaces-native-window', {detail: window.__GROK_SPACES_WINDOW__}));", completionHandler: nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
