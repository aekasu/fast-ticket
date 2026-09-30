import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

EventHandler = Callable[[dict, AsyncSession], Awaitable[None]]


class EventRegistry:
    """Registry mapping event type strings to handler callables.

    Multiple handlers can be registered for the same event type; they are
    called sequentially in registration order.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def register(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)
            logger.debug("Registered event handler for %s: %s", event_type, handler.__qualname__)

    async def dispatch(self, event_type: str, payload: dict, session: AsyncSession) -> None:
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            logger.info("No handlers registered for event type '%s' — ignoring.", event_type)
            return
        for handler in tuple(handlers):
            try:
                await handler(payload, session)
            except Exception:
                # one broken handler must not stop the others (or the delivery that follows)
                logger.exception("Event handler %s failed for '%s'", getattr(handler, "__qualname__", handler), event_type)


# Module-level singletons — import these rather than constructing new instances.
registry = EventRegistry()
# Alias for backwards-compatible imports in tests.
event_registry = registry
