from reach_perception.hands import hand_size, pick_closest


def hand(cx: float, cy: float, scale: float) -> list[tuple[float, float, float]]:
    """A crude 21-point hand: wrist at (cx, cy); middle base and knuckles scale with `scale`."""
    pts = [(cx, cy, 0.0)] * 21
    pts = list(pts)
    pts[9] = (cx, cy - 0.2 * scale, 0.0)
    pts[5] = (cx - 0.08 * scale, cy - 0.18 * scale, 0.0)
    pts[17] = (cx + 0.08 * scale, cy - 0.15 * scale, 0.0)
    return pts


def test_larger_hand_is_closer_and_wins():
    near, far = hand(0.3, 0.8, 1.0), hand(0.7, 0.8, 0.5)
    assert hand_size(near, 640, 480) > hand_size(far, 640, 480)
    assert pick_closest([far, near], 640, 480) == 1
    assert pick_closest([near, far], 640, 480) == 0


def test_similar_sizes_stay_with_the_hand_tracked_last_frame():
    a, b = hand(0.3, 0.8, 1.0), hand(0.7, 0.8, 0.95)  # within 15%
    assert pick_closest([a, b], 640, 480, prev_wrist=(0.7, 0.8)) == 1  # keeps b although a is slightly bigger
    assert pick_closest([a, b], 640, 480, prev_wrist=(0.3, 0.8)) == 0


def test_much_closer_hand_overrides_history():
    near, far = hand(0.3, 0.8, 1.0), hand(0.7, 0.8, 0.4)
    assert pick_closest([near, far], 640, 480, prev_wrist=(0.7, 0.8)) == 0


def test_single_hand():
    assert pick_closest([hand(0.5, 0.5, 0.7)], 640, 480) == 0
