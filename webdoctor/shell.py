"""Shell-safe interpolation for developer-facing reproduction commands.

URLs often contain query strings, ampersands and occasionally quotes. Every
interpolated value in a copy/paste command must be quoted so the reproduced
command means exactly what WebDoctor meant.
"""
from __future__ import annotations

import shlex


def quote(value) -> str:
    """Return a POSIX shell-quoted string for ``value``."""
    return shlex.quote(str(value if value is not None else ""))
