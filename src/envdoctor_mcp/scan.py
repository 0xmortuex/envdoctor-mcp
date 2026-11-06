"""Finding things: env files, compose/CI files, and environment reads in source code."""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import dotenv

MAX_ENV_BYTES = 2_000_000
MAX_SOURCE_BYTES = 1_000_000
MAX_FILES = 60_000
MAX_DEPTH = 10

PRUNE_DIRS = {
    ".git", "node_modules", "__pycache__", "dist", "build", ".next", ".nuxt", ".output",
    ".svelte-kit", ".turbo", "target", "vendor", "coverage", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".idea", ".cache", "bower_components", "site-packages",
    "Pods", ".gradle", "bin", "obj",
}  # fmt: skip

TEMPLATE_WORDS = {"example", "sample", "template", "dist", "tpl", "defaults"}
TEMPLATE_ORDER = ("example", "sample", "template", "dist", "defaults", "tpl")
NOT_ENV_EXT = {"ts", "js", "mjs", "cjs", "md", "json", "py", "yml", "yaml", "txt", "sh", "html"}
PROD_NAMES = {"production", "prod"}
PATH_CHECK_ENVS = {"", "local", "development", "dev", "test"}

# Variables every process has, or that tooling sets: not worth reporting as undocumented.
AMBIENT = {
    "PATH", "HOME", "USER", "USERNAME", "PWD", "SHELL", "TERM", "LANG", "LC_ALL", "TMP",
    "TMPDIR", "TEMP", "HOSTNAME", "NODE_ENV", "CI", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMFILES", "SYSTEMROOT", "COMSPEC", "OS", "EDITOR", "PAGER", "TZ", "DISPLAY",
    "LOGNAME", "OLDPWD", "SHLVL", "NO_COLOR", "FORCE_COLOR", "COLORTERM", "PYTHONPATH",
    "VIRTUAL_ENV", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "RUST_LOG",
    "RUST_BACKTRACE", "GOPATH", "JAVA_HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
}  # fmt: skip
AMBIENT_PREFIXES = ("GITHUB_", "RUNNER_", "npm_", "NPM_CONFIG_", "LC_", "VERCEL_", "CI_")


def is_ambient(name: str) -> bool:
    return name in AMBIENT or name.startswith(AMBIENT_PREFIXES)


@dataclass(frozen=True)
class Kind:
    kind: str  # "real" or "template"
    env: str  # "", "local", "production", ...


def classify_env_file(filename: str) -> Kind | None:
    """Is this file name an env file, and what sort? Name only, never content."""
    low = filename.lower()
    parts = low.split(".")
    if low == ".env":
        return Kind("real", "")
    if low.startswith(".env."):
        suffix = parts[2:]
        if not suffix or suffix[-1] in NOT_ENV_EXT:
            return None
        if any(p in TEMPLATE_WORDS for p in suffix):
            names = [p for p in suffix if p not in TEMPLATE_WORDS and p != "local"]
            return Kind("template", names[0] if names else "")
        names = [p for p in suffix if p != "local"]
        return Kind("real", names[0] if names else "local")
    if low.endswith(".env") and len(low) > 4 and parts[-2]:
        stem = parts[-2]
        return Kind("real", stem)
    return None


_COMPOSE = re.compile(r"^(docker-)?compose([.\-].+)?\.ya?ml$", re.IGNORECASE)

SOURCE_EXT: dict[str, str] = {
    **dict.fromkeys(
        [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte", ".astro"],
        "js",
    ),
    ".py": "py", ".rb": "rb", ".rake": "rb", ".rs": "rs", ".go": "go", ".java": "jvm",
    ".kt": "jvm", ".kts": "jvm", ".scala": "jvm", ".groovy": "jvm", ".cs": "cs",
    ".php": "php", ".ex": "ex", ".exs": "ex", ".prisma": "prisma",
}  # fmt: skip

_ID = r"([A-Za-z_][A-Za-z0-9_]*)"
_QID = r"""\s*['"`]""" + _ID + r"""['"`]"""
PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "js": [
        re.compile(r"process\.env\." + _ID),
        re.compile(r"process\.env\[" + _QID + r"\s*\]"),
        re.compile(r"import\.meta\.env\." + _ID),
        re.compile(r"import\.meta\.env\[" + _QID + r"\s*\]"),
        re.compile(r"Deno\.env\.get\(" + _QID),
        re.compile(r"Bun\.env\." + _ID),
    ],
    "py": [
        re.compile(r"os\.environ\[" + _QID + r"\s*\]"),
        re.compile(r"os\.environ\.get\(" + _QID),
        re.compile(r"os\.getenv\(" + _QID),
        re.compile(r"(?<![\w.])environ(?:\.get\(|\[)" + _QID),
        re.compile(r"(?<![\w.])getenv\(" + _QID),
        re.compile(r"\benv(?:\.\w+)?\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"),
    ],
    "rb": [
        re.compile(r"\bENV\[" + _QID + r"\s*\]"),
        re.compile(r"\bENV\.fetch\(" + _QID),
    ],
    "rs": [
        re.compile(r"\benv::var(?:_os)?\(\s*\"" + _ID + r"\""),
        re.compile(r"\b(?:option_)?env!\(\s*\"" + _ID + r"\""),
    ],
    "go": [re.compile(r"\bos\.(?:Getenv|LookupEnv)\(\s*\"" + _ID + r"\"")],
    "jvm": [re.compile(r"System\.getenv\(\s*\"" + _ID + r"\"")],
    "cs": [re.compile(r"Environment\.GetEnvironmentVariable\(\s*\"" + _ID + r"\"")],
    "php": [
        re.compile(r"\bgetenv\(" + _QID),
        re.compile(r"\$_ENV\[" + _QID + r"\s*\]"),
        re.compile(r"(?<![\w>$:])env\(" + _QID),
    ],
    "ex": [re.compile(r"System\.(?:get_env|fetch_env!?)\(\s*\"" + _ID + r"\"")],
    "prisma": [re.compile(r"\benv\(\s*\"" + _ID + r"\"\s*\)")],
}
VITE_BUILTINS = {"MODE", "BASE_URL", "PROD", "DEV", "SSR"}  # always present in import.meta.env
_DESTRUCTURE = re.compile(r"\{([^{}]*)\}\s*=\s*(?:process\.env|import\.meta\.env)\b")
_COMPOSE_REF = re.compile(r"(?<!\$)\$\{?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Usage:
    name: str
    file: str  # posix path relative to the sandbox root
    line: int


def read_text(path: Path, limit: int) -> tuple[str, bool]:
    """(text, truncated). utf-8 with replacement; BOM preserved so the parser can see it."""
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    truncated = len(data) > limit
    return data[:limit].decode("utf-8", errors="replace"), truncated


def scan_source(text: str, lang: str, rel: str) -> list[Usage]:
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())

    def line_of(pos: int) -> int:
        return bisect.bisect_right(starts, pos)

    found: list[Usage] = []
    for pat in PATTERNS.get(lang, ()):
        for m in pat.finditer(text):
            if pat.pattern.startswith("import") and m.group(1) in VITE_BUILTINS:
                continue
            found.append(Usage(m.group(1), rel, line_of(m.start())))
    if lang == "js":
        for m in _DESTRUCTURE.finditer(text):
            for part in m.group(1).split(","):
                part = part.strip()
                if part.startswith("..."):
                    continue
                nm = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", part)
                if nm and not ("import.meta" in m.group(0) and nm.group(1) in VITE_BUILTINS):
                    found.append(Usage(nm.group(1), rel, line_of(m.start())))
    return found


def scan_compose_refs(text: str, rel: str) -> list[Usage]:
    found: list[Usage] = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in _COMPOSE_REF.finditer(line):
            found.append(Usage(m.group(1), rel, n))
    return found


@dataclass(frozen=True)
class YamlVar:
    name: str
    line: int


_YAML_KEY_LINE = r"^(\s*)(?:-\s+)?{keys}:\s*(?:#.*)?$"
_ITEM_NAME = re.compile(r"^(?:-\s+)?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*(?:[:=]|$)")


def yaml_env_vars(text: str, keys: tuple[str, ...] = ("environment", "env")) -> list[YamlVar]:
    """Names (and lines) in ``environment:`` / ``env:`` blocks. A line-based reader, not YAML."""
    key_re = re.compile(_YAML_KEY_LINE.format(keys="(?:" + "|".join(keys) + ")"))
    lines = text.split("\n")
    out: list[YamlVar] = []
    i = 0
    while i < len(lines):
        m = key_re.match(lines[i].rstrip("\r"))
        i += 1
        if not m:
            continue
        base = len(m.group(1))
        block_indent: int | None = None
        while i < len(lines):
            raw = lines[i].rstrip("\r")
            body = raw.strip()
            if not body or body.startswith("#"):
                i += 1
                continue
            indent = len(raw) - len(raw.lstrip())
            if indent < base or (indent == base and not body.startswith("- ")):
                break
            if block_indent is None:
                block_indent = indent
            if indent == block_indent:
                nm = _ITEM_NAME.match(body)
                if nm:
                    out.append(YamlVar(nm.group(1), i + 1))
            i += 1
    return out


def yaml_env_files(text: str) -> list[str]:
    """Paths listed under ``env_file:`` (scalar or list form)."""
    lines = text.split("\n")
    out: list[str] = []
    for i, raw in enumerate(lines):
        m = re.match(r"^(\s*)env_file:\s*(.*?)\s*(?:#.*)?$", raw.rstrip("\r"))
        if not m:
            continue
        inline = m.group(2).strip().strip("'\"")
        if inline:
            out.append(inline)
            continue
        base = len(m.group(1))
        for nxt in lines[i + 1 :]:
            nxt = nxt.rstrip("\r")
            body = nxt.strip()
            if not body or body.startswith("#"):
                continue
            indent = len(nxt) - len(nxt.lstrip())
            if (indent <= base and not body.startswith("- ")) or not body.startswith("- "):
                break
            item = body[2:].split("#")[0].strip().strip("'\"")
            if item and not item.startswith(("path:", "required:")):
                out.append(item)
    return out


@dataclass
class EnvFile:
    rel: str  # posix path from the sandbox root
    kind: Kind
    parsed: dotenv.ParsedFile
    truncated: bool = False

    @property
    def dir_rel(self) -> str:
        return os.path.dirname(self.rel) or "."

    @property
    def is_prod(self) -> bool:
        return self.kind.env in PROD_NAMES


@dataclass
class EnvRoot:
    dir_rel: str
    files: list[EnvFile] = field(default_factory=list)
    compose: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)

    def real(self) -> list[EnvFile]:
        return [f for f in self.files if f.kind.kind == "real"]

    def templates(self) -> list[EnvFile]:
        return [f for f in self.files if f.kind.kind == "template"]

    def find(self, filename: str) -> EnvFile | None:
        for f in self.files:
            if os.path.basename(f.rel) == filename:
                return f
        return None

    def main_template(self, env: str = "") -> EnvFile | None:
        """Best template: env-specific one if present, else the generic one by TEMPLATE_ORDER."""
        cands = [t for t in self.templates() if t.kind.env == env] or [
            t for t in self.templates() if t.kind.env == ""
        ]
        if not cands:
            return None

        def rank(t: EnvFile) -> int:
            parts = os.path.basename(t.rel).lower().split(".")
            for i, w in enumerate(TEMPLATE_ORDER):
                if w in parts:
                    return i
            return len(TEMPLATE_ORDER)

        return sorted(cands, key=lambda t: (rank(t), t.rel))[0]


@dataclass
class Project:
    root: Path  # sandbox root (resolved)
    scan: Path  # directory being scanned (resolved, inside root)
    roots: list[EnvRoot]
    sources: list[tuple[str, str]]  # (rel path, lang)
    compose_files: list[str]
    workflow_files: list[str]
    notes: list[str]
    all_files: int = 0

    def rel(self, p: Path) -> str:
        r = p.relative_to(self.root).as_posix()
        return r if r else "."

    def read(self, rel: str, limit: int = MAX_SOURCE_BYTES) -> str:
        return read_text(self.root / rel, limit)[0]

    def root_for(self, rel_file: str) -> EnvRoot | None:
        """The nearest env root at or above a file."""
        best: EnvRoot | None = None
        best_depth = -1
        d = os.path.dirname(rel_file) or "."
        for r in self.roots:
            if r.dir_rel == ".":
                depth = 0
            elif d == r.dir_rel or d.startswith(r.dir_rel + "/"):
                depth = r.dir_rel.count("/") + 1
            else:
                continue
            if depth > best_depth:
                best, best_depth = r, depth
        return best

    def all_env_files(self) -> list[EnvFile]:
        return [f for r in self.roots for f in r.files]


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def load_project(root: Path, scan: Path) -> Project:
    """Walk ``scan`` (inside ``root``), parse every env file, and index the rest."""
    notes: list[str] = []
    by_dir: dict[str, EnvRoot] = {}
    sources: list[tuple[str, str]] = []
    compose: list[str] = []
    workflows: list[str] = []
    count = 0

    def rel_of(p: Path) -> str:
        r = p.relative_to(root).as_posix()
        return r if r else "."

    def root_obj(d: str) -> EnvRoot:
        return by_dir.setdefault(d, EnvRoot(d))

    scan_depth = len(scan.relative_to(root).parts)
    for dirpath, dirnames, filenames in os.walk(scan, followlinks=False):
        dp = Path(dirpath)
        depth = len(dp.relative_to(root).parts) - scan_depth
        keep = []
        for d in sorted(dirnames):
            if d in PRUNE_DIRS or (dp / d / "pyvenv.cfg").exists():
                continue
            if (dp / d).is_symlink():
                notes.append(f"skipped symlinked folder {rel_of(dp / d)}")
                continue
            keep.append(d)
        dirnames[:] = keep if depth < MAX_DEPTH else []
        if depth >= MAX_DEPTH and keep:
            notes.append(f"stopped descending below depth {MAX_DEPTH} in {rel_of(dp)}")
        for fn in sorted(filenames):
            count += 1
            if count > MAX_FILES:
                notes.append(f"stopped after {MAX_FILES} files; results are partial")
                dirnames[:] = []
                break
            fp = dp / fn
            rel = rel_of(fp)
            kind = classify_env_file(fn)
            if kind is not None:
                if not _inside(root, fp):
                    notes.append(f"skipped {rel}: symlink points outside the sandbox")
                    continue
                text, truncated = read_text(fp, MAX_ENV_BYTES)
                if truncated:
                    notes.append(f"{rel} is larger than {MAX_ENV_BYTES} bytes; only the start was read")
                root_obj(rel_of(dp)).files.append(EnvFile(rel, kind, dotenv.parse(text), truncated))
                continue
            if _COMPOSE.match(fn):
                compose.append(rel)
                continue
            parent = dp.name
            if (parent == "workflows" and dp.parent.name == ".github" and fn.endswith((".yml", ".yaml"))) or (
                fn == ".gitlab-ci.yml"
            ):
                workflows.append(rel)
                continue
            lang = SOURCE_EXT.get(os.path.splitext(fn)[1].lower())
            if lang:
                try:
                    if fp.stat().st_size > MAX_SOURCE_BYTES:
                        notes.append(f"skipped {rel}: larger than {MAX_SOURCE_BYTES} bytes")
                        continue
                except OSError:
                    continue
                if _inside(root, fp):
                    sources.append((rel, lang))
    proj = Project(root, scan, [by_dir[k] for k in sorted(by_dir)], sources, compose, workflows, notes, count)
    for rel in compose:
        owner = proj.root_for(rel)
        if owner:
            owner.compose.append(rel)
    for rel in workflows:
        owner = proj.root_for(rel)
        if owner:
            owner.workflows.append(rel)
    return proj
