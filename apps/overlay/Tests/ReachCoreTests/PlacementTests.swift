import CoreGraphics
import Testing
@testable import ReachCore

@Suite struct PlacementTests {
    let size = CGSize(width: 120, height: 32)

    @Test func hugsTheNotchCentre() {
        // 1512x982 notched laptop: menu bar 37 pt, notch between x 662 and 850 (off-centre on purpose)
        let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
        let visible = CGRect(x: 0, y: 0, width: 1512, height: 945)
        let o = Placement.notchOrigin(screen: screen, visible: visible, safeTop: 37,
                                      auxLeft: CGRect(x: 0, y: 945, width: 662, height: 37),
                                      auxRight: CGRect(x: 850, y: 945, width: 662, height: 37), size: size)
        #expect(o.x == CGFloat(696))  // notch centre 756 minus half the width
        #expect(o.y + size.height == 982 - 37 - Placement.gap)  // just under the notch, never covering it
    }

    @Test func noNotchCentresUnderMenuBar() {
        let screen = CGRect(x: 1512, y: 0, width: 1920, height: 1080)
        let visible = CGRect(x: 1512, y: 0, width: 1920, height: 1055)
        let o = Placement.notchOrigin(screen: screen, visible: visible, safeTop: 0, auxLeft: nil, auxRight: nil, size: size)
        #expect(o.x == 1512 + 960 - 60 && o.y + size.height == 1055 - Placement.gap)
    }

    @Test func clampsIntoVisibleFrame() {
        let v = CGRect(x: 0, y: 0, width: 800, height: 600)
        #expect(Placement.clamp(CGPoint(x: -50, y: 900), size: size, in: v) == CGPoint(x: 0, y: 568))
        #expect(Placement.clamp(CGPoint(x: 790, y: -10), size: size, in: v) == CGPoint(x: 680, y: 0))
        #expect(Placement.clamp(CGPoint(x: 100, y: 100), size: size, in: v) == CGPoint(x: 100, y: 100))
    }

    @Test func restoresOnlyOnAKnownDisplay() {
        let displays = ["1": CGRect(x: 0, y: 0, width: 800, height: 600)]
        let saved = ["1": CGPoint(x: 200, y: 500), "2": CGPoint(x: 3000, y: 500)]
        #expect(Placement.restore(saved: saved, display: "1", displays: displays, size: size) == CGPoint(x: 200, y: 468))
        // display 2 is unplugged: no restore, the caller falls back to the notch
        #expect(Placement.restore(saved: saved, display: "2", displays: displays, size: size) == nil)
        // saved off-screen on a display that shrank: clamped back in
        let small = ["1": CGRect(x: 0, y: 0, width: 300, height: 200)]
        #expect(Placement.restore(saved: saved, display: "1", displays: small, size: size) == CGPoint(x: 180, y: 168))
    }

    @Test func growsDownwardKeepingTop() {
        let old = CGRect(x: 100, y: 900, width: 120, height: 32)
        #expect(Placement.keepTop(old: old, newSize: CGSize(width: 420, height: 300)) == CGPoint(x: -50, y: 632))
        let v = CGRect(x: 0, y: 0, width: 1000, height: 932)
        #expect(Placement.keepTop(old: old, newSize: CGSize(width: 420, height: 300), visible: v) == CGPoint(x: 0, y: 632))
    }
}
