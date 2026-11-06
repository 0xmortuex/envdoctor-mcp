# envdoctor-mcp

An MCP server that diagnoses environment-variable problems in a project, **read-only, and without ever showing a value.**
Your agent gets variable *names*, line numbers, lengths and type guesses, which is everything it needs to fix a broken
`.env`, and nothing it could leak.

**How it differs from other .env MCP servers.** Existing ones, such as
[env-check-mcp](https://github.com/TheoryofShadows/env-check-mcp), compare `.env` against `.env.example` and redact the
report. envdoctor-mcp also reads your *source code* (JavaScript/TypeScript, Python, Ruby, Rust, Go, Java/Kotlin/Scala, C#, PHP, Elixir, Prisma, compose files) to find variables that are used but
undocumented, or documented but dead. It validates shapes (URL, port, bool, DSN, JWT, path-exists, placeholders,
whitespace, CRLF, BOM, quoting mistakes). It warns when a secret would ship to the browser (`VITE_`, `NEXT_PUBLIC_`, ...).
It checks git ignore state and history by file name. It proposes a safe `.env.example`. It handles monorepos with several
env roots. It has zero dependencies beyond `mcp`: the dotenv parser is hand-written and cross-checked against
python-dotenv in the tests.

## Install

Needs [uv](https://docs.astral.sh/uv/). Replace the path with the project you want inspected; the server can read
nothing outside it.

**Claude Code**

```bash
claude mcp add envdoctor -- uvx envdoctor-mcp --dir .
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "envdoctor": {
      "command": "uvx",
      "args": ["envdoctor-mcp", "--dir", "C:\\Users\\you\\projects\\myapp"]
    }
  }
}
```

## Tools

| Tool | What it answers |
| --- | --- |
| `env_overview` | Which env files exist (`.env`, `.env.*`, templates, compose `environment:`/`env_file:`, CI `env:`), grouped per env root (monorepos have several). Variable counts, duplicates, syntax problems, BOM/CRLF. |
| `compare_env` | `.env` vs `.env.example`: missing, extra, empty-but-required (a `# optional` comment exempts a line), duplicated, differing types. Or any two files via `file=` / `against=`. |
| `find_usage` | Scans source for env reads and cross-checks the templates: used-but-undocumented with `file:line`, documented-but-unused, and secrets exposed to the client. |
| `validate_env` | Placeholders, empty values, trailing whitespace, unquoted spaces, CRLF, BOM, literal quotes, undefined `${REF}`, duplicates, bad URL/port/bool/int/email/JWT/path, localhost in production files, real-looking secrets in templates. |
| `check_gitignore` | For each real env file: ignored? tracked? in any commit on any branch? Plus env files committed once and deleted (still in history). Names and hashes only. |
| `generate_example` | A `.env.example` proposal as text: names, type hints, "secret" notes, where the code reads each one. Every line is `NAME=`. Never writes a file. |

All tools take `path` (a folder inside the sandbox, default the whole sandbox).

Source scanning understands `process.env.X`, `process.env["X"]`, `const { X } = process.env`, `import.meta.env.X`,
`Deno.env.get`, `os.environ[...]`, `os.environ.get`, `os.getenv`, `ENV['X']`, `ENV.fetch`, `std::env::var`, `env!`,
`os.Getenv`, `os.LookupEnv`, `System.getenv`, `Environment.GetEnvironmentVariable`, `getenv`, `$_ENV`, Laravel `env()`,
`System.get_env`, Prisma `env()`, and `${X}` in compose files.

## Try asking

- "Run env_overview on this repo and tell me which env files are missing a template."
- "Why does my app crash on startup? Compare my .env with .env.example."
- "Which environment variables does the code read that nobody documented?"
- "Validate my .env files. Anything that looks unfinished or malformed?"
- "Is my .env safe from git? Did I ever commit one?"
- "Write me a .env.example for the api package."
- "Could any of my secrets end up in the browser bundle?"

## Security

- **Values are never output.** Every report is built from names, line numbers, counts, lengths and type words. The
  parser keeps values in memory only so shapes can be checked; no formatting code touches them. Syntax-error messages are
  fixed phrases with a line number. A line holding a single bare word is reported by line number only (it could be a
  secret pasted on its own).
- **A trip-wire backs that up.** If any output ever contained the value of a secret-looking variable, the tool refuses
  to return it and says so.
- **Read-only.** Nothing is written, deleted or executed. The only subprocess is `git` (`rev-parse`, `check-ignore`,
  `ls-files`, `log` by file name).
- **Sandboxed.** `--dir` is the only place the server reads. Paths that resolve outside it (including through symlinks)
  are refused; symlinks pointing out of the sandbox are skipped and reported.
- The tests plant fake secrets in hostile files (garbage lines, unterminated quotes, template secrets, commented-out
  values) and assert that none appears in any tool output, any error, or the stdio transcript.

## Limits

- Source scanning is a text scan: matches inside comments count and dynamic names (`process.env[name]`) are invisible.
  "Documented but unused" can be wrong for variables read by frameworks, shell scripts or Dockerfiles.
- The compose and CI readers are line-based, not full YAML.
- Expansion follows python-dotenv: `${VAR}`, `${VAR:-d}` and `${VAR-d}` are resolved against earlier variables of the same
  file, never against your real environment, and `:-` and `-` both mean "when unset".
- `*_PATH` / `*_DIR` / `*_FILE` existence is only checked in `.env`, `.env.local`, development and test files.

## Development

```bash
uv sync --extra test
uv run pytest
uv run ruff check src tests && uv run mypy --strict src
```

A from-scratch tutorial that builds a minimal version of this server lives in
[envdoctor-mcp-the-hard-way](https://github.com/0xmortuex/envdoctor-mcp-the-hard-way).

MIT licensed.
