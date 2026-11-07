import AppKit
import CoreGraphics

@MainActor
final class CompanionCoordinator: NSObject {
    var followsGrok = true {
        didSet {
            if !followsGrok {
                hiddenForGrok = false
                window.title = "Grok Spaces"
                window.orderFront(nil)
            }
            refreshPolling()
        }
    }
    private weak var window: NSWindow!
    private var timer: Timer?
    private var pendingRefresh: Task<Void, Never>?
    private var started = false
    private var grok: NSRunningApplication?
    private var hiddenForGrok = false
    private var wasGrokActive = false

    init(window: NSWindow) {
        self.window = window
        super.init()
    }

    func start() {
        guard !started else { return }
        started = true
        let workspace = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didActivateApplicationNotification, NSWorkspace.didLaunchApplicationNotification,
                     NSWorkspace.didTerminateApplicationNotification, NSWorkspace.didHideApplicationNotification,
                     NSWorkspace.didUnhideApplicationNotification] {
            workspace.addObserver(self, selector: #selector(environmentChanged), name: name, object: nil)
        }
        let notifications = NotificationCenter.default
        for name in [NSApplication.didBecomeActiveNotification, NSApplication.didResignActiveNotification,
                     NSApplication.didHideNotification, NSApplication.didUnhideNotification,
                     NSApplication.didChangeScreenParametersNotification] {
            notifications.addObserver(self, selector: #selector(environmentChanged), name: name, object: nil)
        }
        for name in [NSWindow.didMiniaturizeNotification, NSWindow.didDeminiaturizeNotification,
                     NSWindow.didChangeScreenNotification] {
            notifications.addObserver(self, selector: #selector(environmentChanged), name: name, object: window)
        }
        refreshPolling()
    }

    func stop() {
        started = false
        pendingRefresh?.cancel()
        pendingRefresh = nil
        stopTimer()
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func environmentChanged(_ notification: Notification) {
        pendingRefresh?.cancel()
        pendingRefresh = Task { @MainActor [weak self] in
            await Task.yield()
            guard let self, !Task.isCancelled else { return }
            self.refreshPolling()
            do { try await Task.sleep(for: .milliseconds(150)) } catch { return }
            guard !Task.isCancelled else { return }
            self.refreshPolling()
            self.pendingRefresh = nil
        }
    }

    private func refreshPolling() {
        guard started, let window else { return }
        grok = NSRunningApplication.runningApplications(withBundleIdentifier: "com.anysphere.sand").first
        guard followsGrok, !NSApp.isHidden, !window.isMiniaturized else {
            stopTimer()
            return
        }
        if grok?.isHidden == true {
            hiddenForGrok = true
            wasGrokActive = false
            window.orderOut(nil)
            stopTimer()
            return
        }
        if grok == nil {
            window.title = "Grok Spaces"
            restoreIfNeeded(canBringForward: foregroundIsOwn)
            stopTimer()
            return
        }
        let active = foregroundIsGrok || foregroundIsOwn
        guard active else {
            restoreIfNeeded(canBringForward: false)
            wasGrokActive = false
            stopTimer()
            return
        }
        update()
        guard timer == nil else { return }
        let timer = Timer.scheduledTimer(withTimeInterval: 0.35, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.update() }
        }
        timer.tolerance = 0.1
        self.timer = timer
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    func update() {
        guard started, followsGrok, let window, !NSApp.isHidden, !window.isMiniaturized,
              let grok, !grok.isTerminated, !grok.isHidden else { return }
        guard foregroundIsGrok || foregroundIsOwn else { stopTimer(); return }
        guard let target = mainWindowFrame(pid: grok.processIdentifier) else {
            wasGrokActive = foregroundIsGrok
            window.title = "Grok Spaces"
            // Minimized windows and unavailable cross-app metadata cannot be
            // distinguished reliably without Accessibility access.
            restoreIfNeeded(canBringForward: true)
            return
        }
        guard let placement = CompanionGeometry.placement(target: target, panelWidth: window.frame.width,
                                                          screens: NSScreen.screens.map(\.visibleFrame)) else { return }
        window.title = "Grok Spaces"
        if !approximatelyEqual(window.frame, placement.frame) {
            window.setFrame(placement.frame, display: true, animate: false)
        }
        restoreIfNeeded(canBringForward: true)
        if foregroundIsGrok && !wasGrokActive { window.orderFront(nil) }
        wasGrokActive = foregroundIsGrok
        window.level = .normal
    }

    private var foregroundIsGrok: Bool {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.anysphere.sand"
    }

    private var foregroundIsOwn: Bool {
        guard let ownBundle = Bundle.main.bundleIdentifier else { return false }
        return NSWorkspace.shared.frontmostApplication?.bundleIdentifier == ownBundle
    }

    private func restoreIfNeeded(canBringForward: Bool) {
        guard hiddenForGrok else { return }
        hiddenForGrok = false
        if canBringForward { window.orderFront(nil) }
        else { window.orderBack(nil) }
    }

    private func mainWindowFrame(pid: pid_t) -> NSRect? {
        let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        guard let entries = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]],
              let primaryScreen = NSScreen.screens.first else { return nil }
        return entries.compactMap { item -> NSRect? in
            guard (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                  (item[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
                  (item[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1 > 0,
                  let bounds = item[kCGWindowBounds as String] as? NSDictionary,
                  let rectangle = CGRect(dictionaryRepresentation: bounds),
                  rectangle.width >= 240, rectangle.height >= 200 else { return nil }
            return NSRect(x: rectangle.minX, y: primaryScreen.frame.maxY - rectangle.maxY,
                          width: rectangle.width, height: rectangle.height)
        }.max { $0.width * $0.height < $1.width * $1.height }
    }

    private func approximatelyEqual(_ first: NSRect, _ second: NSRect) -> Bool {
        abs(first.minX - second.minX) < 1 && abs(first.minY - second.minY) < 1 &&
        abs(first.width - second.width) < 1 && abs(first.height - second.height) < 1
    }
}
