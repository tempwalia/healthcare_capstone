from typing import Any, List

from fastapi import Request


def build_page(request: Request, items: List[Any], total: int, skip: int, limit: int) -> dict:
    next_link = None
    if skip + limit < total:
        next_link = str(request.url.include_query_params(skip=skip + limit, limit=limit))
    return {"items": items, "total": total, "skip": skip, "limit": limit, "next": next_link}
