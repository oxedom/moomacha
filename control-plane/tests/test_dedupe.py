from control_plane.dedupe import SeenMessages


def test_first_seen_returns_true_then_false():
    seen = SeenMessages()

    assert seen.mark(112) is True
    assert seen.mark(112) is False


def test_distinct_ids_are_independent():
    seen = SeenMessages()

    assert seen.mark(1) is True
    assert seen.mark(2) is True
    assert seen.mark(1) is False
