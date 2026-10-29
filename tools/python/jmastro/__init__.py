"""Readers for the CTAS and WorldsIndex public releases (jackmcguireastro.github.io).

Kept in the website repository (tools/python); not published to PyPI.
"""
from .client import CTAS, SITE, WorldsIndex, separation_deg

__all__ = ["CTAS", "WorldsIndex", "SITE", "separation_deg"]
__version__ = "1.0.0"
