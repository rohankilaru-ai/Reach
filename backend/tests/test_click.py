import asyncio

from reach.tools import click


def run(coro):
    return asyncio.run(coro)


async def nosleep(_):
    return None


def test_refuses_spending_sending_deleting_and_empty():
    for t in ("the Buy now button", "delete this email", "Send", "confirm purchase", "sign out", ""):
        assert click.refusal(t, "t1") is not None
    assert click.refusal("the first search result", "t1") is None


def test_clicks_once_at_the_found_point_and_counts():
    click._clicks.clear()
    posted = []
    out, text = run(click.click_target("the first result", "t2", find=lambda t: (100.0, 200.0, "Chrome"),
                                       pos=lambda: (5.0, 5.0), click=lambda x, y: posted.append((x, y)),
                                       emit=None, sleep=nosleep))
    assert out == "clicked" and posted == [(100.0, 200.0)] and "Look at the screen" in text


def test_not_found_clicks_nothing():
    posted = []
    out, _ = run(click.click_target("a unicorn", "t3", find=lambda t: None, pos=lambda: (0.0, 0.0),
                                    click=lambda x, y: posted.append(1), sleep=nosleep))
    assert out == "not_found" and not posted


def test_mouse_moved_during_the_pointer_aborts():
    seq = iter([(0.0, 0.0), (50.0, 60.0)])
    posted = []
    out, text = run(click.click_target("the play button", "t4", find=lambda t: (10.0, 10.0, ""),
                                       pos=lambda: next(seq), click=lambda x, y: posted.append(1), sleep=nosleep))
    assert out == "aborted" and not posted and "moved the mouse" in text


def test_budget_per_ask():
    click._clicks.clear()
    for _ in range(click.MAX_CLICKS_PER_ASK):
        o, _ = run(click.click_target("a link", "t5", find=lambda t: (1.0, 1.0, ""), pos=lambda: (0.0, 0.0),
                                      click=lambda x, y: None, sleep=nosleep))
        assert o == "clicked"
    o, _ = run(click.click_target("a link", "t5", find=lambda t: (1.0, 1.0, ""), pos=lambda: (0.0, 0.0),
                                  click=lambda x, y: None, sleep=nosleep))
    assert o == "refused"
