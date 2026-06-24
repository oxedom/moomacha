from control_plane.services.model_era import model_era_for


def test_known_eras():
    assert model_era_for("gpt-4o") == "gpt-4o"
    assert model_era_for("claude-opus-4-7") == "opus-4.x"
    assert model_era_for("claude-opus-4-6") == "opus-4.x"


def test_sonnet_and_haiku_eras():
    assert model_era_for("claude-sonnet-4-6") == "sonnet-4.x"
    assert model_era_for("claude-haiku-4-5-20251001") == "haiku-4.x"


def test_unknown_model_returns_the_model_id():
    assert model_era_for("some-future-model") == "some-future-model"


def test_empty_model_id():
    assert model_era_for("") == ""
