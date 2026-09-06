from app.config import Settings


def test_defaults_when_env_is_empty(monkeypatch):
    for key in [
        "DATABASE_URL", "SILVER_DATABASE_URL", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "MAX_ITERATIONS", "MAX_HISTORY_MESSAGES", "QUERY_ROW_LIMIT", "RAW_QUERY_MAX_RANGE_HOURS",
        "SEED_USERS", "LANGFUSE_ENABLED", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    assert settings.max_iterations == 5
    assert settings.max_history_messages == 40
    assert settings.query_row_limit == 1000
    assert settings.raw_query_max_range_hours == 24
    assert settings.langfuse_enabled is False
    assert settings.seed_users == "Operario Juan,Ingeniera Maria,Administrador"


def test_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "3")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.max_iterations == 3
    assert settings.langfuse_enabled is True
