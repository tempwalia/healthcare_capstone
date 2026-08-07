from app.core.config import settings


class StubChatModel:
    """Deterministic fallback used when no LLM is configured — ADR-005.

    Not actually invoked: every node checks `isinstance(llm, StubChatModel)`
    and calls its own rule-based/template logic instead, so this class exists
    purely as a type marker for that branch.
    """

    def __init__(self, task: str):
        self.task = task

    def invoke(self, *_args, **_kwargs):
        raise NotImplementedError("stub model is only used through task-specific node fallbacks")


def get_chat_model(task: str):
    """The single seam that constructs an LLM client (ADR-005) — tests force
    the stub path by setting `settings.llm_enabled = False`, and every node
    is unit-testable without network access as a result.

    Groq via its OpenAI-compatible endpoint, not Anthropic/OpenAI directly —
    this project's `.env` only ever configures `llm_base_url`/`llmgw_api_key`.
    """
    if settings.llm_enabled and settings.llmgw_api_key:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llmgw_api_key,
            base_url=settings.llm_base_url,
            temperature=0,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    return StubChatModel(task)
