"""UTF-8 WebVTT subtitle profile."""

from . import subtitles
from .subtitles import OPS, edit, inspect, serialize, summary

CONTRACT = subtitles.contract("vtt")
__all__ = ["CONTRACT", "OPS", "edit", "inspect", "parse", "serialize", "summary"]


def parse(data: bytes):
    return subtitles.parse(data, "vtt")
