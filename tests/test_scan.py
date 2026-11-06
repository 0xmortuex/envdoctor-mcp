"""Scanning source for environment reads, across languages, plus file classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor_mcp import scan

from .conftest import write


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (".env", ("real", "")),
        (".env.local", ("real", "local")),
        (".env.production", ("real", "production")),
        (".env.production.local", ("real", "production")),
        (".env.development.local", ("real", "development")),
        (".env.example", ("template", "")),
        (".env.sample", ("template", "")),
        (".env.template", ("template", "")),
        (".env.production.example", ("template", "production")),
        ("prod.env", ("real", "prod")),
        (".envrc", None),
        (".env.d.ts", None),
        ("env.ts", None),
        ("README.md", None),
        (".environment", None),
    ],
)
def test_classify_env_file(filename: str, expected: tuple[str, str] | None) -> None:
    got = scan.classify_env_file(filename)
    assert (None if got is None else (got.kind, got.env)) == expected


SOURCES: dict[str, tuple[str, str, list[tuple[str, int]]]] = {
    "js": (
        "a.ts",
        "const a = process.env.A_ONE;\n"
        "const b = process.env['B_TWO'];\n"
        'const c = import.meta.env.VITE_THREE + import.meta.env.MODE;\n'
        "const { D_FOUR, E_FIVE: alias = 'x', ...rest } = process.env;\n"
        'Deno.env.get("F_SIX");\n',
        [("A_ONE", 1), ("B_TWO", 2), ("D_FOUR", 4), ("E_FIVE", 4), ("F_SIX", 5), ("VITE_THREE", 3)],
    ),
    "py": (
        "a.py",
        "import os\n"
        "x = os.environ['PY_ONE']\n"
        "y = os.environ.get('PY_TWO', 'd')\n"
        'z = os.getenv("PY_THREE")\n'
        "w = env('PY_FOUR')\n"
        "v = environ.get('PY_FIVE')\n"
        "not_env('lower_case')\n",
        [("PY_FIVE", 6), ("PY_FOUR", 5), ("PY_ONE", 2), ("PY_THREE", 4), ("PY_TWO", 3)],
    ),
    "rb": ("a.rb", "ENV['RB_ONE']\nENV.fetch('RB_TWO', 'x')\n", [("RB_ONE", 1), ("RB_TWO", 2)]),
    "rs": (
        "a.rs",
        'let a = std::env::var("RS_ONE");\nlet b = env::var_os("RS_TWO");\nlet c = env!("RS_THREE");\n',
        [("RS_ONE", 1), ("RS_THREE", 3), ("RS_TWO", 2)],
    ),
    "go": ("a.go", 'a := os.Getenv("GO_ONE")\nb, ok := os.LookupEnv("GO_TWO")\n', [("GO_ONE", 1), ("GO_TWO", 2)]),
    "java": ("A.java", 'String s = System.getenv("JV_ONE");\n', [("JV_ONE", 1)]),
    "kt": ("A.kt", 'val s = System.getenv("KT_ONE")\n', [("KT_ONE", 1)]),
    "cs": ("A.cs", 'var s = Environment.GetEnvironmentVariable("CS_ONE");\n', [("CS_ONE", 1)]),
    "php": (
        "a.php",
        "<?php\n$a = getenv('PHP_ONE');\n$b = $_ENV['PHP_TWO'];\n$c = env('PHP_THREE');\n$d = $this->env('NOT_THIS');\n",
        [("PHP_ONE", 2), ("PHP_THREE", 4), ("PHP_TWO", 3)],
    ),
    "ex": ("a.ex", 'System.get_env("EX_ONE")\nSystem.fetch_env!("EX_TWO")\n', [("EX_ONE", 1), ("EX_TWO", 2)]),
    "prisma": ("s.prisma", 'datasource db {\n  url = env("PRISMA_URL")\n}\n', [("PRISMA_URL", 2)]),
}


@pytest.mark.parametrize("key", sorted(SOURCES))
def test_language_patterns(key: str) -> None:
    filename, text, expected = SOURCES[key]
    lang = scan.SOURCE_EXT["." + filename.rsplit(".", 1)[1]]
    found = sorted((u.name, u.line) for u in scan.scan_source(text, lang, filename))
    assert found == expected


def test_vite_builtins_are_not_reported() -> None:
    found = scan.scan_source("import.meta.env.MODE; import.meta.env.BASE_URL; process.env.BASE_URL", "js", "a.js")
    assert [u.name for u in found] == ["BASE_URL"]  # the process.env one is a real variable


def test_compose_refs_and_env_blocks() -> None:
    text = (
        "services:\n"
        "  a:\n"
        "    # ${IN_COMMENT}\n"
        "    image: x:${TAG}\n"
        "    environment:\n"
        "      - LISTED=1\n"
        "      - BARE\n"
        "      - REF=${DB_URL:-sqlite://x}\n"
        "    env_file:\n"
        "      - .env\n"
        "      - ./other.env\n"
        "  b:\n"
        "    environment:\n"
        "      MAPPED: v\n"
        "      OTHER: ${Z}\n"
        "    command: echo $$ESCAPED $PLAIN\n"
    )
    refs = sorted({u.name for u in scan.scan_compose_refs(text, "c.yml")})
    assert refs == ["DB_URL", "PLAIN", "TAG", "Z"]
    names = [(v.name, v.line) for v in scan.yaml_env_vars(text, ("environment",))]
    assert names == [("LISTED", 6), ("BARE", 7), ("REF", 8), ("MAPPED", 14), ("OTHER", 15)]
    assert scan.yaml_env_files(text) == [".env", "./other.env"]


def test_ci_env_blocks() -> None:
    text = (
        "env:\n  TOP: '1'\njobs:\n  t:\n    steps:\n      - run: x\n        env:\n          STEP_VAR: ${{ secrets.X }}\n"
    )
    assert [v.name for v in scan.yaml_env_vars(text, ("env",))] == ["TOP", "STEP_VAR"]


def test_walk_prunes_and_finds_roots(tmp_path: Path) -> None:
    write(tmp_path, ".env", "A=1\n")
    write(tmp_path, "node_modules/pkg/.env", "B=1\n")
    write(tmp_path, ".venv/pyvenv.cfg", "home = x\n")
    write(tmp_path, ".venv/lib/.env", "C=1\n")
    write(tmp_path, "svc/a/.env.example", "D=\n")
    write(tmp_path, "svc/a/main.go", 'os.Getenv("D")\n')
    proj = scan.load_project(tmp_path.resolve(), tmp_path.resolve())
    assert [r.dir_rel for r in proj.roots] == [".", "svc/a"]
    assert [f.rel for f in proj.all_env_files()] == [".env", "svc/a/.env.example"]
    assert proj.sources == [("svc/a/main.go", "go")]
    owner = proj.root_for("svc/a/main.go")
    assert owner is not None and owner.dir_rel == "svc/a"
    other = proj.root_for("docs/x.py")
    assert other is not None and other.dir_rel == "."
