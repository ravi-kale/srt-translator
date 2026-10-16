from subtrans.config import Settings
from subtrans.llm.factory import ClientFactory, missing_keys, stage_model, stage_providers
from subtrans.models import RunConfig

ONLY_OPENROUTER = {"deepseek": False, "claude": False, "gemini": False, "openrouter": True}


def _settings(**keys: str) -> Settings:
    empty = dict.fromkeys(("deepseek_api_key", "anthropic_api_key", "gemini_api_key", "openrouter_api_key"), "")
    return Settings(_env_file=None, **{**empty, **keys})


def test_hybrid_routes_every_keyless_stage_through_openrouter():
    cfg = RunConfig(mode="hybrid", stage1_provider="gemini", stage3_provider="claude")
    assert stage_providers(cfg, ONLY_OPENROUTER) == {1: "openrouter", 2: "openrouter", 3: "openrouter"}
    assert [stage_model(cfg, s, ONLY_OPENROUTER) for s in (1, 2, 3)] == [
        "google/gemini-4-argon",
        "deepseek/deepseek-v4.1-flash",
        "anthropic/claude-sonnet-5.5",
    ]


def test_openrouter_model_follows_the_chosen_provider_not_the_stage():
    cfg = RunConfig(mode="hybrid", stage1_provider="deepseek", stage3_provider="gemini")
    assert stage_model(cfg, 1, ONLY_OPENROUTER) == "deepseek/deepseek-v4.1-flash"
    assert stage_model(cfg, 3, ONLY_OPENROUTER) == "google/gemini-4-argon"


def test_direct_keys_win_over_openrouter():
    keys = {**ONLY_OPENROUTER, "deepseek": True}
    cfg = RunConfig(mode="hybrid")
    assert stage_providers(cfg, keys) == {1: "openrouter", 2: "deepseek", 3: "openrouter"}
    assert stage_model(cfg, 2, keys) == cfg.deepseek_model


def test_all_deepseek_falls_back_to_openrouter():
    cfg = RunConfig(mode="all_deepseek")
    assert set(stage_providers(cfg, ONLY_OPENROUTER).values()) == {"openrouter"}
    assert {stage_model(cfg, s, ONLY_OPENROUTER) for s in (1, 2, 3)} == {"deepseek/deepseek-v4.1-flash"}


def test_missing_keys_only_when_openrouter_absent_too():
    cfg = RunConfig(mode="hybrid")
    assert missing_keys(cfg, _settings(openrouter_api_key="or-key")) == []
    assert missing_keys(cfg, _settings()) == ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"]


def test_build_uses_openrouter_client_for_keyless_stage():
    factory = ClientFactory(lambda: _settings(openrouter_api_key="or-key"))
    client = factory.build(3, RunConfig(mode="hybrid"))
    assert client.provider == "openrouter"
    assert client.model == "anthropic/claude-sonnet-5.5"


def test_deepseek_via_openrouter_has_thinking_off_in_every_stage():
    factory = ClientFactory(lambda: _settings(openrouter_api_key="or-key"))
    for stage in (1, 2, 3):
        client = factory.build(stage, RunConfig(mode="all_deepseek"))
        assert client.extra_body["reasoning"] == {"enabled": False}
    claude = factory.build(3, RunConfig(mode="hybrid"))
    assert claude.extra_body["reasoning"] == {"effort": "medium"}
