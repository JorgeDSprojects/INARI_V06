from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot"
    silver_database_url: str = "postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:14b"

    max_iterations: int = 5
    max_history_messages: int = 40
    query_row_limit: int = 1000
    raw_query_max_range_hours: int = 24

    seed_users: str = "Operario Juan,Ingeniera Maria,Administrador"

    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


settings = Settings()
