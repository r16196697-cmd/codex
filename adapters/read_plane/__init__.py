"""Transport-neutral, instance-bound local semantic reads."""

from .service import LocalReadPlane, ReadPlaneError

__all__ = ["LocalReadPlane", "ReadPlaneError"]
