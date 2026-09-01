"""
Read a .env file into the process environment.

Everything about running Multiva is meant to work from a clone with no
external services, so the obvious place to put a key is a .env beside the
code - and until now nothing read it, which made a key put there look
configured while behaving exactly like no key at all.

This is deliberately not python-dotenv. The whole requirement is "parse
KEY=value lines", and the dependency list is short on purpose.
"""

from __future__ import annotations

import os


def load(path: str, override: bool = False) -> list[str]:
    """
    Set each KEY=value from `path`, returning the names that were applied.

    A real environment variable wins over the file unless `override` is set:
    something exported for one run should not be silently replaced by a stale
    line in a file the user forgot about.
    """
    if not os.path.isfile(path):
        return []

    applied = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # `export FOO=bar` is a normal thing to paste into a .env.
        if key.startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        value = value.strip()
        # Quotes are a shell habit, not part of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied
