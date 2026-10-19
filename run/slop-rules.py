#!/usr/bin/env python3
"""Shadow rows for slop that a tree alone can answer.

Usage: slop-rules.py ROW [--base B] [root]    (default root: the current directory)

With `--base`, a row judges what `git diff B...HEAD` added (a base of forty
zeros is the empty tree); without it, every file under root. Exit status: 0
nothing to name, 1 a finding, 2 could not decide. Each finding is one line,
`slop-rules: ROW: path:LINE: what`, and ROW is one of:

tests            A test that reads the text of a non-test source file and asserts on
                 it. "A test asserts behaviour through the public entry point,
                 never on source text" (the maintainer's global agent instructions,
                 "Building"; the learning-loop report's layer 0 item 7). Python is
                 read as a syntax tree: an assertion over a name assigned from a read
                 of a source file's text, or over the read itself. Shell is a `grep`
                 whose file operand is a source file; JavaScript, TypeScript, Rust
                 and Go are a read of a source file in a test file that also asserts.
                 A source file has a code extension and is not itself a test; a file
                 the test writes is not one.
symbols          A backticked span in added Markdown, a comment or a Python docstring
                 that names a path, a long flag or a function (`name()`) the tree does
                 not have. A path resolves the way instruction-truth/path-truth.py
                 resolves it, `(absent here)` after the span waives it, and a flag or
                 function resolves when some file of the tree has the word outside a
                 backtick span, so a doc that quotes another doc proves nothing.
easing           A UI file with a cubic-bezier whose y values leave 0..1, a linear()
                 stop that does, a named back, bounce or elastic ease, or
                 `animate-bounce`. "Motion uses exponential ease-out curves, never
                 bounce or elastic" (~/STANDARDS.md, "Frontend design defaults").
contrast         One CSS block that declares a literal color and a literal background
                 (hex, rgb(), oklch(), white, black) under 4.5:1, or 3:1 when the
                 block sets large text. A disabled block is exempt, as it is in WCAG.
                 "Keep body text contrast at 4.5:1 or better, including placeholders."
z-index          `z-index` (or Tailwind `z-[...]`) of three or more nines, or
                 2147483647. "Use a semantic z-index scale, never arbitrary 999/9999."
em-dash          U+2014 in a UI file, except under a `LOCAL FORK` header in its first
                 lines. "Never use the em dash" (global agent instructions); the same
                 rule docs-truth.py applies to Markdown.
reduced-motion   An added animation, transition or keyframes in a UI file when no file
                 of the tree names `prefers-reduced-motion` or a `motion-reduce:` or
                 `motion-safe:` variant. "Honor `prefers-reduced-motion` always."

README.md, "The slop rows", owns what each row leaves out and why. Every exit 2
prints `slop-rules: refused (TYPE): ...`, and TYPE is `tool-error` (git failed).
"""

import argparse
import ast
import bisect
import builtins
import importlib.util
import io
import math
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

sys.dont_write_bytecode = True

NO_BASE = "0" * 40
MAX_FILE = 1_000_000
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv", "__pycache__"}
HERE = Path(__file__).resolve().parent


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def sibling(path):
    """A script of this checkout whose name is not an identifier, imported by file."""
    spec = importlib.util.spec_from_file_location(
        Path(path).stem.replace("-", "_"), HERE / path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- what the change added --------------------------------------------------


def git(root, *args):
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Refused("tool-error", f"git {' '.join(args)}: {err}")
    return proc.stdout.decode(errors="replace")


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def scope(root, base):
    """{path: set of added line numbers}, or {path: None} for every file when there is no base."""
    if base is None:
        found = {}
        for here, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                found[
                    os.path.relpath(os.path.join(here, name), root).replace(os.sep, "/")
                ] = None
        return found
    if base == NO_BASE:
        base = git(root, "hash-object", "-t", "tree", "/dev/null").strip()
        spec = [base, "HEAD"]
    else:
        spec = [f"{base}...HEAD"]
    found, path = {}, None
    for line in git(
        root, "diff", "-U0", "--relative", "--no-renames", "--no-color", *spec
    ).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path:
                found[path] = set()
        elif path and (hunk := HUNK.match(line)):
            start, count = int(hunk[1]), int(hunk[2] or 1)
            found[path].update(range(start, start + count))
    return found


def read(root, rel):
    path = Path(root) / rel
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE:
        return None
    data = path.read_bytes()
    return None if b"\0" in data else data.decode(errors="replace")


def texts(root, change, exts=None, accept=None):
    """(path, text, added) for each changed file that is text and passes the filters."""
    for rel, added in sorted(change.items()):
        if exts is not None and not rel.endswith(exts):
            continue
        if accept is not None and not accept(rel):
            continue
        text = read(root, rel)
        if text is not None:
            yield rel, text, added


def is_added(added, number):
    return added is None or number in added


# --- tests: an assertion on source text -------------------------------------

SRC_EXT = r"(?:py|sh|bash|zsh|js|mjs|cjs|ts|tsx|jsx|rs|go|rb|css|scss)"
SRC_PATH = re.compile(rf"[\w./${{}}@-]*[\w-]\.{SRC_EXT}")
TEST_NAME = re.compile(
    r"(?:^|/)(?:test[_-][^/]*|[^/]*[_.-](?:test|spec)\.\w+)$|(?:^|/)(?:tests?|__tests__)/"
)
FIXTURE_DIR = re.compile(r"(?:^|/)(?:fixtures?|testdata|snapshots?)/")


def is_source(path):
    return (
        bool(SRC_PATH.fullmatch(path))
        and not TEST_NAME.search(path)
        and not FIXTURE_DIR.search(path)
    )


def names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def literal_source(node):
    """The first string constant under node that names a source file."""
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and is_source(n.value)
        ):
            return n.value
    return None


def pathy(node):
    """True for an expression that builds a path, so a command's result is never taken for one."""
    if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)):
        return True
    func = node.func if isinstance(node, ast.Call) else None
    return getattr(func, "attr", getattr(func, "id", None)) in (
        "Path",
        "join",
        "joinpath",
        "resolve",
    )


def python_findings(tree):
    """(line, path) of each assertion over the text of a source file, in the order written."""
    paths, handles, derived = {}, {}, {}
    nodes = list(ast.walk(tree))

    def source_of(node):
        found = literal_source(node)
        if found:
            return found
        return next((paths[n] for n in names_in(node) if n in paths), None) or next(
            (handles[n] for n in names_in(node) if n in handles), None
        )

    def reads(node):
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("read_text", "read_bytes", "read", "readlines")
            ):
                if found := source_of(n.func.value):
                    return found
        return None

    for n in nodes:
        if isinstance(n, ast.Assign):
            if pathy(n.value) and (found := literal_source(n.value)):
                paths.update(
                    {t.id: found for t in n.targets if isinstance(t, ast.Name)}
                )
        if isinstance(n, ast.With):
            for item in n.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "open"
                    and call.args
                ):
                    found = source_of(call.args[0])
                    if found and isinstance(item.optional_vars, ast.Name):
                        handles[item.optional_vars.id] = found
    # Two passes so `hit = re.search(p, text)` follows `text = path.read_text()` whatever the walk order.
    for _ in range(2):
        for n in nodes:
            if isinstance(n, ast.Assign):
                found = reads(n.value) or next(
                    (derived[v] for v in names_in(n.value) if v in derived), None
                )
                if found:
                    derived.update(
                        {t.id: found for t in n.targets if isinstance(t, ast.Name)}
                    )
    out = {}
    for n in nodes:
        if isinstance(n, ast.Assert):
            subject = n
        elif isinstance(n, ast.Call) and getattr(
            n.func, "attr", getattr(n.func, "id", "")
        ).startswith("assert"):
            subject = n
        else:
            continue
        found = reads(subject) or next(
            (derived[v] for v in names_in(subject) if v in derived), None
        )
        if found:
            out.setdefault(n.lineno, found)
    return sorted(out.items())


GREP = re.compile(
    rf"\b(?:e?grep|rg)\b[^|;&\n]*?\s[\"']?(?P<path>[^\s\"'|;&]*[\w-]\.{SRC_EXT})[\"']?\s*(?:$|[|;&)#])"
    rf"|\bcat\s+[\"']?(?P<cat>[^\s\"'|;&]*[\w-]\.{SRC_EXT})[\"']?\s*\|\s*e?grep\b"
)
READ_CALL = re.compile(
    rf"(?:readFileSync|readFile|include_str!|ReadFile)\s*\(?[^)\n]*[\"'`](?P<path>[^\"'`\n]*[\w-]\.{SRC_EXT})[\"'`]"
)
ASSERTING = re.compile(
    r"\b(?:expect|assert\w*|t\.(?:Error|Fatal)\w*)\b|assert!|assert_eq!"
)


def tests(root, change):
    found = []
    for rel, text, added in texts(root, change, accept=lambda r: TEST_NAME.search(r)):
        lines = text.splitlines()
        if rel.endswith(".py"):
            try:
                hits = python_findings(ast.parse(text))
            except SyntaxError:
                continue
            found += [(rel, n, path) for n, path in hits if is_added(added, n)]
        elif rel.endswith((".sh", ".bash")):
            for n, line in enumerate(lines, 1):
                hit = GREP.search(line)
                path = hit and (hit["path"] or hit["cat"])
                if not path or not is_source(path) or not is_added(added, n):
                    continue
                # A file the test writes itself is a fixture, whatever its name.
                name = re.escape(path.rsplit("/", 1)[-1])
                if re.search(rf">\s*[\"']?[^\s\"']*{name}|\btee\s+\S*{name}", text):
                    continue
                found.append((rel, n, path))
        elif ASSERTING.search(text):
            for n, line in enumerate(lines, 1):
                hit = READ_CALL.search(line)
                if hit and is_source(hit["path"]) and is_added(added, n):
                    found.append((rel, n, hit["path"]))
    return (
        [
            f"{rel}:{n}: asserts on the text of {path}; assert what it does through its entry point"
            for rel, n, path in found
        ],
        "no new test asserts on a source file's text",
    )


# --- symbols: a backticked name the tree does not have ----------------------

SPAN = re.compile(r"`([^`\n]+)`(\s*\(absent[^)]*\))?")
FLAG = re.compile(r"--[A-Za-z][\w-]*")
FUNCTION = re.compile(r"([A-Za-z_]\w*)\(\)")
WORD = re.compile(r"[A-Za-z_]\w*")
# What a full-line comment starts with, by file extension; a trailing comment after code is not read.
COMMENT_HEADS = {
    ("#",): (".sh", ".bash", ".zsh", ".toml", ".yml", ".yaml", ".rb"),
    ("//", "/*", "*"): (
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cpp",
    ),
    ("/*", "*"): (".css", ".scss"),
}


def fenced_split(lines):
    """(prose line numbers, fenced line numbers) of a Markdown file."""
    prose, fenced, inside = [], [], False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            inside = not inside
        elif inside:
            fenced.append(n)
        else:
            prose.append(n)
    return prose, fenced


def python_comment_lines(text):
    """Line numbers of a Python file's comments and docstrings."""
    found = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                found.add(tok.start[0])
        for n in ast.walk(ast.parse(text)):
            if isinstance(
                n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = n.body[0] if n.body else None
                if (
                    isinstance(doc, ast.Expr)
                    and isinstance(doc.value, ast.Constant)
                    and isinstance(doc.value.value, str)
                ):
                    found.update(range(doc.lineno, doc.end_lineno + 1))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return found


def prose_lines(rel, text):
    """Line numbers where a backticked span is a doc's or a comment's claim, not code."""
    lines = text.splitlines()
    if rel.endswith(".md"):
        return fenced_split(lines)[0]
    if rel.endswith(".py"):
        return sorted(python_comment_lines(text))
    for heads, exts in COMMENT_HEADS.items():
        if rel.endswith(exts):
            return [
                n for n, line in enumerate(lines, 1) if line.lstrip().startswith(heads)
            ]
    return []


# The lookbehind keeps a long token with no slash, such as an encoded fixture, from being retried at every offset.
PATH_WORD = re.compile(r"(?<![\w.@/-])[\w.@-]+(?:/[\w.@-]+)+")


def vocabulary(root):
    """(words, long flags, path-shaped words) the tree has outside backtick spans.

    A Markdown file offers its fenced blocks only. A path that a script spells out
    (`REGISTRY = ".github/gates.toml"`) is a path the tree names, though it holds no
    such file here: it is the file of the repositories that call this one.
    """
    words, flags, paths = set(), set(), set()
    for rel in scope(root, None):
        text = read(root, rel)
        if text is None:
            continue
        if rel.endswith(".md"):
            lines = text.splitlines()
            text = "\n".join(lines[n - 1] for n in fenced_split(lines)[1])
        text = SPAN.sub(" ", text)
        words.update(WORD.findall(text))
        flags.update(FLAG.findall(text))
        paths.update(PATH_WORD.findall(text))
    return words, flags, paths


def symbols(root, change):
    path_truth = sibling("instruction-truth/path-truth.py")
    suffixes = path_truth.walk(root)[1]
    # A path is judged only when it starts in a directory of this tree: one that starts
    # elsewhere (`internal/git/git.go`, `repos/owner/name`) names another repository's file
    # or a URL, and a bare name cannot say which tree it belongs to.
    top = {
        d
        for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d)) and d not in SKIP_DIRS
    }
    vocab = []
    found = []

    def has(kind, word):
        if not vocab:
            vocab.append(vocabulary(root))
        return word in vocab[0][kind]

    for rel, text, added in texts(root, change):
        lines = text.splitlines()
        for n in (n for n in prose_lines(rel, text) if is_added(added, n)):
            for span, absent in SPAN.findall(lines[n - 1]):
                span = span.strip()
                target = path_truth.candidate(span)
                flag = span.split("=", 1)[0]
                call = FUNCTION.fullmatch(span)
                if absent:
                    continue
                if target is not None:
                    here = os.path.join(root, os.path.dirname(rel), target)
                    if (
                        target not in suffixes
                        and not os.path.exists(here)
                        and "/" in target
                        and target.split("/")[0] in top
                        and not has(2, target)
                    ):
                        found.append(
                            f"{rel}:{n}: `{span}` names a path that is not in the tree; fix it or mark it `(absent here)`"
                        )
                elif FLAG.fullmatch(flag) and not has(1, flag):
                    found.append(
                        f"{rel}:{n}: `{span}` names a flag that no file of the tree has outside a backtick span"
                    )
                elif call and call[1] not in dir(builtins) and not has(0, call[1]):
                    found.append(
                        f"{rel}:{n}: `{span}` names a function that no file of the tree has outside a backtick span"
                    )
    return (
        found,
        "every backticked path, flag and function in the added prose is in the tree",
    )


# --- UI rules from ~/STANDARDS.md, "Frontend design defaults" ---------------

MARKUP_EXT = (
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
    ".htm",
    ".jsx",
    ".tsx",
    ".vue",
    ".svelte",
    ".astro",
)
SCRIPT_EXT = (".js", ".mjs", ".ts")


def lines_matching(root, change, exts, pattern, describe):
    out = []
    for rel, text, added in texts(root, change, exts):
        for n, line in enumerate(text.splitlines(), 1):
            if is_added(added, n):
                out += [
                    f"{rel}:{n}: {m}"
                    for hit in pattern.finditer(line)
                    if (m := describe(hit))
                ]
    return out


CUBIC = re.compile(
    r"cubic-bezier\(\s*([-+.\d]+)\s*,\s*([-+.\d]+)\s*,\s*([-+.\d]+)\s*,\s*([-+.\d]+)\s*\)",
    re.I,
)
LINEAR = re.compile(r"\blinear\(([^)]*)\)")
NAMED = re.compile(
    r"\bease-?(?:in-?out|in|out)?-?(?:back|bounce|elastic)\b|\banimate-bounce\b", re.I
)


def number(text):
    try:
        return float(text)
    except ValueError:
        return None


def overshoot(hit):
    if hit.re is CUBIC:
        ys = [number(hit[2]), number(hit[4])]
        if any(y is None or not 0 <= y <= 1 for y in ys):
            return f"{hit[0]} overshoots 0..1 on y, which is a bounce or elastic curve; use an exponential ease-out"
    elif hit.re is LINEAR:
        stops = [number(s.split()[0]) for s in hit[1].split(",") if s.split()]
        if any(s is not None and not 0 <= s <= 1 for s in stops):
            return f"{hit[0]} has a stop outside 0..1, which is an overshoot; use an exponential ease-out"
    else:
        return (
            f"{hit[0]} is a bounce, elastic or back ease; use an exponential ease-out"
        )
    return None


def easing(root, change):
    found = []
    for pattern in (CUBIC, LINEAR, NAMED):
        found += lines_matching(
            root, change, MARKUP_EXT + SCRIPT_EXT, pattern, overshoot
        )
    return sorted(found), "no added easing overshoots"


ZINDEX = re.compile(
    r"""z-?index["']?\s*[:=]\s*["']?(?:9{3,}|2147483647)\b|\bz-\[(?:9{3,}|2147483647)\]""",
    re.I,
)


def z_index(root, change):
    found = lines_matching(
        root,
        change,
        MARKUP_EXT + SCRIPT_EXT,
        ZINDEX,
        lambda h: f"{h[0].strip()} is an arbitrary z-index; use the semantic scale",
    )
    return found, "no added z-index is arbitrary"


def em_dash(root, change):
    docs = sibling("docs-truth.py")
    found = []
    for rel, text, added in texts(root, change, MARKUP_EXT):
        lines = text.splitlines()
        if any(
            docs.FORK_MARKER in line or "/* LOCAL FORK" in line
            for line in lines[: docs.FORK_LINES]
        ):
            continue
        found += [
            f"{rel}:{n}: carries an em dash; use a hyphen or reword"
            for n, line in enumerate(lines, 1)
            if "\u2014" in line and is_added(added, n)
        ]
    return found, "no added UI line carries an em dash"


MOTION = re.compile(
    r"(?<![\w-])(?:animation|animation-name|transition|transition-property)\s*:\s*(?!\s|none\b|0m?s\b)|@keyframes\b"
)
HONOURED = re.compile(r"prefers-reduced-motion|\bmotion-(?:reduce|safe):")


def reduced_motion(root, change):
    first = next(
        (
            f"{rel}:{n}: adds motion and no file of the tree names prefers-reduced-motion; honor it always"
            for rel, text, added in texts(root, change, MARKUP_EXT)
            for n, line in enumerate(text.splitlines(), 1)
            if is_added(added, n) and MOTION.search(line)
        ),
        None,
    )
    if first is None:
        return [], "no motion was added"
    for rel, text, _ in texts(root, scope(root, None), MARKUP_EXT + SCRIPT_EXT):
        if HONOURED.search(text):
            return [], f"motion was added and {rel} honors prefers-reduced-motion"
    return [first], ""


def luminance(rgb):
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def oklch_luminance(light, chroma, hue):
    """Relative luminance of an OKLCH color: OKLab to linear sRGB (Ottosson's published matrices), clamped to the gamut."""
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    lc = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = [
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    ]
    r, g, bl = (min(1.0, max(0.0, c)) for c in lin)
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


HEX = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{6})")
RGB = re.compile(r"rgb\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})\s*\)")
OKLCH = re.compile(r"oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?\s*\)")


def color_luminance(value):
    """Relative luminance of a single opaque literal color, or None when the value is anything else."""
    value = value.lower().replace("!important", "").strip()
    if value in ("white", "black"):
        return 1.0 if value == "white" else 0.0
    if hit := HEX.fullmatch(value):
        digits = "".join(c * 2 for c in hit[1]) if len(hit[1]) == 3 else hit[1]
        return luminance([int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)])
    if hit := RGB.fullmatch(value):
        return luminance([min(255, int(c)) / 255 for c in hit.groups()])
    if hit := OKLCH.fullmatch(value):
        light = float(hit[1]) / (100 if hit[2] else 1)
        return oklch_luminance(light, float(hit[3]), float(hit[4]))
    return None


# Anchored at the brace, so a megabyte of HTML with none is one linear pass; the selector is read back from it.
BLOCK = re.compile(r"\{([^{}]*)\}")
DISABLED = re.compile(r":disabled|\[disabled|\[aria-disabled|\.disabled\b", re.I)


def css_declarations(body):
    for part in re.finditer(r"([\w-]+)\s*:\s*([^;]+)", body):
        yield part[1].lower(), part[2].strip(), part.start()


def large_text(decls):
    size = decls.get("font-size", ("", 0))[0]
    hit = re.fullmatch(r"([\d.]+)(px|rem|em)", size.lower())
    if not hit:
        return False
    px = float(hit[1]) * (1 if hit[2] == "px" else 16)
    weight = decls.get("font-weight", ("", 0))[0].lower()
    return px >= 24 or (
        px >= 18.66 and (weight == "bold" or weight.isdigit() and int(weight) >= 700)
    )


def contrast(root, change):
    found = []
    for rel, text, added in texts(root, change, MARKUP_EXT):
        newlines = [m.start() for m in re.finditer("\n", text)]
        for block in BLOCK.finditer(text):
            selector = re.split(
                r"[{}]", text[max(0, block.start() - 300) : block.start()]
            )[-1]
            if DISABLED.search(selector):
                continue
            decls = {}
            for name, value, at in css_declarations(block[1]):
                line = bisect.bisect_left(newlines, block.start(1) + at) + 1
                decls[name] = (value, line)
            fore, back = (
                decls.get("color"),
                decls.get("background-color") or decls.get("background"),
            )
            if not fore or not back:
                continue
            lums = [color_luminance(fore[0]), color_luminance(back[0])]
            if None in lums or not (
                is_added(added, fore[1]) or is_added(added, back[1])
            ):
                continue
            ratio = (max(lums) + 0.05) / (min(lums) + 0.05)
            need = 3.0 if large_text(decls) else 4.5
            if ratio < need:
                found.append(
                    f"{rel}:{fore[1]}: color {fore[0]} on {back[0]} is {ratio:.1f}:1, under {need}:1"
                )
    return found, "no added color and background pair is under its contrast floor"


ROWS = {
    "tests": tests,
    "symbols": symbols,
    "easing": easing,
    "contrast": contrast,
    "z-index": z_index,
    "em-dash": em_dash,
    "reduced-motion": reduced_motion,
}


def main():
    ap = argparse.ArgumentParser(usage="slop-rules.py ROW [--base B] [root]")
    ap.add_argument("row", choices=ROWS)
    ap.add_argument("--base")
    ap.add_argument("root", nargs="?", default=".")
    # Plain parse_args consumes the optional root early on Python before 3.12.7 and rejects it after --base.
    args = ap.parse_intermixed_args()
    root = Path(args.root).resolve()
    try:
        base = None
        if args.base:
            base = args.base
            if base != NO_BASE:
                base = git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        found, clean = ROWS[args.row](root, scope(root, base))
    except Refused as e:
        print(f"slop-rules: refused ({e.kind}): {e}")
        return 2
    for line in found:
        print(f"slop-rules: {args.row}: {line}")
    if found:
        print(f"slop-rules: {len(found)} finding(s)")
        return 1
    print(f"slop-rules: {clean}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
