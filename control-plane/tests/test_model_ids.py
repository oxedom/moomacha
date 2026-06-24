from control_plane.runtime.model_ids import normalize_model_id


def test_bare_openai_model_gets_prefixed():
    assert normalize_model_id("gpt-4o") == "openai:gpt-4o"
    assert normalize_model_id("gpt-4.1") == "openai:gpt-4.1"


def test_provider_prefixed_unchanged():
    assert normalize_model_id("openai:gpt-4o") == "openai:gpt-4o"
    assert normalize_model_id("anthropic:claude-3-5") == "anthropic:claude-3-5"
    assert normalize_model_id("google_genai:gemini-3.1-pro") == "google_genai:gemini-3.1-pro"
