"""Compatibility alias: implementation resides in ace_anime_discovery."""
from __future__ import annotations
import sys
from ace_anime_discovery.propose_anime_sites import *
from ace_anime_discovery import propose_anime_sites as _implementation
if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
