import CoreGraphics

/// Where the panel sits. Pure maths in AppKit coordinates (origin bottom-left, y up), so it is unit-testable.
public enum Placement {
    /// Gap between the menu bar / notch and the panel's top edge.
    public static let gap: CGFloat = 6

    /// Top-center origin under the menu bar, centred on the notch when the screen has one.
    /// - `safeTop`: `NSScreen.safeAreaInsets.top` (the notch height; 0 without a notch).
    /// - `auxLeft` / `auxRight`: `auxiliaryTopLeftArea` / `auxiliaryTopRightArea` (the menu-bar areas either side of
    ///   the notch). The notch is the gap between them.
    public static func notchOrigin(screen: CGRect, visible: CGRect, safeTop: CGFloat, auxLeft: CGRect?, auxRight: CGRect?,
                                   size: CGSize) -> CGPoint {
        let menuBar = max(screen.maxY - visible.maxY, safeTop)
        let top = screen.maxY - menuBar - gap
        var centerX = screen.midX
        if let l = auxLeft, let r = auxRight, r.minX > l.maxX {
            centerX = (l.maxX + r.minX) / 2
        }
        return clamp(CGPoint(x: centerX - size.width / 2, y: top - size.height), size: size, in: visible)
    }

    /// Keeps the whole panel inside `visible` (it never hides under the menu bar or off screen).
    public static func clamp(_ origin: CGPoint, size: CGSize, in visible: CGRect) -> CGPoint {
        let x = min(max(origin.x, visible.minX), max(visible.minX, visible.maxX - size.width))
        let y = min(max(origin.y, visible.minY), max(visible.minY, visible.maxY - size.height))
        return CGPoint(x: x, y: y)
    }

    /// A saved top-left point for `display`, clamped to that display's visible frame; nil when there is none or the
    /// display is gone (the caller then uses `notchOrigin` on the main screen).
    public static func restore(saved: [String: CGPoint], display: String, displays: [String: CGRect],
                               size: CGSize) -> CGPoint? {
        guard let topLeft = saved[display], let visible = displays[display] else { return nil }
        return clamp(CGPoint(x: topLeft.x, y: topLeft.y - size.height), size: size, in: visible)
    }

    /// The panel is placed by its top-centre anchor, so pill <-> panel cycles never drift.
    public static func origin(anchor: CGPoint, size: CGSize, visible: CGRect?) -> CGPoint {
        let o = CGPoint(x: (anchor.x - size.width / 2).rounded(), y: (anchor.y - size.height).rounded())
        return visible.map { clamp(o, size: size, in: $0) } ?? o
    }

    /// Top-centre anchor under the notch / menu bar.
    public static func notchAnchor(screen: CGRect, visible: CGRect, safeTop: CGFloat, auxLeft: CGRect?,
                                   auxRight: CGRect?) -> CGPoint {
        let o = notchOrigin(screen: screen, visible: visible, safeTop: safeTop, auxLeft: auxLeft, auxRight: auxRight,
                            size: .zero)
        return CGPoint(x: o.x, y: o.y)
    }

    /// A saved anchor for `display`, kept inside that display's visible frame; nil when the display is gone.
    public static func restoreAnchor(saved: [String: CGPoint], display: String, displays: [String: CGRect]) -> CGPoint? {
        guard let a = saved[display], let v = displays[display] else { return nil }
        return CGPoint(x: min(max(a.x, v.minX), v.maxX), y: min(max(a.y, v.minY), v.maxY))
    }

    /// When the content changes size, keep the top edge and the horizontal centre (the pill grows downward from
    /// where it is), then clamp into `visible` when known.
    public static func keepTop(old: CGRect, newSize: CGSize, visible: CGRect? = nil) -> CGPoint {
        let o = CGPoint(x: (old.midX - newSize.width / 2).rounded(), y: (old.maxY - newSize.height).rounded())
        return visible.map { clamp(o, size: newSize, in: $0) } ?? o
    }
}
