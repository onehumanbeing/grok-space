import AppKit
import CoreGraphics

/// Controls only this application's window. Display IDs always come from NSScreen.
@MainActor
final class OwnWindowCoordinator: NSObject {
    private enum Operation { case mode(String), display(String) }
    private weak var window: NSWindow!
    private let companion: CompanionCoordinator
    var onChange: (() -> Void)?
    private var pending: Operation?
    private var completion: ((Bool, String?) -> Void)?
    private var transitionTimeout: Task<Void, Never>?
    private var transitioning = false
    private var companionWidth: CGFloat = 420
    private var standaloneFrame: NSRect?
    private var modeBeforeEnteringFullscreen: String?

    init(window: NSWindow, companion: CompanionCoordinator) {
        self.window = window
        self.companion = companion
        super.init()
        window.collectionBehavior.insert(.fullScreenPrimary)
        let center = NotificationCenter.default
        for name in [NSWindow.willEnterFullScreenNotification, NSWindow.willExitFullScreenNotification,
                     NSWindow.didEnterFullScreenNotification, NSWindow.didExitFullScreenNotification,
                     NSWindow.didChangeScreenNotification] {
            center.addObserver(self, selector: #selector(windowChanged), name: name, object: window)
        }
        center.addObserver(self, selector: #selector(windowChanged), name: NSApplication.didChangeScreenParametersNotification, object: nil)
    }

    func stop() {
        transitionTimeout?.cancel()
        NotificationCenter.default.removeObserver(self)
    }

    var state: [String: Any] {
        let screens = NSScreen.screens
        let displays: [[String: Any]] = screens.enumerated().compactMap { index, screen in
            guard let id = displayID(screen), let number = UInt32(id) else { return nil }
            let builtIn = CGDisplayIsBuiltin(number) != 0
            return ["id": id, "name": builtIn ? "内建显示器" : "显示器\(index + 1)",
                    "is_main": index == 0, "is_builtin": builtIn]
        }
        return ["mode": mode, "display_id": window.screen.flatMap(displayID) ?? "", "displays": displays]
    }

    private var mode: String {
        if window.styleMask.contains(.fullScreen) { return "fullscreen" }
        return companion.followsGrok ? "companion" : "standalone"
    }

    func request(action: String, value: String?, completion: @escaping (Bool, String?) -> Void) {
        if action == "get" { completion(true, nil); return }
        guard !transitioning, pending == nil else { completion(false, "窗口正在切换，请稍候。"); return }
        let operation: Operation
        if action == "mode", let value, ["companion", "standalone", "fullscreen"].contains(value) {
            operation = .mode(value)
        } else if action == "display", let value, screen(id: value) != nil {
            operation = .display(value)
        } else { completion(false, "窗口选项无效，请重新选择。"); return }
        if case .mode(let requested) = operation, requested == mode { completion(true, nil); return }

        if case .mode("fullscreen") = operation {
            guard !window.styleMask.contains(.fullScreen) else { completion(true, nil); return }
            modeBeforeEnteringFullscreen = mode
            rememberFrame()
            companion.followsGrok = false
            beginTransition(operation, completion: completion)
            window.toggleFullScreen(nil)
        } else if window.styleMask.contains(.fullScreen) {
            beginTransition(operation, completion: completion)
            window.toggleFullScreen(nil)
        } else {
            let error = apply(operation)
            onChange?()
            completion(error == nil, error)
        }
    }

    func fullscreenFailed() {
        transitioning = false
        if !window.styleMask.contains(.fullScreen), let previous = modeBeforeEnteringFullscreen {
            _ = apply(.mode(previous))
        }
        modeBeforeEnteringFullscreen = nil
        finish(false, "全屏切换未完成，请重试。"); onChange?()
    }

    private func beginTransition(_ operation: Operation, completion: @escaping (Bool, String?) -> Void) {
        pending = operation
        self.completion = completion
        transitionTimeout?.cancel()
        transitionTimeout = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(5)) } catch { return }
            guard let self, self.pending != nil else { return }
            self.transitioning = false
            self.finish(false, "窗口切换超时，请使用窗口左上角按钮重试。")
        }
    }

    @objc private func windowChanged(_ notification: Notification) {
        switch notification.name {
        case NSWindow.willEnterFullScreenNotification:
            transitioning = true
            if pending == nil {
                modeBeforeEnteringFullscreen = companion.followsGrok ? "companion" : "standalone"
                rememberFrame()
            }
            companion.followsGrok = false
        case NSWindow.willExitFullScreenNotification:
            transitioning = true
        case NSWindow.didEnterFullScreenNotification:
            transitioning = false
            modeBeforeEnteringFullscreen = nil
            finish(true, nil)
        case NSWindow.didExitFullScreenNotification:
            transitioning = false
            let error = apply(pending ?? .mode("standalone"))
            finish(error == nil, error)
        default: break
        }
        onChange?()
    }

    private func rememberFrame() {
        if companion.followsGrok { companionWidth = window.frame.width }
        else if !window.styleMask.contains(.fullScreen) { standaloneFrame = window.frame }
    }

    private func apply(_ operation: Operation) -> String? {
        switch operation {
        case .mode("companion"):
            if !companion.followsGrok { standaloneFrame = window.frame }
            var frame = window.frame
            frame.size.width = companionWidth
            window.setFrame(frame, display: true)
            companion.followsGrok = true
        case .mode("standalone"):
            let wasCompanion = companion.followsGrok
            if wasCompanion { companionWidth = window.frame.width }
            companion.followsGrok = false
            if let screen = window.screen ?? NSScreen.screens.first {
                let desired = standaloneFrame ?? NSRect(x: window.frame.minX, y: window.frame.minY,
                                                       width: max(window.frame.width, 960), height: max(window.frame.height, 700))
                window.setFrame(fitted(desired, to: screen.visibleFrame), display: true)
            }
        case .display(let id):
            guard let screen = screen(id: id) else { return "显示器已断开，请重新选择。" }
            rememberFrame()
            companion.followsGrok = false
            let safe = screen.visibleFrame
            let desired = NSRect(x: safe.midX - max(window.frame.width, 960) / 2,
                                 y: safe.midY - max(window.frame.height, 700) / 2,
                                 width: max(window.frame.width, 960), height: max(window.frame.height, 700))
            let frame = fitted(desired, to: safe)
            window.setFrame(frame, display: true)
            standaloneFrame = frame
        default: return "窗口选项无效。"
        }
        window.title = "Grok Spaces"
        return nil
    }

    private func finish(_ ok: Bool, _ error: String?) {
        transitionTimeout?.cancel()
        transitionTimeout = nil
        let handler = completion
        completion = nil
        pending = nil
        handler?(ok, error)
    }

    private func displayID(_ screen: NSScreen) -> String? {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.stringValue
    }

    private func screen(id: String) -> NSScreen? { NSScreen.screens.first { displayID($0) == id } }

    private func fitted(_ desired: NSRect, to safe: NSRect) -> NSRect {
        let width = min(max(desired.width, 320), safe.width)
        let height = min(max(desired.height, 280), safe.height)
        return NSRect(x: min(max(desired.minX, safe.minX), safe.maxX - width),
                      y: min(max(desired.minY, safe.minY), safe.maxY - height), width: width, height: height)
    }
}
