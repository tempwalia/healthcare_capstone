from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import correlation_id_var, new_correlation_id


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Reads X-Correlation-Id from the incoming request (or generates one) and
    makes it available to every log line emitted while handling this request —
    including any agent/tool calls it triggers downstream."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-Id") or new_correlation_id()
        token = correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-Id"] = correlation_id
        return response
