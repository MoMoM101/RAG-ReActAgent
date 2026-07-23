"""Global maintenance state for coordinating restore/rebuild operations.

Only one maintenance operation runs at a time. While active, write endpoints
return 503 with Retry-After to prevent data inconsistency.
"""

import asyncio
import contextlib
import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class MaintenancePhase(StrEnum):
    IDLE = "idle"
    VERIFYING = "verifying"
    STAGING = "staging"
    BUILDING = "building"
    SWITCHING = "switching"
    ROLLING_BACK = "rolling_back"
    CLEANING = "cleaning"


class MaintenanceState:
    """Thread-safe singleton tracking maintenance operations."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active = False
        self.phase: MaintenancePhase = MaintenancePhase.IDLE
        self.progress_pct: int = 0
        self.message: str = ""
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self._active

    async def acquire(self) -> bool:
        """Try to acquire the maintenance lock. Returns False if already held."""
        acquired = self._lock.locked()
        if acquired:
            return False
        await self._lock.acquire()
        self._active = True
        self.error = None
        self.progress_pct = 0
        return True

    def release(self):
        """Release the maintenance lock."""
        self._active = False
        self.phase = MaintenancePhase.IDLE
        self.progress_pct = 0
        self.message = ""
        with contextlib.suppress(RuntimeError):
            self._lock.release()

    def update(self, phase: MaintenancePhase, pct: int, message: str = ""):
        self.phase = phase
        self.progress_pct = pct
        self.message = message
        logger.info("maintenance %s (%d%%): %s", phase.value, pct, message)

    def set_error(self, message: str):
        self.error = message
        logger.error("maintenance error: %s", message)

    def snapshot(self) -> dict:
        return {
            "active": self._active,
            "phase": self.phase.value,
            "progress_pct": self.progress_pct,
            "message": self.message,
            "error": self.error,
        }


_maintenance_state: MaintenanceState | None = None


def get_maintenance_state() -> MaintenanceState:
    global _maintenance_state
    if _maintenance_state is None:
        _maintenance_state = MaintenanceState()
    return _maintenance_state
