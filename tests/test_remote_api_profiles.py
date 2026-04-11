from backend.App.orchestration.application.pipeline_graph import _remote_api_client_kwargs_for_role


def test_remote_profile_overrides_global():
    state = {
        "agent_config": {
            "remote_api": {
                "provider": "anthropic",
                "api_key": "global-key",
            },
            "remote_api_profiles": {
                "g": {"provider": "gemini", "api_key": "g-key", "base_url": "https://x/v1beta/openai/"},
            },
        }
    }
    base_only = _remote_api_client_kwargs_for_role(state, {})
    assert base_only.get("remote_provider") == "anthropic"
    merged = _remote_api_client_kwargs_for_role(state, {"remote_profile": "g"})
    assert merged.get("remote_provider") == "gemini"
    assert merged.get("remote_api_key") == "g-key"
    assert "v1beta" in (merged.get("remote_base_url") or "")
