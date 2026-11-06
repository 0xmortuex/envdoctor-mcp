"""Parser semantics, including a cross-check against python-dotenv."""

from __future__ import annotations

import io

import pytest

from envdoctor_mcp.dotenv import parse

BOM = chr(0xFEFF)

# Corpus where python-dotenv and this parser must agree on every value.
CORPUS = [
    "A=1\n",
    "A = 1\n",
    "export A=1\n",
    "export   A=1\nB=2\n",
    "A=\n",
    "A=   \n",
    "A=hello world\n",
    "A=hello   # trailing comment\n",
    "A=hello#notacomment\n",
    "A=#starts with hash\n",
    "A='single quoted # not a comment'\n",
    'A="double quoted # not a comment"\n',
    'A="line1\\nline2"\n',
    "A='line1\\nline2'\n",
    'A="say \\"hi\\""\n',
    "A='it\\'s'\n",
    'A="tab\\there"\n',
    'A="multi\nline\nvalue"\n',
    "A='multi\nline'\n",
    'A="x"   # comment after quote\n',
    "A=1\nA=2\n",
    "# only a comment\n\nA=1\n   # indented comment\nB=2\n",
    "A=1\r\nB=2\r\n",
    "A=a=b=c\n",
    'A="a=b"\n',
    'A=""\n',
    "A=''\n",
    "FOO.BAR=1\nFOO-BAR=2\n",
    "  A=1\n",
    "A=C:\\path\\to\\file\n",
    'A="C:\\\\path"\n',
]


@pytest.mark.parametrize("text", CORPUS)
def test_matches_python_dotenv_values(text: str) -> None:
    dotenv = pytest.importorskip("dotenv")
    expected = dotenv.dotenv_values(stream=io.StringIO(text), interpolate=False)
    got = {e.name: e.value for e in parse(text).entries}
    assert got == expected, text


def test_interpolation_matches_python_dotenv() -> None:
    dotenv = pytest.importorskip("dotenv")
    text = (
        "ED_BASE=/srv\n"
        "ED_DIR=${ED_BASE}/app\n"
        "ED_MISSING_DEFAULT=${ED_NOPE_X:-fallback}\n"
        'ED_DQ="${ED_BASE}/dq"\n'
        "ED_SQ='${ED_BASE}/sq'\n"
        "ED_EMPTY=\n"
        "ED_EMPTY_DEFAULT=${ED_EMPTY:-used}\n"
        "ED_EMPTY_DASH=${ED_EMPTY-unused}\n"
    )
    expected = dotenv.dotenv_values(stream=io.StringIO(text), interpolate=True)
    got = {e.name: e.value for e in parse(text).entries}
    assert got == expected
    assert got["ED_DIR"] == "/srv/app"


def test_flags_and_shapes() -> None:
    pf = parse(
        "# optional: not needed in dev\n"
        "OPT=1\n"
        "A=hello world\n"
        "B=trailing   \n"
        "C=ok   # comment\n"
        'D="\'x\'"\n'
        "E=${NOPE}/x\n"
        "# COMMENTED=1\n"
        'M="a\nb"\n'
    )
    e = {x.name: x for x in pf.entries}
    assert e["OPT"].optional and not e["A"].optional
    assert e["A"].unquoted_space and not e["A"].trailing_ws
    assert e["B"].trailing_ws and e["B"].value == "trailing"
    assert not e["C"].trailing_ws and e["C"].value == "ok"
    assert e["D"].quoted_literal
    assert e["E"].unresolved == ("NOPE",) and e["E"].refs == ("NOPE",)
    assert pf.commented == [("COMMENTED", 8)]
    assert e["M"].multiline and e["M"].line == 9
    assert pf.errors == []


def test_duplicates_and_last_wins() -> None:
    pf = parse("A=1\nB=x\nA=2\nA=3\n")
    assert pf.duplicates() == {"A": [1, 3, 4]}
    assert pf.last()["A"].value == "3"


def test_bom_and_crlf_detected() -> None:
    pf = parse(BOM + "A=1\r\nB=2\r\nC=3\n")
    assert pf.bom and pf.crlf_lines == 2 and pf.lf_lines == 1
    assert [e.name for e in pf.entries] == ["A", "B", "C"]
    assert pf.entries[0].value == "1"


def test_cr_inside_quoted_value() -> None:
    pf = parse('A="one\r\ntwo"\r\n')
    assert pf.entries[0].has_cr and pf.entries[0].multiline


def test_syntax_errors_carry_no_text() -> None:
    pf = parse(
        "good=1\n"
        "this line has PLANTED_BAD_LINE_77 words\n"
        "K='unterminated PLANTED_UNTERM_88\nrest of file\n"
    )
    assert [e.name for e in pf.entries] == ["good"]
    assert pf.errors == [
        (2, "unexpected text after the variable name"),
        (3, "unterminated quoted value (runs to end of file)"),
    ]
    assert "PLANTED" not in repr(pf.errors)


def test_text_after_closing_quote_is_an_error() -> None:
    pf = parse('A="x" PLANTED_TAIL_1\nB=2\n')
    assert pf.errors == [(1, "unexpected text after the closing quote")]
    assert [e.name for e in pf.entries] == ["B"]


def test_entry_repr_hides_value() -> None:
    pf = parse("SECRET=PLANTED_REPR_99\n")
    assert "PLANTED_REPR_99" not in repr(pf.entries[0])
    assert "PLANTED_REPR_99" not in repr(pf)


def test_export_prefix_and_spaces_around_equals() -> None:
    pf = parse("export A = 1\nexportB=2\n")
    assert [(e.name, e.value) for e in pf.entries] == [("A", "1"), ("exportB", "2")]
