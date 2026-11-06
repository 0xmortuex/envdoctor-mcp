"""The reports behind the tools. Every report is built from names, line numbers, counts
and type words. No function here ever formats an ``Entry.value``."""

from __future__ import annotations

import os
from collections import defaultdict

from . import shapes
from .errors import EnvDoctorError
from .scan import (
    EnvFile,
    EnvRoot,
    Project,
    Usage,
    is_ambient,
    scan_compose_refs,
    scan_source,
    yaml_env_files,
    yaml_env_vars,
)

LIST_LIMIT = 80

_FORMAT_HINTS = {
    "url": "a URL such as https://host:port/path",
    "dsn": "a connection string such as scheme://user:password@host:port/dbname",
    "port": "an integer from 1 to 65535",
    "bool": "true or false",
    "int": "an integer",
    "float": "a decimal number",
    "email": "an email address",
    "jwt": "a JWT (three dot-separated base64url parts)",
    "uuid": "a UUID",
    "json": "a JSON document",
    "path": "a filesystem path",
}


def names_list(names: list[str] | set[str], limit: int = LIST_LIMIT) -> str:
    ordered = sorted(names)
    shown = ", ".join(ordered[:limit])
    if len(ordered) > limit:
        shown += f" (+{len(ordered) - limit} more)"
    return shown


def collect_usage(p: Project) -> list[Usage]:
    found: list[Usage] = []
    for rel, lang in p.sources:
        found.extend(scan_source(p.read(rel), lang, rel))
    for rel in p.compose_files:
        found.extend(scan_compose_refs(p.read(rel), rel))
    seen: set[tuple[str, str, int]] = set()
    unique: list[Usage] = []
    for u in sorted(found, key=lambda u: (u.file, u.line, u.name)):
        key = (u.name, u.file, u.line)
        if key not in seen:
            seen.add(key)
            unique.append(u)
    return unique


def usage_by_root(p: Project, usage: list[Usage]) -> dict[str, list[Usage]]:
    out: dict[str, list[Usage]] = defaultdict(list)
    for u in usage:
        owner = p.root_for(u.file)
        out[owner.dir_rel if owner else "."].append(u)
    return out


def _label(f: EnvFile) -> str:
    return f"{f.kind.kind}" + (f" ({f.kind.env})" if f.kind.env else "")


def _compose_defs(p: Project, root: EnvRoot) -> dict[str, list[str]]:
    defs: dict[str, list[str]] = defaultdict(list)
    for rel in root.compose:
        for v in yaml_env_vars(p.read(rel), ("environment",)):
            defs[v.name].append(rel)
    for rel in root.workflows:
        for v in yaml_env_vars(p.read(rel), ("env",)):
            defs[v.name].append(rel)
    return defs


def overview(p: Project) -> str:
    usage = collect_usage(p)
    out = [f"Scanned {p.rel(p.scan)}: {p.all_files} files seen, {len(p.roots)} env root(s)."]
    if not p.roots:
        out.append("No .env files were found (looked for .env, .env.*, *.env and their templates).")
    for r in p.roots:
        out.append(f"\n== env root: {r.dir_rel} ==")
        for f in r.files:
            pf = f.parsed
            bits = [f"{len(pf.names())} variable(s)"]
            if pf.duplicates():
                bits.append(f"{len(pf.duplicates())} duplicated")
            if pf.errors:
                bits.append(f"{len(pf.errors)} syntax problem(s)")
            if pf.bom:
                bits.append("BOM")
            if pf.crlf_lines:
                bits.append("CRLF line endings")
            out.append(f"  {os.path.basename(f.rel)}  [{_label(f)}]  " + ", ".join(bits))
        if r.real() and not r.templates():
            out.append("  ! real env file(s) but no .env.example/.sample/.template to document them")
        if r.templates() and not any(f.kind.env == "" and f.kind.kind == "real" for f in r.files):
            out.append("  note: a template exists but there is no plain .env here")
        for rel in r.compose:
            text = p.read(rel)
            envs = sorted({v.name for v in yaml_env_vars(text, ("environment",))})
            refs = sorted({u.name for u in scan_compose_refs(text, rel)})
            files = yaml_env_files(text)
            out.append(
                f"  compose {rel}: environment names [{names_list(envs)}]; "
                f"${{VAR}} references [{names_list(refs)}]; env_file [{', '.join(files)}]"
            )
        for rel in r.workflows:
            envs = sorted({v.name for v in yaml_env_vars(p.read(rel), ("env",))})
            out.append(f"  CI {rel}: env names [{names_list(envs)}]")
    orphan_compose = [c for c in p.compose_files if p.root_for(c) is None]
    orphan_ci = [c for c in p.workflow_files if p.root_for(c) is None]
    if orphan_compose or orphan_ci:
        out.append("\n== outside any env root ==")
        for rel in orphan_compose:
            envs = sorted({v.name for v in yaml_env_vars(p.read(rel), ("environment",))})
            out.append(f"  compose {rel}: environment names [{names_list(envs)}]")
        for rel in orphan_ci:
            envs = sorted({v.name for v in yaml_env_vars(p.read(rel), ("env",))})
            out.append(f"  CI {rel}: env names [{names_list(envs)}]")
    names = {u.name for u in usage} | {n for f in p.all_env_files() for n in f.parsed.names()}
    fw: dict[str, int] = defaultdict(int)
    for n in names:
        hit = shapes.client_exposure(n)
        if hit:
            fw[f"{hit[0]} ({hit[1]}*)"] += 1
    if fw:
        out.append("\nFramework client prefixes in use: " + ", ".join(f"{k}: {v} name(s)" for k, v in sorted(fw.items())))
    out.extend(f"\nnote: {n}" for n in p.notes)
    return "\n".join(out)


# ---------------------------------------------------------------- compare


def _compare_pair(r: EnvFile, t: EnvFile) -> list[str]:
    out = [f"== {r.rel} vs {t.rel} =="]
    rl, tl = r.parsed.last(), t.parsed.last()
    t_documented = set(tl) | {n for n, _ in t.parsed.commented}
    problems = 0

    missing = sorted(n for n in tl if n not in rl)
    if missing:
        problems += len(missing)
        out.append(f"Missing from {r.rel} ({len(missing)}): " + ", ".join(
            f"{n} (template line {tl[n].line}{', optional' if tl[n].optional else ''})" for n in missing))
    extra = sorted(n for n in rl if n not in t_documented)
    if extra:
        problems += len(extra)
        out.append(f"Extra in {r.rel}, not in the template ({len(extra)}): " + ", ".join(
            f"{n} (line {rl[n].line})" for n in extra))
    empty = sorted(n for n in rl if n in tl and rl[n].empty and not tl[n].optional)
    if empty:
        problems += len(empty)
        out.append(f"Empty in {r.rel} but required by the template ({len(empty)}): " + ", ".join(
            f"{n} (line {rl[n].line})" for n in empty))
    for label, f in ((r.rel, r), (t.rel, t)):
        for n, lines in sorted(f.parsed.duplicates().items()):
            problems += 1
            out.append(f"Duplicated in {label}: {n} on lines {', '.join(map(str, lines))} (the last one wins)")
    for n in sorted(set(rl) & set(tl)):
        rv, tv = rl[n].value, tl[n].value
        if not rv or not tv or shapes.is_placeholder(tv):
            continue
        rt, tt = shapes.guess_type(n, rv), shapes.guess_type(n, tv)
        if tt != "string" and shapes.type_family(rt) != shapes.type_family(tt):
            problems += 1
            out.append(f"Type differs: {n}: template looks like {tt}, {r.rel} looks like {rt}")
    out.append("Result: in sync." if problems == 0 else f"Result: {problems} difference(s).")
    return out


def compare(p: Project, file: str | None, against: str | None) -> str:
    by_rel = {f.rel: f for f in p.all_env_files()}
    if file:
        if file not in by_rel:
            raise EnvDoctorError(f"{file} is not an env file under the scanned folder (known: {names_list(list(by_rel))})")
        r = by_rel[file]
        if against:
            if against not in by_rel:
                raise EnvDoctorError(f"{against} is not an env file under the scanned folder")
            t = by_rel[against]
        else:
            root = next(x for x in p.roots if r in x.files)
            maybe = root.main_template(r.kind.env)
            if maybe is None:
                raise EnvDoctorError(f"no template (.env.example/.sample/.template) next to {file}; pass against=")
            t = maybe
        return "\n".join(_compare_pair(r, t))
    if against:
        raise EnvDoctorError("against= needs file= as well")
    out: list[str] = []
    for root in p.roots:
        renv = root.find(".env")
        tmpl = root.main_template("")
        if renv and tmpl:
            out.extend(_compare_pair(renv, tmpl))
            out.append("")
        elif renv and not tmpl:
            out.append(f"{root.dir_rel}: .env exists but there is no template to compare it against.\n")
        elif tmpl and not renv:
            out.append(f"{root.dir_rel}: {os.path.basename(tmpl.rel)} exists but there is no .env to compare "
                       f"({len(tmpl.parsed.names())} variable(s) to set up).\n")
    if not out:
        out.append("Nothing to compare: no .env / template pair found. Pass file= and against= to compare two files.")
    return "\n".join(out).rstrip()


# ------------------------------------------------------------------ usage


def usage_report(p: Project) -> str:
    usage = collect_usage(p)
    by_root = usage_by_root(p, usage)
    roots: list[tuple[str, EnvRoot | None]] = [(r.dir_rel, r) for r in p.roots]
    if any(k not in {r.dir_rel for r in p.roots} for k in by_root):
        roots.append((".", None))
    if not roots:
        return "No env files and no environment reads found."
    out = [f"Scanned {len(p.sources)} source file(s), {len(p.compose_files)} compose file(s)."]
    for dir_rel, root in roots:
        used: dict[str, list[Usage]] = defaultdict(list)
        for u in by_root.get(dir_rel, []):
            used[u.name].append(u)
        templates = root.templates() if root else []
        documented = {n for t in templates for n in t.parsed.names()} | {
            n for t in templates for n, _ in t.parsed.commented
        }
        real_names = {n for f in (root.real() if root else []) for n in f.parsed.names()}
        defs = _compose_defs(p, root) if root else {}
        out.append(f"\n== env root: {dir_rel} ==")
        if not templates:
            out.append("No .env.example/.sample/.template here, so every variable the code reads counts as undocumented.")
        undocumented = sorted(n for n in used if n not in documented and not is_ambient(n))
        if undocumented:
            out.append(f"Used in code but not documented in the template ({len(undocumented)}):")
            for n in undocumented[:LIST_LIMIT]:
                where = ", ".join(f"{u.file}:{u.line}" for u in used[n][:3])
                more = f" (+{len(used[n]) - 3} more)" if len(used[n]) > 3 else ""
                have = []
                if n in real_names:
                    have.append("set in a real env file")
                if n in defs:
                    have.append("defined in " + ", ".join(sorted(set(defs[n]))))
                tail = f" [{'; '.join(have)}]" if have else " [defined nowhere]"
                out.append(f"  {n}: {where}{more}{tail}")
        elif used:
            out.append("Every variable the code reads is documented in the template.")
        unused = sorted(n for n in documented if n not in used)
        if unused and templates:
            out.append(
                f"Documented but never read by scanned code ({len(unused)}): {names_list(unused)}\n"
                "  (frameworks, shell scripts, Dockerfiles and tools can read variables this scan cannot see)"
            )
        for n in sorted(used):
            warn = shapes.exposure_warning(n)
            if warn:
                where = f"{used[n][0].file}:{used[n][0].line}"
                out.append(f"  WARNING {n} (read at {where}): {warn}")
        ambient = sorted(n for n in used if is_ambient(n))
        if ambient:
            out.append(f"Ignored ambient variables: {names_list(ambient)}")
    out.extend(f"\nnote: {n}" for n in p.notes)
    return "\n".join(out)


# --------------------------------------------------------------- validate


def _fmt(sev: str, line: int | None, name: str, code: str, msg: str) -> str:
    where = f"line {line}" if line is not None else "file"
    who = f" {name}" if name else ""
    return f"  [{sev}] {where}{who}: {code} - {msg}"


def validate_file(f: EnvFile, base_dir: str) -> list[str]:
    pf = f.parsed
    out: list[str] = []
    if pf.bom:
        out.append(_fmt("warning", 1, "", "bom", "file starts with a UTF-8 BOM; some loaders glue it onto the first variable name"))
    if pf.crlf_lines:
        sev = "warning" if pf.lf_lines else "info"
        mixed = " mixed with LF lines" if pf.lf_lines else ""
        out.append(_fmt(sev, None, "", "crlf", f"{pf.crlf_lines} line(s) end with CRLF{mixed}; shell `source` and "
                        "docker --env-file can keep the \\r as part of the value"))
    for line, msg in pf.errors:
        out.append(_fmt("error", line, "", "syntax", msg))
    if f.truncated:
        out.append(_fmt("info", None, "", "truncated", "file is very large; only the start was read"))
    dups = pf.duplicates()
    check_paths = f.kind.env in {"", "local", "development", "dev", "test"}
    is_template = f.kind.kind == "template"
    for e in pf.entries:
        if dups.get(e.name) and e.line != dups[e.name][-1]:
            out.append(_fmt("warning", e.line, e.name, "duplicate",
                            f"defined again on line {dups[e.name][-1]}; this earlier value is ignored"))
        if e.trailing_ws:
            out.append(_fmt("warning", e.line, e.name, "trailing-whitespace", "unquoted value ends with spaces/tabs; some loaders keep them"))
        if e.unquoted_space:
            out.append(_fmt("warning", e.line, e.name, "unquoted-space", "unquoted value contains a space; quote it (shell `source` would split it)"))
        if e.has_cr:
            out.append(_fmt("warning", e.line, e.name, "cr-in-value", "value contains a carriage return (Windows line ending inside the value)"))
        if e.quoted_literal:
            out.append(_fmt("warning", e.line, e.name, "quoted-literal", "value itself starts and ends with quote characters, so the quotes are part of it"))
        for ref in e.unresolved:
            out.append(_fmt("warning", e.line, e.name, "undefined-reference", f"references ${{{ref}}}, which is not defined earlier in this file"))
        warn = shapes.exposure_warning(e.name)
        if warn:
            out.append(_fmt("error", e.line, e.name, "client-exposed-secret", warn))
        value = e.value
        if is_template:
            if value and shapes.is_secret_name(e.name) and not shapes.is_placeholder(value):
                out.append(_fmt("error", e.line, e.name, "real-looking-secret-in-template",
                                f"template holds a non-placeholder value (length {len(value)}) for a secret-looking name; "
                                "templates get committed"))
            continue
        if value == "":
            sev = "warning" if f.is_prod else "info"
            out.append(_fmt(sev, e.line, e.name, "empty", "empty value"))
            continue
        if shapes.is_placeholder(value):
            out.append(_fmt("warning", e.line, e.name, "placeholder", f"value looks like a placeholder (length {len(value)}) that was never replaced"))
            continue
        for sev, code, msg in shapes.check_value(e.name, value, base_dir, check_paths):
            out.append(_fmt(sev, e.line, e.name, code, msg))
        if f.is_prod and shapes.points_at_localhost(value):
            out.append(_fmt("error", e.line, e.name, "localhost-in-production", "production file points at localhost/127.0.0.1/0.0.0.0"))
    return out


def validate(p: Project, file: str | None) -> str:
    files = p.all_env_files()
    if file:
        files = [f for f in files if f.rel == file]
        if not files:
            raise EnvDoctorError(f"{file} is not an env file under the scanned folder")
    if not files:
        return "No .env files found to validate."
    out: list[str] = []
    total = 0
    for f in files:
        base = str(p.root / os.path.dirname(f.rel)) if os.path.dirname(f.rel) else str(p.root)
        findings = validate_file(f, base)
        total += len(findings)
        out.append(f"== {f.rel} [{_label(f)}, {len(f.parsed.names())} variable(s)] ==")
        out.extend(findings or ["  no problems found"])
    out.append(f"\n{total} finding(s) in {len(files)} file(s). Values are never shown; only names, line numbers and lengths.")
    out.extend(f"note: {n}" for n in p.notes)
    return "\n".join(out)


# --------------------------------------------------------------- example


def _type_hint(name: str, root: EnvRoot | None) -> str:
    guess = ""
    candidates: list[EnvFile] = []
    if root:
        candidates = sorted(root.real(), key=lambda f: (f.kind.env != "", f.rel)) + root.templates()
    for f in candidates:
        e = f.parsed.last().get(name)
        if e and e.value:
            t = shapes.guess_type(name, e.value)
            if t not in ("string", "empty"):
                guess = t
                break
    expected = shapes.expected_type(name)
    if expected in ("port", "bool", "int"):
        return expected
    return guess or expected or "string"


def generate_example(p: Project) -> str:
    usage = collect_usage(p)
    by_root = usage_by_root(p, usage)
    roots: list[tuple[str, EnvRoot | None]] = [(r.dir_rel, r) for r in p.roots]
    if any(k not in {r.dir_rel for r in p.roots} for k in by_root):
        roots.append((".", None))
    if not roots:
        return "Nothing to generate from: no env files and no environment reads found."
    sections: list[str] = []
    for dir_rel, root in roots:
        order: list[str] = []
        optional: set[str] = set()
        if root:
            for t in root.templates():
                for e in t.parsed.entries:
                    if e.name not in order:
                        order.append(e.name)
                    if e.optional:
                        optional.add(e.name)
                for n, _ in t.parsed.commented:
                    if n not in order:
                        order.append(n)
                    optional.add(n)
            others = sorted({n for f in root.real() for n in f.parsed.names()} - set(order))
            order.extend(others)
        used: dict[str, list[Usage]] = defaultdict(list)
        for u in by_root.get(dir_rel, []):
            used[u.name].append(u)
        order.extend(sorted(n for n in used if n not in order and not is_ambient(n)))
        lines = [f"# .env.example for {dir_rel}", "# Names and type hints only. Fill in real values in your own .env (never commit it).", ""]
        for n in order:
            ty = _type_hint(n, root)
            hint = f"# {n}: {ty}" + (f" - {_FORMAT_HINTS[ty]}" if ty in _FORMAT_HINTS else "")
            if n in optional:
                hint += " (optional)"
            lines.append(hint)
            if shapes.is_secret_name(n):
                lines.append("# secret: keep the real value out of git")
            if n in used:
                first = used[n][0]
                extra = f" (+{len(used[n]) - 1} more)" if len(used[n]) > 1 else ""
                lines.append(f"# read at {first.file}:{first.line}{extra}")
            warn = shapes.exposure_warning(n)
            if warn:
                lines.append(f"# WARNING: {warn}")
            lines.append(("# " if n in optional else "") + f"{n}=")
            lines.append("")
        if not order:
            lines.append("# (no variables found)")
        sections.append("\n".join(lines).rstrip())
    return "\n\n".join(sections)
