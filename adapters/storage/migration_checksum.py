"""Conservative, byte-preserving validation of applied migration history."""

from __future__ import annotations

import hashlib


def normalization_is_safe(raw: bytes) -> bool:
    """Prove LF/CRLF transport cannot change a SQLite quoted construct.

    This is a lexical guard, not SQL parsing. Unknown/unterminated quoting or
    comments fail closed. Exact checksum validation does not need this guard.
    """
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw.replace(b"\r\n", b""):
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    i = 0
    while i < len(text):
        if text.startswith("--", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                return False
            i = end + 2
            continue
        if text[i] in "'\"`[":
            closing = "]" if text[i] == "[" else text[i]
            i += 1
            while i < len(text):
                if text[i] in "\r\n":
                    return False
                if text[i] == closing:
                    if closing != "]" and i + 1 < len(text) and text[i + 1] == closing:
                        i += 2
                        continue
                    break
                i += 1
            if i == len(text):
                return False
        i += 1
    return True


def classify_migration_checksum(raw: bytes, recorded_checksum: str) -> str:
    """Accept only exact bytes or proven-safe LF/CRLF variants; never write."""
    digest = lambda value: hashlib.sha256(value).hexdigest()
    if digest(raw) == recorded_checksum:
        return "EXACT_RAW"
    if not normalization_is_safe(raw):
        return "MISMATCH"
    lf = raw.replace(b"\r\n", b"\n")
    if recorded_checksum in {digest(lf), digest(lf.replace(b"\n", b"\r\n"))}:
        return "LINE_ENDING_EQUIVALENT"
    return "MISMATCH"
