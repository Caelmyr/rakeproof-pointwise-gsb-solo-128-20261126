"""Vocabulary alias for peak-distance/width helpers."""

from .groove import *  # noqa: F401,F403
from .groove import SCHEMA, groove_at, trace_groove, validate_groove

measure_width = trace_groove

__all__ = ["trace_groove", "measure_width", "groove_at", "validate_groove", "SCHEMA"]
