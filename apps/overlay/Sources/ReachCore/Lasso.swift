import CoreGraphics
import Foundation

/// Freehand circle selection: pure maths and a small state machine, so it is unit-testable without UI.
/// Points are global Cocoa coordinates (origin bottom-left of the primary screen, y up) unless said otherwise.
public enum Lasso {
    /// Strokes whose bounding box is smaller than this on both sides are ignored ("draw a bigger circle").
    public static let minExtent: CGFloat = 40
    public static let maxPoints = 64

    public static func boundingBox(_ pts: [CGPoint]) -> CGRect {
        guard let f = pts.first else { return .null }
        var minX = f.x, maxX = f.x, minY = f.y, maxY = f.y
        for p in pts { minX = min(minX, p.x); maxX = max(maxX, p.x); minY = min(minY, p.y); maxY = max(maxY, p.y) }
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }

    public static func isTooSmall(_ pts: [CGPoint]) -> Bool {
        guard pts.count >= 3 else { return true }
        let b = boundingBox(pts)
        return max(b.width, b.height) < minExtent
    }

    /// Ramer-Douglas-Peucker, loosened until at most `maxPoints` remain. The loop is closed implicitly.
    public static func simplify(_ pts: [CGPoint], maxPoints: Int = maxPoints) -> [CGPoint] {
        guard pts.count > 3 else { return pts }
        let b = boundingBox(pts)
        var eps = max(b.width, b.height) / 500
        var out = rdp(pts, eps)
        while out.count > maxPoints {
            eps *= 1.5
            out = rdp(pts, eps)
        }
        return out
    }

    static func rdp(_ pts: [CGPoint], _ eps: CGFloat) -> [CGPoint] {
        guard pts.count > 2 else { return pts }
        var keep = [Bool](repeating: false, count: pts.count)
        keep[0] = true; keep[pts.count - 1] = true
        var stack = [(0, pts.count - 1)]
        while let (a, z) = stack.popLast() {
            guard z > a + 1 else { continue }
            var best = -1.0, idx = a
            for i in (a + 1)..<z {
                let d = Double(distance(pts[i], pts[a], pts[z]))
                if d > best { best = d; idx = i }
            }
            if best > Double(eps) {
                keep[idx] = true
                stack.append((a, idx)); stack.append((idx, z))
            }
        }
        return pts.indices.filter { keep[$0] }.map { pts[$0] }
    }

    static func distance(_ p: CGPoint, _ a: CGPoint, _ b: CGPoint) -> CGFloat {
        let dx = b.x - a.x, dy = b.y - a.y
        let len2 = dx * dx + dy * dy
        guard len2 > 0 else { return hypot(p.x - a.x, p.y - a.y) }
        let t = max(0, min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2))
        return hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
    }

    /// Even-odd point-in-polygon (the loop closes from last point back to first).
    public static func contains(_ poly: [CGPoint], _ p: CGPoint) -> Bool {
        guard poly.count >= 3 else { return false }
        var inside = false
        var j = poly.count - 1
        for i in poly.indices {
            let a = poly[i], b = poly[j]
            if (a.y > p.y) != (b.y > p.y), p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x { inside.toggle() }
            j = i
        }
        return inside
    }

    /// Contract region `[x, y, w, h]` (top-left origin, points) for a Cocoa rect; `primaryTop` is the primary
    /// screen's frame.maxY.
    public static func region(_ r: CGRect, primaryTop: CGFloat) -> [Double] {
        [r.minX, primaryTop - r.maxY, r.width, r.height].map { Double($0) }
    }

    /// Contract `path` `[[x, y], ...]` (top-left origin, points, rounded to 0.5 pt).
    public static func contractPath(_ pts: [CGPoint], primaryTop: CGFloat) -> [[Double]] {
        pts.map { [(Double($0.x) * 2).rounded() / 2, (Double(primaryTop - $0.y) * 2).rounded() / 2] }
    }

    /// The polygon in pixel coordinates of a capture of `box` at `scale` (bitmap context: origin bottom-left).
    public static func toImageSpace(_ pts: [CGPoint], box: CGRect, scale: CGFloat) -> [CGPoint] {
        pts.map { CGPoint(x: ($0.x - box.minX) * scale, y: ($0.y - box.minY) * scale) }
    }

    /// Keeps only the pixels inside the loop; everything outside becomes transparent.
    public static func mask(_ image: CGImage, polygon: [CGPoint]) -> CGImage? {
        guard polygon.count >= 3,
              let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let path = CGMutablePath()
        path.addLines(between: polygon)
        path.closeSubpath()
        ctx.addPath(path)
        ctx.clip()
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return ctx.makeImage()
    }
}

/// The circle-select gesture: idle -> drawing -> closed | tooSmall | cancelled. Logged by `--lasso-selftest`.
public struct LassoRecorder: Equatable, Sendable {
    public enum Outcome: Equatable, Sendable {
        case closed(path: [CGPoint], box: CGRect)
        case tooSmall
        case cancelled
    }
    public enum State: String, Equatable, Sendable { case idle, drawing, closed, tooSmall = "too small", cancelled }

    public private(set) var state: State = .idle
    public private(set) var points: [CGPoint] = []

    public init() {}

    public mutating func begin(_ p: CGPoint) { state = .drawing; points = [p] }

    public mutating func add(_ p: CGPoint) {
        guard state == .drawing else { return }
        if let last = points.last, hypot(p.x - last.x, p.y - last.y) < 1 { return }
        points.append(p)
    }

    /// Mouse up: auto-closes the loop. Returns the simplified polygon and its bounding box, or why not.
    public mutating func end() -> Outcome {
        guard state == .drawing else { state = .cancelled; return .cancelled }
        if Lasso.isTooSmall(points) { state = .tooSmall; return .tooSmall }
        state = .closed
        let path = Lasso.simplify(points)
        return .closed(path: path, box: Lasso.boundingBox(points))  // capture covers the whole stroke
    }

    public mutating func cancel() { state = .cancelled; points = [] }
}

/// One key, two meanings: a quick tap (< `threshold`) opens or closes the panel; press-and-hold talks.
public struct TapOrHold: Equatable, Sendable {
    public enum Action: Equatable, Sendable { case none, tap, startHold, endHold }
    public static let threshold: Double = 0.25
    private var pressedAt: Double?
    private var holding = false
    private var press = 0

    public init() {}

    /// Key down. Returns a token the caller passes back to `holdCheck` after `threshold`.
    public mutating func down(at t: Double) -> Int {
        if pressedAt == nil { pressedAt = t; holding = false; press += 1 }
        return press
    }

    /// Called `threshold` after key down: starts the hold if that same press is still down.
    public mutating func holdCheck(token: Int, at t: Double) -> Action {
        guard token == press, let p = pressedAt, !holding, t - p >= Self.threshold - 0.001 else { return .none }
        holding = true
        return .startHold
    }

    public mutating func up(at t: Double) -> Action {
        guard let p = pressedAt else { return .none }
        pressedAt = nil
        if holding { holding = false; return .endHold }
        return t - p < Self.threshold ? .tap : .none
    }
}
