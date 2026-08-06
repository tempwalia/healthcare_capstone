import asyncio
import sys


def selector_event_loop_factory() -> asyncio.AbstractEventLoop:
    """psycopg3's async mode (the LangGraph Postgres checkpointer's driver)
    cannot run under Windows' default ProactorEventLoop. uvicorn>=0.36
    hardcodes ProactorEventLoop for its built-in "asyncio" loop on win32,
    bypassing `asyncio`'s event-loop *policy* entirely (see
    `uvicorn.loops.asyncio.asyncio_loop_factory`), so setting the policy has
    no effect — uvicorn must be pointed at this factory directly via
    `--loop app.core.event_loop:selector_event_loop_factory`. A no-op
    everywhere except Windows.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
