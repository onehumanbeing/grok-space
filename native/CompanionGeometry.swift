import Foundation
import CoreGraphics

/// Pure layout: coordinates and display bounds are supplied by the caller.
enum CompanionGeometry {
    struct Placement {
        let frame: NSRect
        let overlapsTarget: Bool
    }

    static func placement(target: NSRect, panelWidth: CGFloat, screens: [NSRect]) -> Placement? {
        let screens = screens.filter { $0.width > 0 && $0.height > 0 }
        guard let home = screens.max(by: { area($0.intersection(target)) < area($1.intersection(target)) }) else { return nil }
        let width = min(max(panelWidth, 320), home.width)
        let gap: CGFloat = 8
        let orderedScreens = [home] + screens.filter { $0 != home }

        // A neighboring display can provide space beside a full-width Grok
        // window. Try those actual side positions before overlapping Grok.
        for x in [target.maxX + gap, target.minX - gap - width] {
            for screen in orderedScreens where x >= screen.minX && x + width <= screen.maxX {
                let frame = clampedFrame(x: x, target: target, width: width, screen: screen)
                let verticalOverlap = min(frame.maxY, target.maxY) - max(frame.minY, target.minY)
                if verticalOverlap >= min(100, target.height / 2) {
                    return Placement(frame: frame, overlapsTarget: false)
                }
            }
        }
        let rightRoom = home.maxX - target.maxX - gap
        let leftRoom = target.minX - home.minX - gap
        let x = rightRoom >= leftRoom ? target.maxX + gap : target.minX - gap - width
        let frame = clampedFrame(x: x, target: target, width: width, screen: home)
        return Placement(frame: frame, overlapsTarget: area(frame.intersection(target)) > 0)
    }

    private static func clampedFrame(x: CGFloat, target: NSRect, width: CGFloat, screen: NSRect) -> NSRect {
        let height = min(max(target.height, 280), screen.height)
        return NSRect(x: min(max(x, screen.minX), screen.maxX - width),
                      y: min(max(target.maxY - height, screen.minY), screen.maxY - height),
                      width: width, height: height)
    }

    private static func area(_ rect: NSRect) -> CGFloat {
        rect.isNull ? 0 : rect.width * rect.height
    }
}
