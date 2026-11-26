"""Alias for exact-caliber channel point/transect reading."""

from .channels import *  # noqa: F401,F403
from .channels import channel_section
from .probe import channel_review

read_channel_section = channel_section
inspect_channel = channel_review

__all__ = ["channel_section", "read_channel_section", "channel_review", "inspect_channel"]
