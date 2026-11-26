"""Alias for channel-aware horizontal profile planning."""

from .channels import *  # noqa: F401,F403
from .channels import channel_section, plan_route

channel_profile = channel_section
extract = channel_section
plan_channel_route = plan_route

__all__ = ["channel_section", "channel_profile", "extract", "plan_route", "plan_channel_route"]
