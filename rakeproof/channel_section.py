"""Compatibility entry point for the horizontal channel reread."""

from .channels import (
    adoption_from_plan,
    channel_section as extract,
    channel_section,
    plan_route,
)

__all__ = ["extract", "channel_section", "plan_route", "adoption_from_plan"]
