"""Compatibility alias: implementation resides in ace_anime_discovery."""
from __future__ import annotations
import sys
from ace_anime_discovery.anime_discovery import *
from ace_anime_discovery import anime_discovery as _implementation
if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
