"""Vocabulary alias for the coupled groove wall tracker."""

from .groove import *  # noqa: F401,F403
from .groove import SCHEMA, groove_at, trace_groove, validate_groove

track_groove = trace_groove

__all__ = ["trace_groove", "track_groove", "groove_at", "validate_groove", "SCHEMA"]
