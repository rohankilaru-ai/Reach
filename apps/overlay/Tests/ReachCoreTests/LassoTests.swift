import CoreGraphics
import Foundation
import Testing
@testable import ReachCore

@Suite struct LassoTests {
    func circle(_ c: CGPoint, _ r: CGFloat, n: Int = 400) -> [CGPoint] {
        (0..<n).map { i in
            let a = CGFloat(i) / CGFloat(n) * 2 * .pi
            return CGPoint(x: c.x + r * cos(a), y: c.y + r * sin(a))
        }
    }

    @Test func simplifiesToAtMost64AndKeepsShape() {
        let raw = circle(CGPoint(x: 500, y: 400), 120)
        let s = Lasso.simplify(raw)
        #expect(s.count <= 64 && s.count >= 8)
        #expect(Lasso.contains(s, CGPoint(x: 500, y: 400)))
        #expect(!Lasso.contains(s, CGPoint(x: 500 + 130, y: 400)))
        let b = Lasso.boundingBox(s)
        #expect(abs(b.width - 240) < 6 && abs(b.height - 240) < 6)
        // a straight-ish line collapses to its ends
        #expect(Lasso.simplify((0...100).map { CGPoint(x: CGFloat($0), y: 0) }).count == 2)
    }

    @Test func rejectsTinyStrokes() {
        #expect(Lasso.isTooSmall(circle(.zero, 15)))            // 30 pt across
        #expect(!Lasso.isTooSmall(circle(.zero, 25)))           // 50 pt across
        #expect(Lasso.isTooSmall([CGPoint(x: 0, y: 0), CGPoint(x: 100, y: 0)]))  // fewer than 3 points
        var r = LassoRecorder()
        r.begin(.zero)
        for p in circle(.zero, 10) { r.add(p) }
        #expect(r.end() == .tooSmall && r.state == .tooSmall)
    }

    @Test func recorderClosesLoopAndCancels() {
        var r = LassoRecorder()
        #expect(r.state == .idle)
        let pts = circle(CGPoint(x: 300, y: 300), 80)
        r.begin(pts[0])
        for p in pts.dropFirst() { r.add(p) }
        #expect(r.state == .drawing)
        guard case .closed(let path, let box) = r.end() else { Issue.record("not closed"); return }
        #expect(r.state == .closed && path.count <= Lasso.maxPoints)
        #expect(abs(box.minX - 220) < 1 && abs(box.maxY - 380) < 1)
        var c = LassoRecorder()
        c.begin(.zero); c.cancel()
        #expect(c.state == .cancelled && c.end() == .cancelled)
    }

    @Test func boundingBoxAndContractCoordinates() {
        let box = CGRect(x: 100, y: 700, width: 200, height: 100)   // Cocoa, primary screen 982 tall
        #expect(Lasso.region(box, primaryTop: 982) == [100, 182, 200, 100])
        #expect(Lasso.contractPath([CGPoint(x: 100.26, y: 800)], primaryTop: 982) == [[100.5, 182]])
        #expect(Lasso.toImageSpace([CGPoint(x: 150, y: 750)], box: box, scale: 2) == [CGPoint(x: 100, y: 100)])
    }

    @Test func maskKeepsOnlyInsideTheLoop() throws {
        let w = 40, h = 40
        let ctx = try #require(CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                         space: CGColorSpaceCreateDeviceRGB(),
                                         bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        ctx.setFillColor(CGColor(red: 1, green: 0, blue: 0, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        let img = try #require(ctx.makeImage())
        let tri = [CGPoint(x: 0, y: 0), CGPoint(x: 40, y: 0), CGPoint(x: 0, y: 40)]   // lower-left triangle
        let masked = try #require(Lasso.mask(img, polygon: tri))
        let data = try #require(masked.dataProvider?.data as Data?)
        func alpha(_ x: Int, _ yBottom: Int) -> UInt8 {
            let row = h - 1 - yBottom   // CGImage rows run top to bottom
            return data[row * masked.bytesPerRow + x * 4 + 3]
        }
        #expect(alpha(5, 5) == 255)    // inside
        #expect(alpha(35, 35) == 0)    // outside: transparent
    }

    @Test func selectionEncodesPath() throws {
        let json = try ClientMessage.ask(text: "q", selection: Selection(region: [1, 2, 3, 4], path: [[1, 2], [3, 4], [5, 6]])).json()
        let v = try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
        #expect(v["selection"]?["path"] == .array([.array([.number(1), .number(2)]), .array([.number(3), .number(4)]),
                                                   .array([.number(5), .number(6)])]))
    }
}

@Suite struct TapOrHoldTests {
    @Test func quickTapToggles() {
        var k = TapOrHold()
        let tok = k.down(at: 0)
        #expect(k.up(at: 0.1) == .tap)
        #expect(k.holdCheck(token: tok, at: 0.25) == .none)   // the late timer of a finished tap does nothing
    }

    @Test func holdTalksUntilRelease() {
        var k = TapOrHold()
        let tok = k.down(at: 0)
        _ = k.down(at: 0.1)                                    // key repeat is ignored
        #expect(k.holdCheck(token: tok, at: 0.25) == .startHold)
        #expect(k.up(at: 2) == .endHold)
        #expect(k.up(at: 2.1) == .none)
    }

    @Test func staleTokenFromEarlierPressIsIgnored() {
        var k = TapOrHold()
        let first = k.down(at: 0)
        #expect(k.up(at: 0.05) == .tap)
        _ = k.down(at: 0.2)
        #expect(k.holdCheck(token: first, at: 0.25) == .none)
    }
}

@Suite struct SubmitGuardTests {
    @Test func oneAskAtATimeAndNeverWhileListeningOrReading() {
        var s = PanelState()
        #expect(s.submitBlock(listening: false, capturing: false) == nil)
        #expect(s.submitBlock(listening: true, capturing: false)?.contains("listening") == true)
        #expect(s.submitBlock(listening: false, capturing: true)?.contains("reading") == true)
        s.submitted()
        #expect(s.submitBlock(listening: false, capturing: false)?.contains("already running") == true)
        s.apply(.accepted(taskID: "t"))
        s.apply(try! ServerMessage.decode(#"{"type":"receipt","task_id":"t","status":"ok","reason":"completed","system1_asks":0,"tokens":0,"duration_ms":1}"#))
        #expect(s.submitBlock(listening: false, capturing: false) == nil)
    }
}
