"""Compatibility alias: implementation resides in ace_anime_discovery."""
from __future__ import annotations
import sys
from ace_anime_discovery.anime_crawler import *
from ace_anime_discovery import anime_crawler as _implementation
sys.modules[__name__] = _implementation
