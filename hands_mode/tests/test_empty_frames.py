from types import SimpleNamespace as NS

from hands_mode import actions, signals


def test_no_hand_or_partial_fingers_never_raise():
    for fr in (NS(fingers={}), NS(fingers=None), NS(), NS(fingers={"index": NS(extended=True)})):
        assert actions.is_pointing(fr) is False
        assert actions.is_index_curl(fr) is False
        assert actions.is_two_finger(fr) is False
        assert signals.is_thumbs_up(fr) is False
        assert signals.is_peace(fr) is False


def test_full_hand_still_works():
    ext = lambda e: NS(extended=e)  # noqa: E731
    pointing = NS(fingers={"index": ext(True), "middle": ext(False), "ring": ext(False), "pinky": ext(False)})
    assert actions.is_pointing(pointing) is True and actions.is_index_curl(pointing) is False
