# Changelog

## 0.1.0 - 2026-11-06

First release.

- Six read-only tools: `env_overview`, `compare_env`, `find_usage`, `validate_env`,
  `check_gitignore`, `generate_example`.
- Hand-written dotenv parser (quotes, escapes, `export`, inline comments, multiline
  values, `${VAR}` expansion, duplicates) cross-checked against python-dotenv in the tests.
- Values are never part of any output: results are built from names, lengths, line
  numbers and type guesses only, with a trip-wire guard on secret-looking names.
- `--dir` sandbox; monorepos (several env roots); Vite / Next.js / CRA / Nuxt / Expo /
  Astro / SvelteKit client-exposure warnings.
