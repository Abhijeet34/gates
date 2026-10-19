#!/usr/bin/env python3
"""Shadow rows over the test doubles a repository uses and what it declares about them.

Usage: fakes.py undeclared [--base B] [root]
       fakes.py counterpart [root]
       fakes.py stale [--today YYYY-MM-DD] [root]        (default root: the current directory)
       fakes.py compare [--id ID] [--api URL] [--out DIR] [--write] [--today YYYY-MM-DD] [root]

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
             7) before today, or after it, or that carries a `drift` its last
             compare wrote. `--today` fixes today; the default is today in UTC.
compare      Each `[[fake.github]]` replay of a [[fake]] whose recorded reply no
             longer has the shape of a fresh real reply to the same GET request:
             a field added, removed or retyped, or another status. Values are not
             compared. Every declared GitHub fake, or the one `--id` names; this
             is the daily real smoke. `--api` replaces https://api.github.com,
             `--out` keeps each fresh reply under DIR at its recording's path,
             a new replay's first recording included, and `--write` moves a
             matching fake's `recorded` to today and gives a drifted one a
             `drift`, which `stale` then refuses.

Exit status: 0 nothing to name, 1 a finding, 2 could not decide. Every exit 2
prints `fakes: refused (TYPE): ...`, and TYPE is one of:

bad-declaration   .github/fakes.toml does not parse or a [[fake]] is malformed,
                  or a replay's recording is missing or not JSON.
tool-error        git failed, a sibling script could not be loaded, or --write
                  could not rewrite .github/fakes.toml by line.
network           a real request failed, timed out, answered 5xx or not JSON.
rate-limited      GitHub answered 429, or 403 with no requests left.
nothing-to-compare  compare found no GitHub fake declared, or none with --id.

compare exits 1 when any fake drifted, even if another could not be compared.
"""

import argparse
import copy
import datetime
import http.client
import importlib.util
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
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
OPTIONAL = ("real_name", "max_age_days", "github", "drift")
REPLAY_KEYS = {"request", "recording", "status"}
# A GET path and query on the API host: no scheme, no `//` that could name
# another host, no whitespace.
REQUEST = re.compile(r"/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*")


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
        drift = decl.get("drift")
        if drift is not None and (not isinstance(drift, str) or not drift.strip()):
            raise bad(fid, "drift must be a non-empty string")
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
                "github": replays(decl, fid),
                "drift": drift,
            }
        )
    return fakes


def replays(decl, fid):
    """The [[fake.github]] tables of one [[fake]], checked; [] for a fake of no GitHub request."""
    found = decl.get("github", [])
    if not isinstance(found, list) or not all(isinstance(r, dict) for r in found):
        raise bad(fid, "github must be [[fake.github]] tables")
    checked = []
    for r in found:
        missing = sorted({"request", "recording"} - set(r))
        unknown = sorted(set(r) - REPLAY_KEYS)
        if missing or unknown:
            raise bad(fid, f"[[fake.github]] missing {missing}, unknown {unknown}")
        request, recording = r["request"], r["recording"]
        # Only GET is ever sent, so a method in the declaration is refused
        # rather than silently read as one.
        if (
            not isinstance(request, str)
            or not REQUEST.fullmatch(request)
            or ".." in request.split("?")[0].split("/")
        ):
            raise bad(fid, f"request {request!r} must be a GET path such as /repos/o/r")
        if (
            not isinstance(recording, str)
            or not recording.strip()
            or Path(recording).is_absolute()
            or ".." in Path(recording).parts
        ):
            raise bad(fid, f"recording {recording!r} must be a path inside the tree")
        status = r.get("status", 200)
        if type(status) is not int or not 100 <= status <= 599:
            raise bad(fid, f"status {status!r} must be an HTTP status, such as 200")
        checked.append({"request": request, "recording": recording, "status": status})
    return checked


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
        if f["drift"]:
            found.append(
                f"{f['id']}: its last compare found drift ({f['drift']});"
                f" update the recording or the code, then re-run {f['compare']!r}"
            )
    return found, f"every declared fake is fresh ({len(fakes)} declared, today {today})"


# --- record and compare: a GitHub fake against a fresh real reply ------------

API = "https://api.github.com"
TIMEOUT = 30
MAX_REPLY = 10 * 2**20
KEY = re.compile(r"[A-Za-z0-9_-]+")


class Obj(dict):
    """An object's shape: {key: shape}, and the keys some items of its array lack."""

    optional = frozenset()


def shape(value):
    """A JSON value's shape: a type name, an Obj for an object, [element shape] for an array.

    An array's element shape is the merge of every element's, so a list's length
    and order are values and not shape.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        merged = None
        for item in value:
            merged = shape(item) if merged is None else merge(merged, shape(item))
        return [] if merged is None else [merged]
    return Obj({k: shape(v) for k, v in value.items()})


def kind(s):
    return "object" if isinstance(s, dict) else "array" if isinstance(s, list) else s


def merge(a, b):
    if a == "null":
        return b
    if b == "null":
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        merged = Obj(
            {
                k: merge(a[k], b[k]) if k in a and k in b else a.get(k, b.get(k))
                for k in a | b
            }
        )
        merged.optional = a.optional | b.optional | (a.keys() ^ b.keys())
        return merged
    if isinstance(a, list) and isinstance(b, list):
        return [merge(a[0], b[0])] if a and b else a or b
    if a == b:
        return a
    return " or ".join(sorted(set(kind(a).split(" or ")) | set(kind(b).split(" or "))))


def step(path, key):
    return (
        f"{path}.{key}" if KEY.fullmatch(key) else f"{path or '.'}[{json.dumps(key)}]"
    )


def drift(recorded, real, path=""):
    """Each way `real` differs in shape from `recorded`, in key order.

    null matches any type, because GitHub answers null for an optional value it
    lacks (a repository's description, a pull request's merged_at); an empty
    array matches any array, because it shows no element to compare; and a key
    some recorded items lack may be missing from every real one.
    """
    if recorded == "null" or real == "null":
        return []
    if kind(recorded) != kind(real):
        return [f"retyped {path or '.'} from {kind(recorded)} to {kind(real)}"]
    if isinstance(recorded, dict):
        found = []
        for key in sorted(recorded.keys() | real.keys()):
            at = step(path, key)
            if key not in recorded:
                found.append(f"added {at} ({kind(real[key])})")
            elif key not in real:
                if key not in recorded.optional:
                    found.append(f"removed {at} ({kind(recorded[key])})")
            else:
                if key in real.optional and key not in recorded.optional:
                    found.append(
                        f"removed {at} from some items ({kind(recorded[key])})"
                    )
                found += drift(recorded[key], real[key], at)
        return found
    if isinstance(recorded, list) and recorded and real:
        return drift(recorded[0], real[0], f"{path or '.'}[]")
    return []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect is a status to report, and following one would carry the token
    # to wherever it points.
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch(api, request):
    """(status, body) of one GET; only the CI job's own token, only to GitHub itself."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gates-fakes-compare",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if api == API and token and os.environ.get("GITHUB_ACTIONS") == "true":
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api + request, headers=headers, method="GET")
    try:
        try:
            reply = urllib.request.build_opener(NoRedirect).open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            reply = e
        with reply:
            status, replied = reply.status, reply.headers
            # One read(n) may return less than is there, so read to the end.
            body = b""
            while len(body) <= MAX_REPLY and (chunk := reply.read(2**16)):
                body += chunk
    except (OSError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        raise Refused("network", f"GET {request}: {reason!r}") from e
    if status == 429 or (status == 403 and replied.get("x-ratelimit-remaining") == "0"):
        raise Refused(
            "rate-limited",
            f"GET {request}: status {status}, resets at {replied.get('x-ratelimit-reset')}",
        )
    if status >= 500:
        raise Refused("network", f"GET {request}: status {status}")
    if len(body) > MAX_REPLY:
        raise Refused("network", f"GET {request}: reply over {MAX_REPLY} bytes")
    return status, body


def compare_fake(root, api, out, f):
    """Drift lines for one fake's replays; Refused when it cannot be compared."""
    found, missing = [], []
    for r in f["github"]:
        where = f"{r['recording']} (GET {r['request']})"
        status, body = fetch(api, r["request"])
        if out:
            kept = out / r["recording"]
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(body)
        # Read after the fetch, so `--out` gives every new replay its first recording.
        try:
            recorded = json.loads((root / r["recording"]).read_bytes())
        except FileNotFoundError:
            missing.append(r["recording"])
            continue
        except (ValueError, OSError) as e:
            raise bad(f["id"], f"recording {r['recording']} is not JSON: {e}") from e
        if status != r["status"]:
            found.append(f"{where}: status {r['status']} recorded, {status} now")
            continue
        try:
            real = json.loads(body)
        except ValueError as e:
            raise Refused(
                "network", f"GET {r['request']}: the reply is not JSON: {e}"
            ) from e
        found += [f"{where}: {d}" for d in drift(shape(recorded), shape(real))]
    if missing:
        raise bad(f["id"], f"recording {', '.join(missing)} does not exist")
    return found


HEADER = re.compile(r"\s*\[\[?\s*[A-Za-z0-9_.\"-]+\s*\]\]?\s*(?:#.*)?")
RECORDED_LINE = re.compile(r"(\s*recorded\s*=\s*)([^\s#]+)(.*)", re.S)
DRIFT_LINE = re.compile(r"\s*drift\s*=")
FAKE_HEADER = re.compile(r"\s*\[\[\s*fake\s*\]\]\s*(?:#.*)?")


def rewrite(root, index, today, drifted):
    """Move the index-th [[fake]]'s `recorded` to today and drop its `drift`, or set `drift`.

    Edited by line so the file's comments and layout stay, then re-read: any
    difference but the intended one refuses rather than writing.
    """
    path = root / CONFIG
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if FAKE_HEADER.fullmatch(line.rstrip())]
    begin = starts[index] if index < len(starts) else len(lines)
    end = next(
        (
            i
            for i in range(begin + 1, len(lines))
            if HEADER.fullmatch(lines[i].rstrip("\n"))
        ),
        len(lines),
    )
    kept, seen = [], 0
    for line in lines[begin:end]:
        if DRIFT_LINE.match(line):
            continue
        m = RECORDED_LINE.match(line)
        if m:
            seen += 1
            line = m[1] + (m[2] if drifted else today.isoformat()) + m[3]
            line += "" if line.endswith("\n") else "\n"
            if drifted:
                line += f"drift = {json.dumps(drifted, ensure_ascii=False)}\n"
        kept.append(line)
    new = "".join(lines[:begin] + kept + lines[end:])
    want = copy.deepcopy(tomllib.loads(text))
    entry = want["fake"][index]
    if drifted:
        entry["drift"] = drifted
    else:
        entry["recorded"] = today
        entry.pop("drift", None)
    try:
        ok = seen == 1 and tomllib.loads(new) == want
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:
        raise Refused(
            "tool-error",
            f"cannot rewrite [[fake]] {entry['id']!r} in {CONFIG} by line;"
            " set its recorded or drift by hand",
        )
    path.write_text(new)


def compare(root, api, out, only, write, fakes, today):
    chosen = [
        (i, f) for i, f in enumerate(fakes) if f["github"] and only in (None, f["id"])
    ]
    if not chosen:
        named = f"with id {only!r}" if only else "declared"
        raise Refused(
            "nothing-to-compare", f"no [[fake]] with a [[fake.github]] replay {named}"
        )
    found, refused, replies = [], [], 0
    for index, f in chosen:
        try:
            lines = compare_fake(root, api, out, f)
            replies += len(f["github"])
            found += [f"{f['id']}: {line}" for line in lines]
            if write:
                more = f" (and {len(lines) - 1} more)" if len(lines) > 1 else ""
                rewrite(
                    root, index, today, f"{today}: {lines[0]}{more}" if lines else None
                )
        except Refused as e:
            refused.append(e)
    return found, refused, replies, len(chosen)


ROWS = {"undeclared": undeclared, "counterpart": counterpart, "stale": stale}
LINT = None


def main():
    global LINT
    ap = argparse.ArgumentParser(
        usage="fakes.py undeclared|counterpart|stale|compare [--base B] [--today YYYY-MM-DD]"
        " [--id ID] [--api URL] [--out DIR] [--write] [root]"
    )
    ap.add_argument("row", choices=[*ROWS, "compare"])
    ap.add_argument("--base")
    ap.add_argument("--today", type=datetime.date.fromisoformat)
    ap.add_argument("--id")
    ap.add_argument("--api", default=API)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("root", nargs="?", default=".")
    # Plain parse_args consumes the optional root early on Python before 3.12.7 and rejects it after --base.
    args = ap.parse_intermixed_args()
    if args.base and args.row != "undeclared":
        ap.error(
            "--base applies to undeclared only; the other rows read every declaration"
        )
    if args.today and args.row not in ("stale", "compare"):
        ap.error("--today applies to stale and compare only")
    own = args.id or args.out or args.write or args.api != API
    if own and args.row != "compare":
        ap.error("--id, --api, --out and --write apply to compare only")
    api = args.api.rstrip("/")
    if not re.fullmatch(r"https?://[^/?#\s]+", api):
        ap.error(f"--api {args.api!r} must be an http or https origin")
    root = Path(args.root).resolve()
    today = args.today or datetime.datetime.now(datetime.UTC).date()
    if args.row == "compare":
        return run_compare(root, api, args, today)
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


def run_compare(root, api, args, today):
    try:
        found, refused, replies, fakes = compare(
            root, api, args.out, args.id, args.write, declarations(root), today
        )
    except Refused as e:
        print(f"fakes: refused ({e.kind}): {e}")
        return 2
    for line in found:
        print(f"fakes: compare: {line}")
    for e in refused:
        print(f"fakes: refused ({e.kind}): {e}")
    if found:
        print(f"fakes: {len(found)} finding(s)")
        return 1
    if refused:
        return 2
    print(
        f"fakes: every declared GitHub fake has the shape of its real reply"
        f" ({replies} replies, {fakes} fakes)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
