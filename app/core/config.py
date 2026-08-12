from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Intelligent Care Coordination & Referral Management Platform"
    debug: bool = False
    version: str = "1.0.0"

    # Database
    database_url: str = Field(...)

    # JWT
    secret_key: str = Field(...)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # CORS
    backend_cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:8080",
    ]

    # LLM (Groq OpenAI-compatible endpoint by default). Agents fall back to
    # deterministic rule-based logic whenever llm_enabled is False or the key
    # is blank — see app/agents/llm.py (Phase 6). This keeps the platform fully
    # functional, and cheap to run, without any LLM credentials configured.
    llm_enabled: bool = False
    llmgw_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_max_tokens_extraction: int = 300
    llm_max_tokens_summary: int = 400
    llm_max_tokens_chat: int = 500
    # A slow/hanging Groq response previously had no app-level cutoff — since
    # every LLM-calling node runs from a FastAPI BackgroundTask on the same
    # single-threaded event loop, an unbounded call there stalls every other
    # concurrent request too, not just the one referral. Bounded here, once,
    # for every node built via get_chat_model rather than per-call.
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1

    # Agent orchestration (Phase 6). The mocked external systems (payer,
    # provider directory, scheduling, notification) are mounted in-process
    # but the agent layer still reaches them over real loopback HTTP, since
    # MCP's streamable_http transport needs an actual socket, not an ASGI
    # transport.
    mock_base_url: str = "http://127.0.0.1:8000"

    # Phase 9: the conversational assistant's own MCP client reaches this
    # same process's own /mcp mount over real loopback HTTP, same reasoning
    # as mock_base_url above.
    api_base_url: str = "http://127.0.0.1:8000"

    # The local policy knowledge base's MCP server (app.mount("/kb", ...) in
    # app/main.py) — same in-process-but-real-loopback-HTTP reasoning as
    # mock_base_url/api_base_url above, kept as its own setting rather than
    # reusing one of those so it can point elsewhere without conflating "the
    # mocked external systems" or "this app's own authenticated API" with
    # "first-party public reference content."
    kb_base_url: str = "http://127.0.0.1:8000"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def database_url_psycopg(self) -> str:
        """LangGraph's Postgres checkpointer uses psycopg3, not this app's
        asyncpg engine — same database, different driver prefix."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


settings = Settings()
