#!/usr/bin/env python3
"""Shadow rows over the test doubles a repository uses and what it declares about them.

Usage: fakes.py undeclared [--base B] [root]
       fakes.py counterpart [root]
       fakes.py stale [--today YYYY-MM-DD] [root]        (default root: the current directory)

A repository declares every fake, stub, mock or recorded replay its tests use as
a `[[fake]]` in `.github/fakes.toml` at its root; README.md, "The fake rows",
owns the shape, why each key is there, and how a double is recognised per
language.

undeclared   A test double in a test file that no [[fake]] declares, that is no
             fake whose `paths` match the file and whose `names` hold the
             double's name. With `--base`, only a double on a line that
             `git diff B...HEAD` added (a base of forty zeros is the empty tree),
             unless the change touches .github/fakes.toml, which sweeps every
             test file so that deleting a declaration cannot pass.
counterpart  A [[fake]] whose `real` file is gone, whose `real` no longer names its
             `real_name`, or whose `paths` match no file: it stands for nothing.
stale        A [[fake]] whose `recorded` date is more than `max_age_days` (default
             7) before today, or after it. `--today` fixes today; the default is
             today in UTC.

Exit status: 0 nothing to name, 1 a finding, 2 could not decide. Every exit 2
prints `fakes: refused (TYPE): ...`, and TYPE is one of:

bad-declaration   .github/fakes.toml does not parse or a [[fake]] is malformed.
tool-error        git failed, or a sibling script could not be loaded.
"""

import argparse
import datetime
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

# The imports would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
from gate import glob_re  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = ".github/fakes.toml"
NO_BASE = "0" * 40
DEFAULT_MAX_AGE = 7
REQUIRED = ("id", "paths", "names", "stands_for", "real", "compare", "recorded")
OPTIONAL = ("real_name", "max_age_days")


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def load_slop():
    """slop-rules.py, which owns what a test file is and what a change added."""
    try:
        spec = importlib.util.spec_from_file_location(
            "slop_rules", HERE / "slop-rules.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError, AttributeError) as e:
        raise Refused("tool-error", f"cannot load slop-rules.py: {e}") from e
    names = (
        "SKIP_DIRS",
        "git",
        "scope",
        "texts",
        "is_added",
        "TEST_NAME",
        "FIXTURE_DIR",
        "Refused",
    )
    missing = [n for n in names if not hasattr(module, n)]
    if missing:
        raise Refused("tool-error", f"slop-rules.py lacks {', '.join(missing)}")
    return module


# --- the declaration ---------------------------------------------------------


def bad(fid, detail):
    return Refused("bad-declaration", f"{CONFIG}: [[fake]] {fid!r}: {detail}")


def text_list(decl, key):
    value = decl[key]
    if not (
        isinstance(value, list)
        and value
        and all(isinstance(v, str) and v.strip() for v in value)
    ):
        raise bad(decl.get("id"), f"{key} must be a non-empty list of strings")
    return value


def text(decl, key):
    value = decl[key]
    if not isinstance(value, str) or not value.strip():
        raise bad(decl.get("id"), f"{key} must be a non-empty string")
    return value


def declarations(root):
    """Each [[fake]], checked; [] when the repository declares none."""
    path = Path(root) / CONFIG
    if not path.is_file():
        return []
    try:
        found = tomllib.loads(path.read_text()).get("fake", [])
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise Refused("bad-declaration", f"{CONFIG}: {e}") from e
    if not isinstance(found, list) or not all(isinstance(d, dict) for d in found):
        raise Refused("bad-declaration", f"{CONFIG}: `fake` must be [[fake]] tables")
    fakes, seen = [], set()
    for decl in found:
        fid = decl.get("id")
        missing = [k for k in REQUIRED if k not in decl]
        unknown = sorted(set(decl) - set(REQUIRED) - set(OPTIONAL))
        if missing or unknown:
            # An unknown key is refused because a misspelt optional one would
            # otherwise fall back to its default without a word.
            raise bad(fid, f"missing {missing}, unknown {unknown}")
        fid = text(decl, "id")
        if fid in seen:
            raise bad(fid, "a second [[fake]] has this id")
        seen.add(fid)
        recorded = decl["recorded"]
        # tomllib reads `2026-10-19` as a date and a quoted one as a string.
        if type(recorded) is not datetime.date:
            raise bad(fid, "recorded must be a bare TOML date, such as 2026-10-19")
        max_age = decl.get("max_age_days", DEFAULT_MAX_AGE)
        if type(max_age) is not int or max_age < 1:
            raise bad(fid, "max_age_days must be a whole number of days, 1 or more")
        real_name = decl.get("real_name")
        fakes.append(
            {
                "id": fid,
                "paths": [glob_re(g) for g in text_list(decl, "paths")],
                "names": set(text_list(decl, "names")),
                "real": text(decl, "real"),
                "real_name": None if real_name is None else text(decl, "real_name"),
                "compare": text(decl, "compare"),
                "stands_for": text(decl, "stands_for"),
                "recorded": recorded,
                "max_age": max_age,
            }
        )
    return fakes


# --- what a test double looks like, per language ------------------------------

LANG = {
    ".py": "py",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".mts": "js",
    ".cts": "js",
    ".go": "go",
    ".rs": "rs",
    ".sh": "sh",
    ".bash": "sh",
    ".bats": "sh",
}
CONFTEST = re.compile(r"(?:^|/)conftest\.py$")
CFG_TEST = re.compile(r"^\s*#\[cfg\(test\)\]")
COMMENT = {
    "py": ("#",),
    "sh": ("#",),
    "js": ("//", "/*", "*"),
    "go": ("//", "/*", "*"),
    "rs": ("//", "/*", "*"),
}

# A type or function the author named a double: Fake, Stub, Mock or Dummy, then
# the end of the word, `_`, a digit or a capital, so `mocker` and `stubs` are not.
NAME = r"((?:[Ff]ake|[Ss]tub|[Mm]ock|[Dd]ummy)(?:[A-Z0-9_]\w*)?)(?!\w)"
HAND = {
    "py": [rf"^\s*(?:async\s+)?(?:class|def)\s+{NAME}"],
    "js": [
        rf"\b(?:class|function\*?|interface|type)\s+{NAME}",
        rf"\b(?:const|let|var)\s+{NAME}\s*=",
    ],
    "go": [rf"^\s*type\s+{NAME}", rf"^func\s+{NAME}\s*[(\[]"],
    "rs": [rf"\b(?:struct|enum|trait|type)\s+{NAME}", rf"\bfn\s+{NAME}\s*[<(]"],
    "sh": [rf"^\s*(?:function\s+)?{NAME}\s*\(\)", rf"^\s*function\s+{NAME}\b"],
}
# A mocking library's call, named by its first argument when that is a string
# literal (the module, target or host it replaces) and by the call otherwise.
MOCK = {
    "py": [
        r"(?<![\w.])(?:unittest\.mock\.|mock\.)?patch(?:\.object|\.dict|\.multiple)?(?=\()",
        r"(?<!\w)(?:Magic|Async|NonCallable)?Mock(?=\()",
        r"\bmonkeypatch\.\w+(?=\()",
        r"\bmocker\.\w+(?=\()",
        r"\bresponses\.(?:add|activate|start|replace|upsert)\b",
        r"\bfreeze_time(?=\()",
    ],
    "js": [
        r"\b(?:vi|jest)\.(?:mock|doMock|fn|spyOn|stubGlobal|stubEnv|useFakeTimers)(?=\()",
        r"\bsinon\.\w+(?=\()",
        r"(?<![\w.])nock(?=\()",
        r"(?<![\w.])(?:\w+\.)?mock\.(?:fn|method|getter|setter|module|timers\.enable)(?=\()",
        r"\bmockIPC(?=\()",
        r"\bpage\.route(?=\()",
        r"\bsetupServer(?=\()",
    ],
    "go": [r"\bgomock\.\w+(?=\()", r"\bmock\.Mock\b"],
    "rs": [r"#\[automock\]", r"\bmock!\s*\{", r"\bmockall::\w+"],
    "sh": [],
}
HAND = {k: [re.compile(p) for p in v] for k, v in HAND.items()}
MOCK = {k: [re.compile(p) for p in v] for k, v in MOCK.items()}
FIRST_LITERAL = re.compile(r"""\(\s*(["'`])((?:\\.|(?!\1).)+?)\1""")

SHEBANG = re.compile(r"""(["'`])#!""")
STRING = re.compile(r"""(["'`])((?:\\.|(?!\1).)*)\1""")
IDENT = re.compile(r"[A-Za-z_]\w*")
COMPARED = re.compile(r"(?:==|!=)=?\s*$")
# A write call: a method's receiver (`gh.write_text(`) or a function's arguments
# (`os.WriteFile(path, `, `fs::write(path, `) hold the path.
WRITE = re.compile(
    r"(?P<method>\.\s*(?:write_text|write_bytes|write)\s*\()"
    r"|\b(?:writeFileSync|writeFile|WriteFile|write)\s*\("
)
DOUBLE_WORD = re.compile(r"fake|stub|mock|shim", re.I)
ASSIGNED = re.compile(
    r"^\s*(?:(?:const|let|var|let\s+mut)\s+)?([A-Za-z_]\w*)\s*(?::\s*[^=]+?\s*)?:?=\s*(.+)$"
)
TARGET = re.compile(r"""\s*("[^"]+"|'[^']+'|[^\s;|&<>'"]+)""")
HEREDOC = re.compile(r"<<-?\s*['\"]?\w+")
SH_ASSIGN = re.compile(r"""(?<![\w$])([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;|&]+)""")
CHMOD = re.compile(r"\bchmod\s+(?:-[A-Za-z]+\s+)*(\S+)\s+([^;|&#]+)")


def unquote(word):
    return re.sub(r"\$\{(\w+)\}", r"$\1", word.strip().strip("\"'"))


def basename(path):
    return path.rstrip("/").rsplit("/", 1)[-1]


def last_component(expr, lines, index, depth=0):
    """The basename a path expression spells: its last literal, else the variable holding its end.

    A literal that is compared (`=== "win32"`) or holds a space is no path, and a
    call, an object or an attribute is not the variable that holds the name.
    """
    literals = [
        m[2]
        for m in STRING.finditer(expr)
        if m[2]
        and not re.search(r"\s", m[2])
        and not COMPARED.search(expr[: m.start()])
    ]
    if literals:
        return basename(literals[-1])
    code = STRING.sub('""', expr)
    words = [
        m[0]
        for m in IDENT.finditer(code)
        if not re.match(r"\s*[.(]", code[m.end() :])
        and not code[: m.start()].rstrip().endswith(".")
    ]
    if not words:
        return None
    if depth == 0:
        for prior in range(index - 1, -1, -1):
            m = ASSIGNED.match(lines[prior])
            if m and m[1] == words[-1]:
                return last_component(m[2], lines, prior, 1) or words[-1]
    return words[-1]


def redirects(line):
    """The targets of the `>` and `>>` of a shell line, outside quotes and comments."""
    found, quote, i = [], None, 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        elif c == ">":
            j = i + 2 if line[i + 1 : i + 2] == ">" else i + 1
            target = TARGET.match(line, j) if line[j : j + 1] != "&" else None
            if target:
                found.append(unquote(target[1]))
                i = target.end()
                continue
        i += 1
    return found


def sh_name(target, lines, index):
    """A redirect target's basename, through the variable that holds it when it is one."""
    name = basename(target)
    held = re.fullmatch(r"\$(\w+)", name)
    if held:
        for prior in range(index - 1, -1, -1):
            values = [v for n, v in SH_ASSIGN.findall(lines[prior]) if n == held[1]]
            if values:
                return basename(unquote(values[-1])) or name
    return name


def written_program(lines, index, lang):
    """The name of a program a line writes, from a `#!` literal on it, or None."""
    line = lines[index]
    if lang == "sh":
        if SHEBANG.search(line) or (
            HEREDOC.search(line)
            and index + 1 < len(lines)
            and lines[index + 1].lstrip().startswith("#!")
        ):
            # A command continued with `\` may name its target on a later line.
            end = index
            while lines[end].endswith("\\") and end + 1 < len(lines):
                end += 1
            targets = [
                t
                for t in redirects(" ".join(lines[index : end + 1]))
                if t != "/dev/null"
            ]
            return sh_name(targets[-1], lines, index) if targets else None
        return None
    hit = SHEBANG.search(line)
    if not hit:
        return None
    before = line[: hit.start()]
    writes = list(WRITE.finditer(before))
    if writes:
        w = writes[-1]
        path = before[: w.start()] if w["method"] else before[w.end() :]
        return last_component(path, lines, index)
    # A program's source held in a variable its author named a double, such as `STUB = """#!`.
    held = ASSIGNED.match(before + '""')
    return held[1] if held and DOUBLE_WORD.search(held[1]) else None


def path_programs(lines, lang):
    """{line index: name} for each file a shell test makes executable and puts on a PATH."""
    if lang != "sh":
        return {}
    dirs, seams = set(), set()
    for line in lines:
        for name, value in SH_ASSIGN.findall(line):
            value = unquote(value)
            if name == "PATH" or name.endswith("_PATH") or "$PATH" in value:
                dirs.update(unquote(e) for e in value.split(":"))
            elif name.endswith("_BIN"):
                seams.add(value)
    dirs -= {"", "$PATH"}
    found = {}
    for index, line in enumerate(lines):
        for mode, operands in CHMOD.findall(line):
            octal = re.fullmatch(r"[0-7]{3,4}", mode)
            if not (
                re.fullmatch(r"[ugoa]*\+[rwxXst]*x[rwxXst]*", mode)
                or (octal and any(int(d) & 1 for d in mode[-3:]))
            ):
                continue
            for operand in map(unquote, operands.split()):
                head = operand.rsplit("/", 1)[0] if "/" in operand else "."
                if head in dirs or operand in seams:
                    found.setdefault(index, basename(operand))
    return found


def test_span(rel, text):
    """(language, first test line index) for a test file, or (language, None)."""
    lang = LANG.get(Path(rel).suffix)
    if lang is None or LINT.FIXTURE_DIR.search(rel):
        return lang, None
    if LINT.TEST_NAME.search(rel) or CONFTEST.search(rel):
        return lang, 0
    if lang == "rs":
        lines = text.splitlines()
        return lang, next(
            (i for i, line in enumerate(lines) if CFG_TEST.match(line)), None
        )
    return lang, None


def doubles(rel, text):
    """(line number, name, kind) for each test double of a test file, first sighting per name."""
    lang, start = test_span(rel, text)
    if start is None:
        return []
    lines = text.splitlines()
    on_path = path_programs(lines, lang)
    found, seen = [], set()

    def add(index, name, kind):
        if name and name not in seen:
            seen.add(name)
            found.append((index + 1, name, kind))

    for index in range(start, len(lines)):
        line = lines[index]
        if line.lstrip().startswith(COMMENT[lang]) and not line.lstrip().startswith(
            "#!"
        ):
            continue
        for pattern in HAND[lang]:
            for m in pattern.finditer(line):
                add(index, m[1], "the hand fake")
        for pattern in MOCK[lang]:
            for m in pattern.finditer(line):
                arg = FIRST_LITERAL.match(line, m.end())
                add(index, arg[2] if arg else re.sub(r"\s+", "", m[0]), "the mock")
        add(index, written_program(lines, index, lang), "the stand-in program")
        add(index, on_path.get(index), "the stand-in program")
    return sorted(found)


# --- the rows ----------------------------------------------------------------


def undeclared(root, base, fakes, _today):
    change = LINT.scope(root, base)
    if CONFIG in change:
        change = LINT.scope(root, None)
    found, seen = [], 0

    def judged(rel):
        # A vendored or installed tree is someone else's tests, whether or not it is tracked.
        return Path(rel).suffix in LANG and not set(rel.split("/")) & LINT.SKIP_DIRS

    for rel, body, added in LINT.texts(root, change, accept=judged):
        for number, name, kind in doubles(rel, body):
            if not LINT.is_added(added, number):
                continue
            seen += 1
            if any(
                name in f["names"] and any(g.match(rel) for g in f["paths"])
                for f in fakes
            ):
                continue
            found.append(f"{rel}:{number}: {kind} {name} is not declared in {CONFIG}")
    return found, f"every test double is declared ({seen} seen, {len(fakes)} declared)"


def counterpart(root, _base, fakes, _today):
    files = list(LINT.scope(root, None))
    found = []
    for f in fakes:
        real = Path(root) / f["real"]
        if not real.is_file():
            found.append(
                f"{f['id']}: {f['real']} does not exist, so the fake stands for nothing"
            )
        elif f["real_name"] and not re.search(
            rf"(?<![\w-]){re.escape(f['real_name'])}(?![\w-])",
            real.read_text(errors="replace"),
        ):
            found.append(f"{f['id']}: {f['real']} no longer names {f['real_name']}")
        if not any(g.match(p) for p in files for g in f["paths"]):
            found.append(
                f"{f['id']}: no file matches its paths, so there is no fake to declare"
            )
    return (
        found,
        f"every declared fake has its real counterpart ({len(fakes)} declared)",
    )


def stale(_root, _base, fakes, today):
    found = []
    for f in fakes:
        age = (today - f["recorded"]).days
        if age < 0:
            found.append(f"{f['id']}: recorded {f['recorded']} is after today, {today}")
        elif age > f["max_age"]:
            found.append(
                f"{f['id']}: recorded {f['recorded']}, {age} days ago, past its"
                f" {f['max_age']}-day freshness; re-run {f['compare']!r} and move recorded"
            )
    return found, f"every declared fake is fresh ({len(fakes)} declared, today {today})"


ROWS = {"undeclared": undeclared, "counterpart": counterpart, "stale": stale}
LINT = None


def main():
    global LINT
    ap = argparse.ArgumentParser(
        usage="fakes.py undeclared|counterpart|stale [--base B] [--today YYYY-MM-DD] [root]"
    )
    ap.add_argument("row", choices=ROWS)
    ap.add_argument("--base")
    ap.add_argument("--today", type=datetime.date.fromisoformat)
    ap.add_argument("root", nargs="?", default=".")
    # Plain parse_args consumes the optional root early on Python before 3.12.7 and rejects it after --base.
    args = ap.parse_intermixed_args()
    if args.base and args.row != "undeclared":
        ap.error(
            "--base applies to undeclared only; the other rows read every declaration"
        )
    if args.today and args.row != "stale":
        ap.error("--today applies to stale only")
    root = Path(args.root).resolve()
    today = args.today or datetime.datetime.now(datetime.UTC).date()
    try:
        LINT = load_slop()
        try:
            base = None
            if args.base:
                base = args.base
                if base != NO_BASE:
                    base = LINT.git(
                        root, "rev-parse", "--verify", f"{base}^{{commit}}"
                    ).strip()
            found, clean = ROWS[args.row](root, base, declarations(root), today)
        except LINT.Refused as e:
            raise Refused(e.kind, str(e)) from e
    except Refused as e:
        print(f"fakes: refused ({e.kind}): {e}")
        return 2
    for line in found:
        print(f"fakes: {args.row}: {line}")
    if found:
        print(f"fakes: {len(found)} finding(s)")
        return 1
    print(f"fakes: {clean}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
