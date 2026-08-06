from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class NoCacheDashboardMiddleware(BaseHTTPMiddleware):
    """The dashboard (`/app/*`) is served by `StaticFiles`, which sets
    `Last-Modified`/`ETag` but no `Cache-Control` — browsers then apply
    heuristic freshness caching and can keep serving an old JS/CSS file for a
    while after it's edited on disk, with no revalidation request at all.
    Fine for a real deployment, actively confusing during iteration on a
    hand-edited, no-build-step frontend — force revalidation on every load."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/app"):
            response.headers["Cache-Control"] = "no-store"
        return response
