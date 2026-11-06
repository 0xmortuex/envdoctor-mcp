"""A hand-written dotenv parser.

Semantics follow python-dotenv (and dotenvx where they agree):

* blank lines and ``#`` comment lines are skipped
* an optional ``export `` prefix
* ``KEY=value`` and ``KEY = value``. A bare ``KEY`` with no ``=`` is reported as a syntax
  problem by line number only (python-dotenv would yield None): such a line may be a
  secret pasted on its own, so its text is never treated as a name
* single quotes are literal (only ``\\'`` and ``\\\\`` are escapes)
* double quotes process escapes (``\\n``, ``\\t``, ``\\"``, ...) and may span lines
* unquoted values end at an inline `` #`` comment and lose trailing whitespace
* ``${VAR}``, ``${VAR:-default}`` and ``${VAR-default}`` expand against earlier
  variables of the same file (not against ``os.environ``: results must not depend
  on the machine), in every quoting style, like python-dotenv
* a duplicated name keeps every entry; the last one wins

Redaction by construction: error messages produced here contain a line number
and a fixed phrase, never any text from the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_EXPORT = re.compile(r"export[ \t]+")
_COMMENTED = re.compile(r"^\s*#\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_EXPAND = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?-)([^}]*))?\}")
_INLINE_COMMENT = re.compile(r"\s+#.*")

_DQ_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "$": "$",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


@dataclass
class Entry:
    """One assignment. ``value`` is held for analysis only and is hidden from repr."""

    name: str
    line: int
    value: str = field(repr=False)
    quote: str = ""  # "", "'" or '"'
    refs: tuple[str, ...] = ()  # names referenced through ${...}
    unresolved: tuple[str, ...] = ()  # refs with no earlier definition and no default
    trailing_ws: bool = False
    unquoted_space: bool = False
    multiline: bool = False
    has_cr: bool = False  # a carriage return inside the value
    quoted_literal: bool = False  # the value itself still starts and ends with a quote
    optional: bool = False  # a comment directly above says "optional"

    @property
    def empty(self) -> bool:
        return not self.value


@dataclass
class ParsedFile:
    entries: list[Entry] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)
    commented: list[tuple[str, int]] = field(default_factory=list)  # "# NAME=..." lines
    bom: bool = False
    crlf_lines: int = 0
    lf_lines: int = 0

    def names(self) -> set[str]:
        return {e.name for e in self.entries}

    def last(self) -> dict[str, Entry]:
        return {e.name: e for e in self.entries}

    def duplicates(self) -> dict[str, list[int]]:
        seen: dict[str, list[int]] = {}
        for e in self.entries:
            seen.setdefault(e.name, []).append(e.line)
        return {n: lines for n, lines in seen.items() if len(lines) > 1}


def _scan_quoted(buf: str, quote: str) -> int:
    """Index of the closing quote in ``buf`` (which starts after the opening one), or -1."""
    i = 0
    while i < len(buf):
        c = buf[i]
        if c == "\\" and i + 1 < len(buf):
            i += 2
            continue
        if c == quote:
            return i
        i += 1
    return -1


def _unescape_double(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in _DQ_ESCAPES:
            out.append(_DQ_ESCAPES[body[i + 1]])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _unescape_single(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body) and body[i + 1] in ("'", "\\"):
            out.append(body[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _expand(value: str, env: dict[str, str]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    refs: list[str] = []
    unresolved: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name, op, default = m.group(1), m.group(2), m.group(3)
        refs.append(name)
        current = env.get(name)
        if op is not None:  # python-dotenv: both ":-" and "-" mean "default when unset"
            return current if current is not None else (default or "")
        if current is None:
            unresolved.append(name)
            return ""
        return current

    return _EXPAND.sub(sub, value), tuple(refs), tuple(unresolved)


def parse(text: str) -> ParsedFile:
    out = ParsedFile()
    if text.startswith(chr(0xFEFF)):
        out.bom = True
        text = text[1:]
    lines = text.split("\n")
    for raw in lines[:-1]:
        if raw.endswith("\r"):
            out.crlf_lines += 1
        else:
            out.lf_lines += 1
    env: dict[str, str] = {}
    optional_pending = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        i += 1
        line = raw[:-1] if raw.endswith("\r") else raw
        stripped = line.strip()
        if not stripped:
            optional_pending = False
            continue
        if stripped.startswith("#"):
            m = _COMMENTED.match(line)
            if m:
                out.commented.append((m.group(1), lineno))
            elif "optional" in stripped.lower():
                optional_pending = True
            continue
        s = line.lstrip()
        em = _EXPORT.match(s)
        if em:
            s = s[em.end() :]
        km = _KEY.match(s)
        if not km:
            out.errors.append((lineno, "not a KEY=value assignment"))
            optional_pending = False
            continue
        name = km.group(0)
        rest = s[km.end() :]
        after = rest.lstrip(" \t")
        optional = optional_pending
        optional_pending = False
        if not after.startswith("="):
            if after == "" or after.startswith("#"):
                # A line holding one bare word may be a secret pasted on its own, so the word
                # is never kept as a name: only the line number is reported.
                out.errors.append((lineno, "no '=' after the name (a bare word is not an assignment)"))
            else:
                out.errors.append((lineno, "unexpected text after the variable name"))
            continue
        v = after[1:].lstrip(" \t")
        if v[:1] in ("'", '"'):
            q = v[0]
            buf = v[1:]
            end = _scan_quoted(buf, q)
            start_line = lineno
            if raw.endswith("\r"):
                buf += "\r"
            while end < 0 and i < len(lines):
                buf += "\n" + lines[i]
                i += 1
                end = _scan_quoted(buf, q)
            if end < 0:
                out.errors.append((start_line, "unterminated quoted value (runs to end of file)"))
                continue
            body = buf[:end]
            tail = buf[end + 1 :].strip()
            if tail and not tail.startswith("#"):
                out.errors.append((start_line, "unexpected text after the closing quote"))
                continue
            multiline = "\n" in body
            has_cr = "\r" in body
            parsed = _unescape_double(body) if q == '"' else _unescape_single(body)
            quoted_literal = len(parsed) >= 2 and parsed[0] == parsed[-1] and parsed[0] in "'\""
            final, refs, unresolved = _expand(parsed, env)
            entry = Entry(
                name,
                start_line,
                final,
                quote=q,
                refs=refs,
                unresolved=unresolved,
                multiline=multiline,
                has_cr=has_cr,
                quoted_literal=quoted_literal,
                optional=optional,
            )
        else:
            has_comment = _INLINE_COMMENT.search(v) is not None
            body = _INLINE_COMMENT.sub("", v)
            trailing = (not has_comment) and body != body.rstrip()
            body = body.rstrip()
            final, refs, unresolved = _expand(body, env)
            entry = Entry(
                name,
                lineno,
                final,
                refs=refs,
                unresolved=unresolved,
                trailing_ws=trailing,
                unquoted_space=(" " in body or "\t" in body),
                has_cr="\r" in body,
                optional=optional,
            )
        out.entries.append(entry)
        env[name] = entry.value
    return out
