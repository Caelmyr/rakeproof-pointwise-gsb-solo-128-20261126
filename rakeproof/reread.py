"""Alternate module name for the evidence-gated transect reread."""

from .channels import *  # noqa: F401,F403
from .channels import channel_section, plan_route

read_transect = channel_section
recommend = plan_route

__all__ = ["channel_section", "read_transect", "plan_route", "recommend"]
