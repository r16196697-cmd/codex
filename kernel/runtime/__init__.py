from .inspect import InspectService
from .modes import MODES, RuntimeModeService, require_mode_permission

__all__ = ["DeterministicRuntime", "InspectService", "MODES", "RuntimeModeService", "require_mode_permission"]


def __getattr__(name):
    if name == "DeterministicRuntime":
        from .service import DeterministicRuntime

        return DeterministicRuntime
    raise AttributeError(name)
