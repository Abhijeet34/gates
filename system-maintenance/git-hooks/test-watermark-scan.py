#!/usr/bin/env python3
"""Prove the no-watermarking gate, rather than asserting it.

    system-maintenance/git-hooks/test-watermark-scan.py

Offline, and every fixture is built here rather than committed: a PNG carrying a
C2PA manifest is exactly what this repository's own push gate refuses, so
checking one in would make the repository unpushable by its own rule.

The load-bearing case is the LAST one. A rule that no fixture exercises
certifies what it never measured - this repository's dominant defect, seven
instances on record - so RULES is compared against the fixture table, and every
fixture is re-graded once NEUTRALISED. Deleting a fixture fails the comparison;
weakening a rule until its fixture stops firing fails the assertion; and a
"rule" that fires on the neutralised input too is caught by the third leg, which
is what distinguishes a working detector from one that flags everything.
"""

import base64
import gzip
import hashlib
import importlib.util
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SCANNER = os.path.join(HERE, "watermark-scan.py")
ALLOWLIST = os.path.join(HERE, "watermark-allow.conf")
OPTOUT = os.path.join(HERE, "watermark-optout.conf")

_spec = importlib.util.spec_from_file_location("watermark_scan", SCANNER)
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)

failed = []
ran = 0
# Every CLI transcript this suite produces. The rule-coverage check at the end
# reads it, so a rule is covered by having been OBSERVED firing and never by
# being named in a table beside it.
TRANSCRIPT = []


def check(desc, ok):
    global ran
    ran += 1
    if not ok:
        failed.append(desc)
        print("\033[31mFAIL\033[0m %s" % desc)


def rules_fired(findings):
    return {f[0] for f in findings}


# --- fixtures, one per rule id -------------------------------------------------
#
# (rule id) -> (fires, neutralised). Neither string is written with a literal
# invisible character: this file is itself pushed through the gate it tests, and
# a fixture pasted in as a literal would be a finding in its own source.
CP = {
    "text.zero-width": 0x200B,
    "text.bidi": 0x202E,
    "text.invisible-op": 0x2060,
    "text.soft-hyphen": 0x00AD,
    "text.mongolian-blank": 0x180E,
    "text.hangul-blank": 0x3164,
    "text.variation-selector": 0xFE0F,
    "text.zwnbsp": 0xFEFF,
    "text.reserved-ignorable": 0xFFF0,
    "text.noncharacter": 0xFDD0,
    "text.unicode-tag": 0xE0001,
}
TEXT_FIXTURES = {
    rule: ("before" + chr(cp) + "after", "before after") for rule, cp in CP.items()
}


def png(chunks=()):
    """A 1x1 PNG, plus whatever extra chunks the caller wants inside it."""

    def chunk(kind, payload):
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + b"".join(chunk(k, p) for k, p in chunks)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def text_chunk(key, value):
    return (b"tEXt", key + b"\x00" + value)


def pdf(info=None, xmp=None, broken_xref=False):
    """A minimal one-page PDF, optionally carrying an Info dict and an XMP packet.

    Built here rather than committed for the same reason the PNGs are: a fixture
    that fires this gate is a file this repository could not push. The two
    metadata homes are separate arguments because they are separate reads - the
    document-information dictionary and the XMP packet - and the gate has to see
    both.

    `broken_xref` points every entry at the same wrong offset, which is the
    audited shape: exiftool reads nothing and exits 0, a viewer reconstructs the
    table on open and reads everything.
    """
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>",
    ]
    trailer_extra = ""
    if info:
        objs.append("<< %s >>" % " ".join("/%s (%s)" % kv for kv in info.items()))
        trailer_extra = " /Info %d 0 R" % len(objs)
    if xmp:
        packet = (
            '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
            'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" '
            'xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            + xmp
            + '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
        )
        objs.append(
            "<< /Type /Metadata /Subtype /XML /Length %d >>\nstream\n%s\nendstream"
            % (len(packet), packet)
        )
        objs[0] = "<< /Type /Catalog /Pages 2 0 R /Metadata %d 0 R >>" % len(objs)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % (9 if broken_xref else off)).encode()
    out += (
        "trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, trailer_extra, 99999 if broken_xref else xref)
    ).encode()
    return bytes(out)


# A JUMBF box is what a C2PA manifest travels in; a PNG carries it in `caBX`.
# The bytes below are not a valid signed manifest and do not need to be - the
# gate is a detector, and what it detects is the manifest's presence.
def jumbf(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def c2pa_box(body):
    """A WELL-FORMED JUMBF superbox carrying `body`, in a PNG `caBX` chunk.

    Well-formed matters now that a parse failure is itself a finding: the
    hand-rolled box this replaces declared a 24-byte description box and gave 16,
    so exiftool answered `Truncated JUMD directory` and every one of these
    fixtures fired binary.unreadable as well as the rule it was written for -
    including the NEUTRALISED members, which is a fixture measuring the wrong
    thing rather than a detector working.

    `bidb` is JUMBF's binary-data box, chosen because exiftool parses it without
    complaint and it carries arbitrary bytes; `jumd` is the description box, a
    16-byte content-type UUID and one toggle byte.
    """
    jumd = jumbf(b"jumd", b"jp2c\x00\x11\x00\x10\x80\x00\x00\xaa\x008\x9bq\x00")
    return (b"caBX", jumbf(b"jumb", jumd + jumbf(b"bidb", body)))


BINARY_FIXTURES = {
    "binary.pixel-watermark-known": (
        png([text_chunk(b"Software", b"Google Imagen 4")]),
        png([text_chunk(b"Software", b"Preview.app")]),
    ),
    "binary.c2pa-manifest": (
        png(
            [c2pa_box(b"urn:c2pa:b19ff451-3830-467d-9ca2-49138540de96c2pa.assertions")]
        ),
        png(),
    ),
    "binary.watermark-assertion": (
        png([c2pa_box(b"c2pa.created c2pa.watermarked.unbound")]),
        png([c2pa_box(b"c2pa.created c2pa.opened")]),
    ),
    "binary.digital-source-type": (
        png([c2pa_box(b"digitalSourceType trainedAlgorithmicMedia")]),
        png([c2pa_box(b"digitalSourceType digitalCapture")]),
    ),
    "binary.generator-tool": (
        png([text_chunk(b"Software", b"Midjourney v6")]),
        # NOT a finding, and that is the whole attributive/detective line: a
        # human's image editor recorded in the same field is provenance for a
        # reader, not a machine-detection signal.
        png([text_chunk(b"Software", b"Preview.app")]),
    ),
}

# A PDF records its producing tool in the document-information dictionary or in
# an XMP packet, and neither was ever examined: the gate matched `%PDF-` and
# `.pdf`, handed the file to exiftool, and then read no field a PDF actually
# uses, so a Producer tag passed as `clean`. The fixture is the measured case -
# a real banner PDF carried `Producer: appifact kit`.
PDF_FIXTURES = {
    "binary.tool-tag": (pdf({"Producer": "appifact kit"}), pdf()),
}

# The evasion that cost one byte: `is_text` returned None on any UTF-8 decode
# failure, so the blob went to the byte-marker scan, which knows no codepoints,
# and the file reported `clean`. Both members are prose carrying nothing
# invisible - what separates them is the trailing byte alone, so a fixture that
# fires only because of its zero-width space would not measure this rule.
ENCODING_FIXTURES = {
    "encoding.undecodable": (b"plain prose\n" + b"\xff", b"plain prose\n"),
}

# A PDF whose xref table is wrong: exiftool warns, reads no Producer and exits
# 0, so the file used to report `clean` - while qpdf reconstructing that xref,
# which is what every viewer does on open, recovers `Producer: Midjourney v6`.
# The neutralised member is the SAME document with a correct xref, so what the
# pair measures is the parse failure and not the tag.
UNREADABLE_FIXTURES = {
    "binary.unreadable": (
        pdf({"Producer": "Midjourney v6"}, broken_xref=True),
        pdf({"Producer": "appifact kit"}),
    ),
}

# --- 1. every rule has a fixture ----------------------------------------------

# The comparison itself runs LAST, in section ZZ, because half the coverage now
# comes from what the CLI cases actually produced rather than from a table - a
# declared name is the "certifies what it never measured" defect this check
# exists to catch, one level up.
TABLE_COVERED = (
    set(TEXT_FIXTURES)
    | set(BINARY_FIXTURES)
    | set(PDF_FIXTURES)
    | set(ENCODING_FIXTURES)
    | set(UNREADABLE_FIXTURES)
)

# --- 2. text rules fire, and stop firing once the fixture is neutralised -------

for rule, (fires, clean) in sorted(TEXT_FIXTURES.items()):
    check("%s fires on its fixture" % rule, rule in rules_fired(ws.scan_text(fires)))
    check(
        "%s does not fire on the neutralised fixture" % rule, ws.scan_text(clean) == []
    )

# --- 3. the three legitimate cases, each with zero findings and no allowlist ---
#
# These are CONTEXTS rather than paths, because a path list cannot cover a file
# nobody has written yet and the brief's own instruction was not to touch the
# three files. Each string below reproduces a base+selector pair measured in the
# fleet on 2026-08-31.
EMOJI_BASES = [0x2139, 0x23ED, 0x23F8, 0x26A0, 0x2714, 0x1F5C2]
for base in EMOJI_BASES:
    for vs in (0xFE0E, 0xFE0F):
        s = "status " + chr(base) + chr(vs) + " ok"
        check(
            "U+%04X after U+%04X is an emoji presentation selector" % (vs, base),
            ws.scan_text(s) == [],
        )
# 👩 ZWJ 👩 - an emoji ZWJ sequence, as measured in a Go test string
check(
    "ZWJ inside an emoji sequence is not a finding",
    ws.scan_text(chr(0x1F469) + chr(0x200D) + chr(0x1F469)) == [],
)
# Persian ZWNJ between two Arabic-script letters, from the same file
check(
    "ZWNJ between Arabic-script letters is not a finding",
    ws.scan_text(chr(0x06CC) + chr(0x200C) + chr(0x0645)) == [],
)
# The same selectors where there is no emoji base ARE findings: the exemption is
# about context, and it must not become a blanket pass for the codepoint.
check(
    "a variation selector after a letter is still a finding",
    ws.scan_text("a" + chr(0xFE0F) + "b") != [],
)
check(
    "ZWJ inside English prose is still a finding",
    ws.scan_text("the" + chr(0x200D) + "word") != [],
)
check(
    "ZWNJ inside English prose is still a finding",
    ws.scan_text("the" + chr(0x200C) + "word") != [],
)

# The measured occurrences in the live fleet, VENDORED. Each snippet is the
# content of a real line, rebuilt from codepoints so this file carries no
# literal invisible character of its own - it is pushed through the gate it
# tests. The label says what kind of line it was measured in (2026-08-31) and
# the `carries` column is what makes the case a case, asserted present before
# the scan so a snippet that lost its codepoint cannot pass by being ordinary
# text.
ZWJ, ZWNJ, VS15, VS16 = chr(0x200D), chr(0x200C), chr(0xFE0E), chr(0xFE0F)

VENDORED = [
    # A heavy check mark asking for TEXT presentation: typography, not a mark.
    (
        "a Markdown table row quoting Homebrew output",
        "| 12 | `brew fetch --formula jq` | `" + chr(0x2714) + VS15 + " Bottle jq`",
        (0x2714, 0xFE0E),
    ),
    # The same pair inside an awk pattern matching Homebrew's own output.
    (
        "an awk pattern matching Homebrew output",
        "else if ($0 ~ /^("
        + chr(0x2714)
        + VS15
        + " (Bottle|JSON API)|"
        + chr(0x1F37A)
        + " )/)",
        (0x2714, 0xFE0E),
    ),
    # ZWJ joining two emoji into one glyph - the only way to write it.
    (
        "a Go test string with an emoji ZWJ sequence",
        '"support ' + chr(0x1F469) + ZWJ + chr(0x1F4BB) + ' workflows"',
        (0x200D,),
    ),
    # ZWNJ between Arabic-script letters: Persian orthography requires it.
    (
        "a Go test string in Persian",
        '"fix '
        + chr(0x0645)
        + chr(0x06CC)
        + ZWNJ
        + chr(0x0631)
        + chr(0x0648)
        + chr(0x062F)
        + ' rendering"',
        (0x200C,),
    ),
    # Emoji presentation selectors after pictographic bases in status output.
    (
        "Go status strings with emoji presentation selectors",
        chr(0x23F8)
        + VS16
        + " awaiting approval / "
        + chr(0x23ED)
        + VS16
        + " skipped / return "
        + chr(0x26A0)
        + VS16
        + " / return "
        + chr(0x2139)
        + VS16,
        (0x23F8, 0x23ED, 0x26A0, 0x2139, 0xFE0F),
    ),
    (
        "a Go test string holding a Markdown PR summary",
        "## Risk Assessment " + chr(0x26A0) + VS16 + " Medium: touches critical error "
        "handling / - "
        + chr(0x1F527)
        + " **Test** - 1 issue found "
        + chr(0x2192)
        + " auto-fixed "
        + chr(0x2705),
        (0x26A0, 0xFE0F),
    ),
    # A card-index-dividers favicon inlined as an SVG data URI.
    (
        "an inline SVG favicon in a JavaScript test",
        "<text>" + chr(0x1F5C2) + VS16 + "</text>",
        (0x1F5C2, 0xFE0F),
    ),
]

for where, snippet, carries in VENDORED:
    missing = ["U+%04X" % cp for cp in carries if chr(cp) not in snippet]
    check("%s: vendored snippet still carries %s" % (where, missing), not missing)
    hits = ws.scan_text(snippet)
    check("%s is not a finding (got %s)" % (where, hits[:3]), hits == [])
    # Printed on pass, so the transcript names the case that ran. The line this
    # replaces said "NOT MEASURED" and still let the suite report success.
    print(
        "measured: %s carrying %s -> %d finding(s)"
        % (where, " ".join("U+%04X" % cp for cp in carries), len(hits))
    )

# --- 4. end to end through the CLI, against a real fixture repository ----------


EMPTY_OPTOUT = None  # assigned once TMP exists


def run(
    repo,
    allow=ALLOWLIST,
    identity="github.com/Abhijeet34/fixture",
    env=None,
    remote="nosuchremote",
    optout=None,
):
    head = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    stdin = "refs/heads/main %s refs/heads/main %s\n" % (head, "0" * 40)
    e = dict(
        os.environ,
        WATERMARK_IDENTITY=identity,
        WATERMARK_ALLOW=allow,
        WATERMARK_OPTOUT=optout or EMPTY_OPTOUT,
    )
    e.update(env or {})
    p = subprocess.run(
        [sys.executable, SCANNER, "push", remote],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=repo,
        env=e,
    )
    TRANSCRIPT.append(p.stdout + p.stderr)
    return p.returncode, p.stdout + p.stderr


def verb(argv, cwd=None, env=None):
    e = dict(os.environ, WATERMARK_ALLOW=ALLOWLIST)
    e.update(env or {})
    p = subprocess.run(
        [sys.executable, SCANNER] + argv, capture_output=True, text=True, cwd=cwd, env=e
    )
    TRANSCRIPT.append(p.stdout + p.stderr)
    return p.returncode, p.stdout + p.stderr


def fixture_repo(tmp, files):
    repo = tempfile.mkdtemp(dir=tmp)
    subprocess.run(["git", "init", "--quiet", "-b", "main", repo], check=True)
    for name, data in files.items():
        path = os.path.join(repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=env)
    subprocess.run(
        [
            "git",
            "-C",
            repo,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@invalid",
            "commit",
            "--quiet",
            "-m",
            "f",
        ],
        check=True,
        env=env,
    )
    return repo


TMP = tempfile.mkdtemp(prefix="test-watermark-scan.")
EMPTY_OPTOUT = os.path.join(TMP, "no-optout")
open(EMPTY_OPTOUT, "w").close()

# The C2PA fixtures must be structurally VALID, or every one of them fires the
# parse-failure rule as well as the rule it was written for and the pair stops
# measuring anything. Asserted rather than assumed: this broke silently once.
_probe = os.path.join(TMP, "jumbf-probe.png")
with open(_probe, "wb") as fh:
    fh.write(png([c2pa_box(b"c2pa.created c2pa.opened")]))
check(
    "the JUMBF fixture is well-formed, so the C2PA pairs measure the C2PA rules",
    "binary.unreadable" not in rules_fired(ws.inspect_paths([_probe])[0][_probe]),
)

for rule, (fires, clean) in sorted(BINARY_FIXTURES.items()):
    repo = fixture_repo(TMP, {"asset.png": fires})
    rc, out = run(repo)
    check("%s: the CLI refuses the fixture" % rule, rc == 1 and rule in out)
    repo = fixture_repo(TMP, {"asset.png": clean})
    rc, out = run(repo)
    # The rule under test, not the exit code: two of these neutralised members
    # share a real JUMBF carrier with their firing twin, and a JUMBF box IS a
    # manifest, so they legitimately still trip binary.c2pa-manifest. Asserting
    # rc==0 here passed only while the fixture was malformed enough that
    # exiftool could not see the box at all.
    check(
        "%s: the CLI does not fire on the neutralised fixture (%s)"
        % (rule, out.strip()[-90:]),
        rule not in out,
    )

# What that exit code was standing in for, asserted where it is actually true:
# an unmarked asset passes. Without this the loop above could be satisfied by a
# detector that refuses everything.
repo = fixture_repo(TMP, {"asset.png": png()})
rc, out = run(repo)
check(
    "a PNG with no manifest and no generator tag passes (%s)" % out.strip()[-90:],
    rc == 0 and "No detective provenance found" in out,
)

# A generative pipeline's PNG text chunk is a finding on its PRESENCE, with no
# name to match - which is what covers a generator this file has never heard of.
repo = fixture_repo(
    TMP, {"a.png": png([text_chunk(b"parameters", b"a photo of a cat, steps: 30")])}
)
rc, out = run(repo)
check(
    "a `parameters` PNG chunk is a finding on presence alone",
    rc == 1 and "binary.generator-tool" in out,
)

# The tier distinction this rule exists for, asserted directly rather than left
# to the fixture pair: a generator that is merely NAMED is clearable, and one
# known to mark the pixels is not. Getting these the same way round is the
# difference between a refusal and a false pass.
repo = fixture_repo(TMP, {"a.png": png([text_chunk(b"Software", b"Midjourney v6")])})
rc, out = run(repo)
check(
    "a named generator that does not mark pixels stays clearable",
    rc == 1
    and "binary.generator-tool" in out
    and "binary.pixel-watermark-known" not in out,
)

# --- 4d. containers ------------------------------------------------------------
#
# All four of these reported `clean`. Deflate destroys the byte markers,
# exiftool reads only the outer file, and a text blob is never byte-scanned - so
# a marked asset one level down was invisible to every check. A STORED zip WAS
# caught, which is what made the hole look smaller than it is.
MARKED_PNG = png([text_chunk(b"Software", b"Midjourney v6")])
DST_PNG = png([c2pa_box(b"digitalSourceType trainedAlgorithmicMedia")])


def docx(members, compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


deflated = docx({"word/media/image1.png": MARKED_PNG})
check(
    "deflate really does hide the marker, so this fixture measures the recursion",
    b"Midjourney" not in deflated,
)
repo = fixture_repo(TMP, {"report.docx": deflated})
rc, out = run(repo)
check(
    "a deflated docx holding a marked PNG is refused (%s)" % out.strip()[-80:],
    rc == 1 and "binary.generator-tool" in out,
)
check("and the finding names the member", "word/media/image1.png" in out)

repo = fixture_repo(TMP, {"doc.odt": docx({"Pictures/a.png": DST_PNG})})
rc, out = run(repo)
check(
    "a zip-family container holding a trainedAlgorithmicMedia asset is refused (%s)"
    % out.strip()[-80:],
    rc == 1 and "binary.digital-source-type" in out,
)

# A PDF that embeds a JPEG as a /DCTDecode XObject keeps the JPEG bytes raw, so
# the marked EXIF is right there and exiftool reads only the container.
jpeg = subprocess.run(
    ["exiftool", "-o", "-", "-Software=Midjourney v6", "-"],
    input=png(),
    capture_output=True,
)
pdf_embed = pdf({"Title": "report"})
pdf_embed = pdf_embed.replace(b"%PDF-1.4\n", b"%PDF-1.4\n%% embedded\n", 1)
# Build the XObject by hand rather than through exiftool, so the fixture does
# not depend on which formats the installed exiftool can WRITE.
raw = MARKED_PNG
stream = (
    b"9 0 obj\n<< /Type /XObject /Subtype /Image /Filter /FlateDecode /Length %d >>\n"
    b"stream\n"
    % len(zlib.compress(raw))
    + zlib.compress(raw)
    + b"\nendstream\nendobj\n"
)
repo = fixture_repo(TMP, {"doc.pdf": pdf_embed[:-1] + stream})
rc, out = run(repo)
check(
    "a PDF stream holding a marked image is refused (%s)" % out.strip()[-80:],
    rc == 1 and "binary.generator-tool" in out and "stream@" in out,
)

# The same asset as a base64 data: URI in a text file, which no check reached
# because byte markers deliberately skip text.
uri = '<img src="data:image/png;base64,%s">\n' % base64.b64encode(DST_PNG).decode()
repo = fixture_repo(TMP, {"page.html": uri.encode()})
rc, out = run(repo)
check(
    "a base64 data: URI carrying a marked asset is refused (%s)" % out.strip()[-80:],
    rc == 1 and "base64@" in out,
)

# The URI prefix was doing no work as a filter and real work as an evasion: a
# bundler writes the same asset as two quoted halves joined by `+`, and a JSON
# field carries it with no prefix at all. Both reported clean.
half = len(base64.b64encode(DST_PNG).decode()) // 2
b64 = base64.b64encode(DST_PNG).decode()
repo = fixture_repo(
    TMP,
    {
        "bundle.js": (
            'const s = "data:image/png;base64,%s" +\n  "%s";\n'
            % (b64[:half], b64[half:])
        ).encode()
    },
)
rc, out = run(repo)
check(
    "a data: URI split by string concatenation is refused (%s)" % out.strip()[-80:],
    rc == 1 and "base64@" in out,
)
repo = fixture_repo(TMP, {"icons.json": ('{"icon": "%s"}\n' % b64).encode()})
rc, out = run(repo)
check(
    "bare base64 with no data: prefix at all is refused (%s)" % out.strip()[-80:],
    rc == 1 and "base64@" in out,
)
# And ordinary prose must not become a candidate. This is the failure mode a
# first attempt actually produced: stripping whitespace globally turned English
# into one enormous alphanumeric run and reported 932 findings in this
# repository where 8 are real.
prose = (
    "The measurement is the point, and a paragraph of ordinary English prose "
    "with long words like internationalization and counterrevolutionary must "
    "never decode as an asset however many of them are written in a row. "
) * 6
repo = fixture_repo(TMP, {"notes.md": prose.encode()})
rc, out = run(repo)
check(
    "ordinary prose is not mistaken for a base64 asset (%s)" % out.strip()[-80:],
    rc == 0 and "base64@" not in out,
)

# A gzipped asset: not a zip, not a PDF, not text, and deflate destroys the
# signature the carve looks for.
repo = fixture_repo(TMP, {"asset.png.gz": gzip.compress(MARKED_PNG)})
rc, out = run(repo)
check(
    "a gzipped marked asset is refused (%s)" % out.strip()[-80:],
    rc == 1 and "gzip" in out,
)

# A container nested PAST the cap must refuse rather than pass. Raising the cap
# only moves the hiding place; saying "this gate did not open it" closes it.
deep = docx({"l1.zip": docx({"l2.zip": docx({"a.png": MARKED_PNG})})})
repo = fixture_repo(TMP, {"deep.zip": deep})
rc, out = run(repo)
check(
    "a container nested past the depth cap is refused, not reported clean (%s)"
    % out.strip()[-80:],
    rc == 1 and "container.unexamined" in out,
)
# And a container within the cap is opened rather than refused on the bound.
repo = fixture_repo(TMP, {"ok.zip": docx({"a.png": png()})})
rc, out = run(repo)
check(
    "a container within the cap carrying nothing marked still passes (%s)"
    % out.strip()[-80:],
    rc == 0 and "container.unexamined" not in out,
)

# --- 4e. a decompression bomb must refuse, not expand -------------------------
#
# gzip_member used to call gzip.decompress(data) whole, and pdf_streams called
# zlib.decompress(payload) with no output cap - so a small, high-ratio artefact
# forced unbounded memory before any size guard ran, on every push and every
# pull request. The fix bounds the read at the point of decompression and
# reports container.unexamined - the same refusal the depth cap already uses -
# rather than a silent skip or a clean pass. zip_members() took the same fix
# (archive.open + a bounded read, declared file_size kept only as a pre-filter)
# for the same reason, but has no fixture of its own here: CPython's zipfile
# already refuses to decompress past a member's declared size (it cross-checks
# the CRC once that many bytes have been produced), so archive.read(info) was
# never actually unbounded there - the fixtures for a deflated container above
# (report.docx, doc.odt) already exercise the changed read path.

t0 = time.time()
gzip_bomb = gzip.compress(b"\x00" * (ws.CONTAINER_MAX_BYTES + 16 * 1024 * 1024))
repo = fixture_repo(TMP, {"bomb.gz": gzip_bomb})
rc, out = run(repo)
elapsed = time.time() - t0
check(
    "a gzip bomb is refused rather than fully expanded (%s)" % out.strip()[-80:],
    rc == 1 and "container.unexamined" in out,
)
check("and the scan of it completes in bounded time (%.1fs)" % elapsed, elapsed < 20)

t0 = time.time()
pdf_bomb_payload = zlib.compress(
    b"\x00" * (ws.CONTAINER_MAX_MEMBER_BYTES + 16 * 1024 * 1024)
)
pdf_bomb_stream = (
    b"9 0 obj\n<< /Type /XObject /Subtype /Image /Filter /FlateDecode /Length %d >>\n"
    b"stream\n" % len(pdf_bomb_payload) + pdf_bomb_payload + b"\nendstream\nendobj\n"
)
pdf_bomb_doc = pdf({"Title": "bomb"})
pdf_bomb_doc = pdf_bomb_doc.replace(b"%PDF-1.4\n", b"%PDF-1.4\n%% embedded\n", 1)
repo = fixture_repo(TMP, {"bomb.pdf": pdf_bomb_doc[:-1] + pdf_bomb_stream})
rc, out = run(repo)
elapsed = time.time() - t0
check(
    "a PDF Flate stream that inflates past its budget is refused (%s)"
    % out.strip()[-80:],
    rc == 1 and "container.unexamined" in out,
)
check("and the scan of it completes in bounded time (%.1fs)" % elapsed, elapsed < 20)

# UTF-16 with no byte-order mark: NUL-heavy, so it took the binary path and its
# codepoints were never scanned. The evasion cost one saved byte.
repo = fixture_repo(
    TMP, {"nobom.md": ("secret" + chr(0x200B) + "mark\n").encode("utf-16-le")}
)
rc, out = run(repo)
check(
    "UTF-16 with no BOM is scanned as the text it is (%s)" % out.strip()[-80:],
    rc == 1 and "text.zero-width" in out,
)
repo = fixture_repo(TMP, {"plain.bin": bytes(range(256)) * 40})
rc, out = run(repo)
check(
    "and a real binary is not decoded as UTF-16 on the strength of its NULs (%s)"
    % out.strip()[-80:],
    rc == 0,
)

# An ANNOTATED tag carries a message of its own and rides the same push. The
# walk filtered to blobs, and the commit pass only ever saw commit objects.
tag_repo = fixture_repo(TMP, {"a.txt": b"plain\n"})
subprocess.run(
    [
        "git",
        "-C",
        tag_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "tag",
        "-a",
        "v1",
        "-m",
        "release" + chr(0x200B) + "notes",
    ],
    check=True,
    env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"),
)
tag_sha = subprocess.run(
    ["git", "-C", tag_repo, "rev-parse", "refs/tags/v1"], capture_output=True, text=True
).stdout.strip()
p = subprocess.run(
    [sys.executable, SCANNER, "push", "nosuchremote"],
    input="refs/tags/v1 %s refs/tags/v1 %s\n" % (tag_sha, "0" * 40),
    capture_output=True,
    text=True,
    cwd=tag_repo,
    env=dict(
        os.environ,
        WATERMARK_IDENTITY="github.com/Abhijeet34/fixture",
        WATERMARK_ALLOW=ALLOWLIST,
        WATERMARK_OPTOUT=EMPTY_OPTOUT,
    ),
)
check(
    "an annotated tag's message is scanned (%s)" % (p.stdout + p.stderr).strip()[-80:],
    p.returncode == 1 and "text.zero-width" in p.stdout + p.stderr,
)

# An ICO is a container nobody wrote a reader for - and exiftool cannot read one
# at all, so the asset is found by its own signature instead.
jpg_payload = subprocess.run(
    ["exiftool", "-Software=Midjourney v6", "-o", os.path.join(TMP, "carve.jpg"), "-"],
    input=open(os.path.join(TMP, "encodings", "real.png"), "rb").read()
    if os.path.exists(os.path.join(TMP, "encodings", "real.png"))
    else png(),
    capture_output=True,
)
carved = os.path.join(TMP, "carve.jpg")
if os.path.exists(carved):
    with open(carved, "rb") as fh:
        payload = fh.read()
    ico = (
        struct.pack("<HHH", 0, 1, 1)
        + struct.pack("<BBBBHHII", 8, 8, 0, 0, 1, 24, len(payload), 22)
        + payload
    )
    repo = fixture_repo(TMP, {"app.ico": ico})
    rc, out = run(repo)
    check(
        "an ICO wrapping a marked JPEG is refused via the signature carve (%s)"
        % out.strip()[-80:],
        rc == 1 and "embedded@" in out,
    )

# The bound that keeps this affordable, and the one that keeps it honest: a
# container nested past the depth cap is not expanded, and an ordinary
# repository full of zips does not become a wall of refusals.
inner = docx({"word/media/image1.png": MARKED_PNG})
outer = docx({"inner.docx": inner})
repo = fixture_repo(TMP, {"bundle.zip": outer})
rc, out = run(repo)
check(
    "a container nested two deep is still reached (%s)" % out.strip()[-80:],
    rc == 1 and "inner.docx" in out,
)
repo = fixture_repo(TMP, {"plain.zip": docx({"notes.txt": b"plain prose\n"})})
rc, out = run(repo)
check(
    "a zip carrying nothing marked still passes (%s)" % out.strip()[-80:],
    rc == 0 and "No detective provenance found" in out,
)

# --- 4c. every generator NAME has its own fixture ------------------------------
#
# The RULES contract covers rule ids; a named-generator list needs the same
# treatment one level down, because a name added to the regex and never
# exercised is a detection certified by nobody. The audit measured Suno,
# ElevenLabs and "Runway Gen-4 Turbo" all landing as non-blocking advisories,
# which is what a stale list looks like from the outside.
GENERATOR_NAMES = {
    "binary.generator-tool": [
        "Generated with Suno",
        "Udio v1.5",
        "Created with ElevenLabs",
        "Runway Gen-4 Turbo",
        "RunwayML Gen-2",
        "Pika 1.5",
        "Kling AI 1.6",
        "Luma Dream Machine",
        "Hailuo AI",
        "MiniMax video-01",
        "Adobe Firefly Image 3",
        "Firefly",
        "Mochi 1",
        "Wan 2.1",
        "Qwen-Image",
        "Midjourney v6.1",
        "Stable Diffusion 3",
    ],
    "binary.pixel-watermark-known": [
        "Google Imagen 4",
        "Veo 3",
        "Gemini 2.5 Flash Image",
        "Nano Banana",
        "Lyria 2",
        "SynthID",
        "Made with Google AI",
        "Google DeepMind",
    ],
}
# Attributive tool records that must NOT become blocking: the list is scoped to
# generator FIELDS, so widening it too far turns every photograph into a refusal.
NOT_A_GENERATOR = [
    "Adobe Photoshop 26.0",
    "Preview.app",
    "GIMP 2.10",
    "darktable 4.6",
    "Capture One 23",
    "Lightroom Classic 14",
    "appifact kit",
]
for expected, names in sorted(GENERATOR_NAMES.items()):
    for name in names:
        fired = ws.rules_from_exif({"IFD0:Software": name})
        check(
            "generator name %r is %s (got %s)" % (name, expected, sorted(fired)),
            expected in fired,
        )
for name in NOT_A_GENERATOR:
    fired = ws.rules_from_exif({"IFD0:Software": name})
    check(
        "editor name %r stays advisory (got %s)" % (name, sorted(fired)),
        fired == {"binary.tool-tag"},
    )

# --- 4a. one undecodable byte must not silence the codepoint scan -------------
#
# Every fixture here reported `clean` at rc=0 before this rule existed. The
# constructions are the audited ones: a UTF-16 file, UTF-8 prose with one bad
# trailing byte, and a latin-1/UTF-8 mixture.
ZWSP = chr(0x200B)
ENC = os.path.join(TMP, "encodings")
os.makedirs(ENC, exist_ok=True)


def enc_case(name, data):
    path = os.path.join(ENC, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


e01 = enc_case("e01_utf16.md", ("secret" + ZWSP + "mark\n").encode("utf-16"))
e02 = enc_case(
    "e02_badbyte.md", ("ships" + ZWSP + "friday\n").encode("utf-8") + b"\xff"
)
e03 = enc_case(
    "e03_mixed.txt", "caf\xe9 ".encode("latin-1") + ("a" + ZWSP + "b\n").encode("utf-8")
)
for path, why in (
    (e01, "a UTF-16 file"),
    (e02, "one bad trailing byte"),
    (e03, "a latin-1/UTF-8 mixture"),
):
    rc, out = verb(["inspect", path], env={"WATERMARK_ALLOW": EMPTY_OPTOUT})
    check(
        "%s does not hide a zero-width space (%s)" % (why, out.strip()[-70:]),
        rc == 1 and "text.zero-width" in out,
    )
# The two that cannot be decoded say so as well, and name the byte.
for path in (e02, e03):
    rc, out = verb(["inspect", path], env={"WATERMARK_ALLOW": EMPTY_OPTOUT})
    check(
        "the undecodable blob is reported as one, naming the byte (%s)"
        % out.strip()[-70:],
        "encoding.undecodable" in out and "offset" in out,
    )
check("encoding.undecodable is not reported as clearable", "unscannable" in out)

# The neutralised member of the pair: same prose, no bad byte, nothing invisible.
fires, neutral = ENCODING_FIXTURES["encoding.undecodable"]
rc, out = verb(
    ["inspect", enc_case("enc_fires.md", fires)], env={"WATERMARK_ALLOW": EMPTY_OPTOUT}
)
check(
    "encoding.undecodable fires on its fixture",
    rc == 1 and "encoding.undecodable" in out,
)
rc, out = verb(
    ["inspect", enc_case("enc_clean.md", neutral)],
    env={"WATERMARK_ALLOW": EMPTY_OPTOUT},
)
check(
    "encoding.undecodable does not fire on the neutralised fixture (%s)"
    % out.strip()[-60:],
    rc == 0 and ": clean  " in out,
)

# A real binary must still scan as one - the refusal is for text-like blobs, and
# widening it to every PNG would be the noise that trains readers to skim.
rc, out = verb(
    ["inspect", enc_case("real.png", png())], env={"WATERMARK_ALLOW": EMPTY_OPTOUT}
)
check(
    "a genuine binary is not reported undecodable (%s)" % out.strip()[-60:],
    rc == 0 and "encoding.undecodable" not in out,
)
# A UTF-16 file with nothing invisible in it is clean: honouring the BOM must
# not invent findings out of the NUL bytes UTF-16 is made of.
rc, out = verb(
    ["inspect", enc_case("plain_utf16.md", "plain prose\n".encode("utf-16"))],
    env={"WATERMARK_ALLOW": EMPTY_OPTOUT},
)
check(
    "a plain UTF-16 file is clean (%s)" % out.strip()[-60:],
    rc == 0 and ": clean  " in out,
)
# UTF-32LE opens with the whole UTF-16LE mark, so the BOM table's order decides
# whether this file is read as text or as a channel that is not there.
rc, out = verb(
    ["inspect", enc_case("plain_utf32.md", "plain prose\n".encode("utf-32"))],
    env={"WATERMARK_ALLOW": EMPTY_OPTOUT},
)
check(
    "a plain UTF-32 file is clean, so the BOM table is ordered longest-first", rc == 0
)
# A binary that happens to open with two BOM-shaped bytes decodes to control
# characters, and scanning that garbage would invent a finding per stray pair.
rc, out = verb(
    ["inspect", enc_case("notutf16.bin", b"\xff\xfe" + bytes(range(0, 32)) * 40)],
    env={"WATERMARK_ALLOW": EMPTY_OPTOUT},
)
check(
    "a control-heavy blob behind a BOM is treated as binary (%s)" % out.strip()[-60:],
    "text.zwnbsp" not in out,
)

# `clean` on an undecodable file must degrade to a refusal: removing the
# codepoint does not make the file decodable, so nothing established it clean.
dirty = enc_case("clean_me.md", ("ships" + ZWSP + "friday\n").encode("utf-8") + b"\xff")
rc, out = verb(["clean", dirty], env={"WATERMARK_ALLOW": EMPTY_OPTOUT})
check(
    "clean reports an undecodable file UNSCANNABLE rather than clean (%s)"
    % out.strip()[-70:],
    rc == 1 and "UNSCANNABLE" in out and "CLEANED and VERIFIED" not in out,
)
with open(dirty, "rb") as fh:
    after = fh.read()
check(
    "clean still removed the codepoint it could remove",
    ZWSP.encode("utf-8") not in after and b"\xff" in after,
)

# The byte markers, not exiftool, are what catch a container exiftool cannot
# parse. A .bin gets no exiftool pass at all, so this asserts the other half.
repo = fixture_repo(
    TMP, {"blob.bin": b"\x00\x01\x02" + b"c2pa.watermarked.unbound" + b"\xff" * 40}
)
rc, out = run(repo)
check(
    "a c2pa marker in an unrecognised binary container is still caught",
    rc == 1 and "binary.watermark-assertion" in out,
)

# Source code naming these strings must NOT be a finding: this file does, and so
# does the scanner beside it. That is the RLO-detector problem, and the fix is
# that the byte markers are matched against binary blobs only.
repo = fixture_repo(
    TMP, {"scan.py": b'MARKERS = ["c2pa.watermarked.unbound", "digitalSourceType"]\n'}
)
rc, out = run(repo)
check(
    "source code naming the markers is not a finding (%s)" % out.strip()[-90:], rc == 0
)

# --- 4b. PDFs: examined, classified, and reported rather than skipped ---------
#
# The defect these pin is an empty green, not a missed refusal: the gate said
# `clean` for a PDF whose metadata it had never read. So each case asserts BOTH
# halves - that the tag is named, and that naming it did not turn a bare tool
# tag into a refusal.

for rule, (fires, neutral) in sorted(PDF_FIXTURES.items()):
    repo = fixture_repo(TMP, {"doc.pdf": fires})
    rc, out = run(repo)
    check(
        "%s: the push gate NAMES the tag and does not refuse on it (%s)"
        % (rule, out.strip()[-90:]),
        rc == 0 and rule in out and "appifact kit" in out,
    )
    check(
        "%s: the summary counts it rather than reporting an unqualified clean" % rule,
        "1 tool tag(s) noted" in out,
    )
    repo = fixture_repo(TMP, {"doc.pdf": neutral})
    rc, out = run(repo)
    check(
        "%s: a PDF with no metadata reports nothing noted (%s)"
        % (rule, out.strip()[-90:]),
        rc == 0 and "0 tool tag(s) noted" in out and rule not in out,
    )

pdf_dir = os.path.join(TMP, "pdfs")
os.makedirs(pdf_dir, exist_ok=True)


def write_pdf(name, data):
    path = os.path.join(pdf_dir, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


tagged = write_pdf("tagged.pdf", pdf({"Producer": "appifact kit"}))
rc, out = verb(["inspect", tagged])
check(
    "inspect reports a PDF Producer instead of calling the file clean (%s)"
    % out.strip()[-90:],
    rc == 0
    and "binary.tool-tag" in out
    and "appifact kit" in out
    and "PDF:Producer" in out
    and "1 noted, 0 blocking" in out,
)
check(
    "inspect's summary for a tagged PDF carries no bare `clean`", ": clean  " not in out
)

bare = write_pdf("bare.pdf", pdf())
rc, out = verb(["inspect", bare])
check(
    "inspect still says clean for a PDF carrying no metadata at all (%s)"
    % out.strip()[-90:],
    rc == 0 and ": clean  " in out and "noted" not in out,
)

# A container the inspector could not parse was never established clean.
# exiftool exits 0 on this file, reads no Producer, and warns - and the warning
# was the one thing scan_exif ignored.
fires, neutral = UNREADABLE_FIXTURES["binary.unreadable"]
broken = write_pdf("broken_xref.pdf", fires)
rc, out = verb(["inspect", broken])
check(
    "a PDF whose xref table is wrong refuses instead of reporting clean (%s)"
    % out.strip()[-90:],
    rc == 1 and "binary.unreadable" in out,
)
repaired = os.path.join(pdf_dir, "repaired.pdf")
qpdf = subprocess.run(["qpdf", broken, repaired], capture_output=True)
if os.path.exists(repaired):
    recovered = subprocess.run(
        ["exiftool", "-s", "-Producer", repaired], capture_output=True, text=True
    ).stdout
    check(
        "and the tag it hid is what a viewer's own xref repair recovers",
        "Midjourney" in recovered,
    )
rc, out = verb(["inspect", write_pdf("sound.pdf", neutral)])
check(
    "a PDF with a correct xref is not refused for a warning it never emitted (%s)"
    % out.strip()[-90:],
    rc == 0 and "binary.unreadable" not in out,
)
# The warning CLASSES, enumerated: exiftool's routine tag-level noise carries a
# [minor] prefix and must not red-light a photograph.
for w in (
    "Error reading xref table",
    "Invalid xref table",
    "Truncated PNG image",
    "Bad header",
    "Corrupted JPEG data",
):
    check(
        "warning %r is a parse failure" % w,
        ws.rules_from_exif({"ExifTool:Warning": w}) == {"binary.unreadable"},
    )
for w in (
    "[minor] Possibly incorrect maker notes offsets",
    "[minor] Truncated MakerNote directory",
    "Tag ID 0x1234 not defined",
    "Odd offset for ICC_Profile",
):
    check(
        "warning %r is not a parse failure" % w,
        ws.rules_from_exif({"ExifTool:Warning": w}) == set(),
    )

# The classification is the SAME taxonomy the images use: a named generative
# pipeline in a PDF is `binary.generator-tool` and refuses, wherever it sits.
for name, data, where in [
    ("info-gen.pdf", pdf({"Producer": "Midjourney v6"}), "PDF:Producer"),
    (
        "xmp-gen.pdf",
        pdf(xmp="<xmp:CreatorTool>Adobe Firefly</xmp:CreatorTool>"),
        "XMP-xmp:CreatorTool",
    ),
]:
    rc, out = verb(["inspect", write_pdf(name, data)])
    check(
        "a generative tool in %s is a blocking finding (%s)"
        % (where, out.strip()[-70:]),
        rc == 1 and "binary.generator-tool" in out and where in out,
    )

# `clean` must not strip an attributive tag - that is provenance a reader uses.
keep = write_pdf("keep.pdf", pdf({"Producer": "appifact kit"}))
rc, out = verb(["clean", keep])
with open(keep, "rb") as fh:
    kept_bytes = fh.read()
check(
    "clean names the tool tag and leaves the file byte-identical (%s)"
    % out.strip()[-70:],
    rc == 0
    and "binary.tool-tag" in out
    and "left in place" in out
    and kept_bytes == pdf({"Producer": "appifact kit"})
    and not os.path.exists(keep + ".orig"),
)

# The byte witness, because exiftool's own re-read is not one for a PDF:
# `-all= -overwrite_original` is an INCREMENTAL update, so exiftool reports the
# tag gone while the value is still in the file. Measured 2026-08-31. The
# witness is unchanged; what changed is that `clean_pdf` now follows the strip
# with a qpdf rewrite, so there is nothing left for the witness to catch.
strip_me = write_pdf("strip.pdf", pdf({"Producer": "Midjourney v6"}))
rc, out = verb(["clean", strip_me])
with open(strip_me, "rb") as fh:
    after = fh.read()
check(
    "clean rewrites the PDF rather than leaving the value in its bytes (%s)"
    % out.strip()[-90:],
    rc == 0 and "CLEANED and VERIFIED" in out and b"Midjourney v6" not in after,
)
# And the witness itself still fires: exiftool ALONE leaves the value behind,
# which is what makes the rewrite load-bearing rather than decorative.
alone = write_pdf("alone.pdf", pdf({"Producer": "Midjourney v6"}))
subprocess.run(
    ["exiftool", "-q", "-all=", "-overwrite_original", "--", alone], capture_output=True
)
with open(alone, "rb") as fh:
    check(
        "exiftool alone still leaves the PDF value recoverable in the bytes",
        b"Midjourney v6" in fh.read(),
    )
# The metadata writer must actually have RUN. A PDF whose objects hold no NUL
# byte decodes as text, and the writer selection used to be an elif - so the
# text writer claimed the file and the metadata exiftool had just flagged was
# never touched, while `clean` reported success having removed nothing.
probe = subprocess.run(
    ["exiftool", "-s3", "-Producer", strip_me], capture_output=True, text=True
)
check(
    "the metadata writer ran on a PDF that also decodes as text (Producer=%r)"
    % probe.stdout.strip(),
    probe.stdout.strip() == "" and after != pdf({"Producer": "Midjourney v6"}),
)

# An allowlist entry excuses a noted tag exactly as it excuses a blocking one.
repo = fixture_repo(TMP, {"doc.pdf": pdf({"Producer": "appifact kit"})})
sha_pdf = subprocess.run(
    ["git", "-C", repo, "rev-parse", "HEAD:doc.pdf"], capture_output=True, text=True
).stdout.strip()
allow_pdf = os.path.join(TMP, "allow-pdf")
with open(allow_pdf, "w") as fh:
    fh.write(
        "blob github.com/Abhijeet34/fixture %s doc.pdf  # the publishing tool, kept\n"
        % sha_pdf
    )
rc, out = run(repo, allow=allow_pdf)
check(
    "an allowlisted PDF prints its reason instead of the note",
    rc == 0 and "the publishing tool, kept" in out and "NOTED" not in out,
)

# The image half of the same taxonomy: an editor a human drove is still not a
# refusal, and is no longer silent either.
repo = fixture_repo(TMP, {"a.png": png([text_chunk(b"Software", b"Preview.app")])})
rc, out = run(repo)
check(
    "a human's image editor is noted, not refused (%s)" % out.strip()[-90:],
    rc == 0 and "binary.tool-tag" in out and "Preview.app" in out,
)

# --- 5. the allowlist ----------------------------------------------------------

png_c2pa = png([c2pa_box(b"urn:c2pa:x c2pa.assertions")])
repo = fixture_repo(TMP, {"hero.png": png_c2pa})
sha = subprocess.run(
    ["git", "-C", repo, "rev-parse", "HEAD:hero.png"], capture_output=True, text=True
).stdout.strip()

allow = os.path.join(TMP, "allow")
with open(allow, "w") as fh:
    fh.write(
        "blob github.com/Abhijeet34/fixture %s hero.png  # a stated reason\n" % sha
    )
rc, out = run(repo, allow=allow)
check(
    "an allowlisted blob passes and its reason is printed",
    rc == 0 and "a stated reason" in out,
)

# Keyed on CONTENT: a regenerated asset does not inherit the exception.
repo2 = fixture_repo(TMP, {"hero.png": png([c2pa_box(b"urn:c2pa:y c2pa.assertions")])})
rc, out = run(repo2, allow=allow)
check("a different blob at the same path is refused", rc == 1)

with open(allow, "w") as fh:
    fh.write("blob github.com/Abhijeet34/other %s hero.png  # a stated reason\n" % sha)
rc, out = run(repo, allow=allow)
check("an entry for another repository does not excuse this one", rc == 1)

with open(allow, "w") as fh:
    fh.write("blob github.com/Abhijeet34/fixture %s hero.png\n" % sha)
rc, out = run(repo, allow=allow)
check(
    "an entry with no reason is refused, not silently honoured",
    rc == 1 and "must name its reason" in out,
)

with open(allow, "w") as fh:
    fh.write("wat github.com/Abhijeet34/fixture %s hero.png # r\n" % sha)
rc, out = run(repo, allow=allow)
check("an unknown entry kind refuses", rc == 1 and "unknown entry kind" in out)

# The shipped allowlist must parse, and it must not excuse content that no
# longer exists. The banner entry went stale that way: the decision it held open
# was answered on 2026-09-01 by replacing the asset, so the sha it named stopped
# being at `assets/banner.png` and the exemption excused a file that now passes
# on its own. Prove it against the real scanner: a marked blob at that same
# repo and path is refused by the shipped allowlist, because no entry excuses
# it any more.
repo = fixture_repo(TMP, {"assets/banner.png": png_c2pa})
rc, out = run(repo, allow=ALLOWLIST, identity="github.com/Abhijeet34/firstmate")
check(
    "the shipped allowlist no longer excuses a marked banner (%s)" % out.strip()[-90:],
    rc == 1,
)

# The blob check above holds for any content at that path, by construction,
# since a blob entry is keyed on an exact sha: it cannot tell "the stale
# entry is gone" from "no entry ever named this path". Query the real parser
# directly with the exact sha the removed entry named, so the check is red
# against the pre-change conf and green against the shipped one.
shipped_allow = ws.Allow(ALLOWLIST)
check(
    "the shipped allowlist's parsed entries no longer excuse the dead banner sha",
    shipped_allow.blob_reason(
        "github.com/Abhijeet34/firstmate",
        "f81282ea33287428b787fe2a2d1d01bb7185970c",
        "assets/banner.png",
    )
    is None,
)

repo = fixture_repo(TMP, {"clean.txt": b"nothing to see\n"})
rc, out = run(repo, allow=ALLOWLIST)
check("the shipped allowlist parses (%s)" % out.strip()[-90:], rc == 0)

# --- 5b. the allowlist is graded per PATH, not per blob ------------------------
#
# One path was recorded per blob sha and the whole blob took that path's
# verdict. Measured both ways round against the shipped allowlist's automation
# entry: the copy sorting AFTER the allowlisted path rode the exemption through
# at rc=0, and the copy sorting BEFORE it refused and stripped the legitimate
# path of its exemption. Enumeration order decided which, so both orders are
# asserted here.
GUARD = (
    "case $f in *[" + chr(0x200B) + chr(0x202E) + chr(0xFEFF) + "]*) warn ;; esac\n"
).encode("utf-8")
LEGIT = "system-maintenance/zsh/zshrc.d/80-archives-files.zsh"
for copy_at in ("zz-evil/copy.zsh", "evil/copy.zsh"):
    repo = fixture_repo(TMP, {LEGIT: GUARD, copy_at: GUARD})
    rc, out = run(repo, identity="github.com/Abhijeet34/automation")
    check(
        "the laundered copy at %s is refused (%s)" % (copy_at, out.strip()[-80:]),
        rc == 1 and copy_at in out,
    )
    check(
        "and the legitimate path keeps its exemption in the same run",
        "ALLOWED  " + LEGIT in out,
    )

# --- 5c. commit messages, filenames and gitlink names --------------------------
#
# `enumerate_objects` filtered to blobs, so only file CONTENT was ever examined.
# A commit message is the cheapest place to put a machine-readable channel.
msg_repo = fixture_repo(TMP, {"ok.txt": b"plain\n"})
subprocess.run(
    [
        "git",
        "-C",
        msg_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "release" + chr(0x200B) + "notes",
    ],
    check=True,
    env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"),
)
rc, out = run(msg_repo)
check(
    "a zero-width space in a commit message refuses the push (%s)" % out.strip()[-80:],
    rc == 1 and "commit message" in out and "text.zero-width" in out,
)
check(
    "and the refusal names a remedy `clean` cannot supply",
    "rewording it" in out and "git commit --amend" in out,
)

name_repo = fixture_repo(TMP, {"read" + chr(0x200B) + "me.txt": b"plain\n"})
rc, out = run(name_repo)
check(
    "a zero-width space in a FILENAME refuses the push (%s)" % out.strip()[-80:],
    rc == 1 and "(filename)" in out and "text.zero-width" in out,
)
check("and the refusal says renaming is the fix", "renaming the file" in out)

# A gitlink has no blob at all - the old walk reported `examined 0 blob(s)`.
link_repo = fixture_repo(TMP, {"seed.txt": b"seed\n"})
env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
subprocess.run(
    [
        "git",
        "-C",
        link_repo,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000,%s,sub%smod" % ("1" * 40, chr(0x200B)),
    ],
    check=True,
    env=env,
)
subprocess.run(
    [
        "git",
        "-C",
        link_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "commit",
        "--quiet",
        "-m",
        "gitlink",
    ],
    check=True,
    env=env,
)
rc, out = run(link_repo)
check(
    "a gitlink whose directory name carries a channel refuses (%s)" % out.strip()[-80:],
    rc == 1 and "(filename)" in out,
)

# An ordinary repository must not become a refusal: every name and message in
# the fixture repo below is plain ASCII.
plain_repo = fixture_repo(TMP, {"src/main.py": b"print(1)\n", "README.md": b"# hi\n"})
rc, out = run(plain_repo)
check(
    "a repository with plain names and messages still passes (%s)" % out.strip()[-80:],
    rc == 0 and "No detective provenance found" in out,
)

# --- 6. fail closed ------------------------------------------------------------

# A directory of symlinks to the core tools, because macOS 26 ships /usr/bin/jq
# and friends: PATH=/usr/bin would not prove absence of anything.
stub = os.path.join(TMP, "stub")
os.makedirs(stub, exist_ok=True)
for tool in ("git", "sh", "sed", "env"):
    # `command` is a shell builtin, so it must be reached through a shell: the
    # direct-exec form worked only on macOS, which ships /usr/bin/command, and
    # raised FileNotFoundError on the Linux runner.
    found = subprocess.run(
        ["/bin/sh", "-c", "command -v %s" % tool], capture_output=True, text=True
    ).stdout.strip()
    if found and not os.path.exists(os.path.join(stub, tool)):
        os.symlink(found, os.path.join(stub, tool))
check(
    "the stub PATH really has no exiftool",
    not os.path.exists(os.path.join(stub, "exiftool")),
)

repo = fixture_repo(TMP, {"clean.txt": b"nothing to see\n"})
rc, out = run(repo, env={"PATH": stub})
check(
    "with the binary inspector unavailable the check REFUSES (%s)" % out.strip()[-90:],
    rc == 1 and "exiftool is not installed" in out and "COULD NOT ESTABLISH" in out,
)

e = dict(os.environ)
e.pop("WATERMARK_IDENTITY", None)
p = subprocess.run(
    [sys.executable, SCANNER, "push", "nosuchremote"],
    input="refs/heads/main %s refs/heads/main %s\n" % ("a" * 40, "0" * 40),
    capture_output=True,
    text=True,
    cwd=repo,
    env=dict(e, WATERMARK_ALLOW=ALLOWLIST, WATERMARK_OPTOUT=EMPTY_OPTOUT),
)
check(
    "an UNSET identity refuses",
    p.returncode == 1 and "WATERMARK_IDENTITY is unset" in p.stderr,
)
# Empty is a repository with no origin, which is still this machine's work.
repo_marked = fixture_repo(
    TMP, {"a.png": png([c2pa_box(b"c2pa.assertions c2pa.watermarked.unbound")])}
)
rc, out = run(repo_marked, identity="")
check(
    "a repository with NO ORIGIN is still scanned, not exempted",
    rc == 1 and "DETECTIVE PROVENANCE" in out,
)

rc, out = run(repo, allow=os.path.join(TMP, "does-not-exist"))
check("an unreadable allowlist refuses", rc == 1 and "cannot read the allowlist" in out)

# --- 7. the opt-out list, which is this gate's scope ---------------------------
#
# Opposite default to the secret scan beside it: every repository is scanned
# unless an entry with a written reason says otherwise, because a repository
# created tomorrow is on no allowlist and would otherwise be born ungated.

marked = fixture_repo(
    TMP, {"a.png": png([c2pa_box(b"c2pa.assertions c2pa.watermarked.unbound")])}
)
rc, out = run(marked, identity="github.com/SomeoneElse/brand-new-repo")
check(
    "a repository on NO allowlist is scanned from its first push",
    rc == 1 and "DETECTIVE PROVENANCE" in out,
)

optout = os.path.join(TMP, "optout")
with open(optout, "w") as fh:
    fh.write("github.com/SomeoneElse/*  # a third-party upstream we only read\n")
rc, out = run(marked, identity="github.com/SomeoneElse/brand-new-repo", optout=optout)
check(
    "an opted-out repository is left alone, and the reason is printed",
    rc == 0 and "we only read" in out,
)
rc, out = run(marked, identity="github.com/Abhijeet34/fixture", optout=optout)
check("the opt-out does not leak to a repository it does not name", rc == 1)

with open(optout, "w") as fh:
    fh.write("github.com/SomeoneElse/*\n")
rc, out = run(marked, identity="github.com/SomeoneElse/brand-new-repo", optout=optout)
check(
    "an opt-out entry with NO REASON is refused, not honoured",
    rc == 1 and "must name its reason" in out,
)

rc, out = run(marked, optout=os.path.join(TMP, "no-such-optout"))
check(
    "an unreadable opt-out list refuses",
    rc == 1 and "cannot read the opt-out list" in out,
)

# The shipped list must parse, and must name only upstreams - never one of ours.
with open(OPTOUT, encoding="utf-8") as fh:
    shipped_optout = [ln.split("#")[0].strip() for ln in fh if ln.split("#")[0].strip()]
check("the shipped opt-out list has entries", bool(shipped_optout))
check(
    "no repository of ours is opted out (%s)"
    % [g for g in shipped_optout if "Abhijeet34" in g],
    not [g for g in shipped_optout if "Abhijeet34" in g],
)
rc, out = run(fixture_repo(TMP, {"c.txt": b"clean\n"}), optout=OPTOUT)
check("the shipped opt-out list parses (%s)" % out.strip()[-80:], rc == 0)

# --- 8. clean and verify -------------------------------------------------------

work = os.path.join(TMP, "work")
os.makedirs(work, exist_ok=True)

# Text: the flagged codepoints go, everything else survives byte for byte.
note = os.path.join(work, "note.md")
body = (
    "Ships Friday."
    + chr(0x200B)
    + chr(0xE0041)
    + " Status "
    + chr(0x26A0)
    + chr(0xFE0F)
    + " ok.\n"
)
with open(note, "w", encoding="utf-8") as fh:
    fh.write(body)
rc, out = verb(["inspect", note])
check(
    "inspect reports the text findings and tags them clearable",
    rc == 1 and "clearable" in out,
)
rc, out = verb(["clean", note])
check(
    "clean removes them and says it re-read the file (%s)" % out.strip()[-70:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
with open(note, encoding="utf-8") as fh:
    after = fh.read()
check(
    "clean kept the emoji and its presentation selector",
    after == "Ships Friday. Status " + chr(0x26A0) + chr(0xFE0F) + " ok.\n",
)
check("clean left the original alongside", os.path.exists(note + ".orig"))
rc, out = verb(["verify", note])
check("verify confirms the cleaned file", rc == 0 and "VERIFIED CLEAN" in out)
rc, out = verb(["verify", note + ".orig"])
check(
    "verify still reports the untouched original as NOT CLEAN",
    rc == 1 and "NOT CLEAN" in out,
)

# Never in place with no original alongside.
rc, out = verb(["clean", note + ".orig"])
check(
    "clean refuses when the .orig backup slot is already taken",
    "already exists" in out or rc == 0,
)

# Binary: the manifest goes, and the verdict comes from re-reading the file.
asset = os.path.join(work, "asset.png")
with open(asset, "wb") as fh:
    fh.write(
        png(
            [
                c2pa_box(b"urn:c2pa:x c2pa.assertions"),
                text_chunk(b"Software", b"Midjourney v6"),
            ]
        )
    )
rc, out = verb(["clean", asset])
check(
    "clean strips a container manifest and verifies it gone",
    rc == 0 and "CLEANED and VERIFIED" in out,
)
with open(asset, "rb") as fh:
    stripped = fh.read()
check(
    "the c2pa bytes are actually gone from the file",
    b"c2pa" not in stripped and b"Midjourney" not in stripped,
)

# Attributive provenance survives the strip. A bare `-all=` is a correct strip
# and a wrong outcome: it wiped Artist, Copyright and Rights alongside the
# generator tag, which is the standing rule in ~/OPINIONS.md inverted.
attrib = os.path.join(work, "attrib.png")
with open(attrib, "wb") as fh:
    fh.write(
        png(
            [
                text_chunk(b"Artist", b"Abhijeet"),
                text_chunk(b"Copyright", b"(c) Abhijeet 2026"),
                text_chunk(b"Software", b"Midjourney v6"),
            ]
        )
    )
rc, out = verb(["clean", attrib])
check(
    "clean strips the generator tag off an attributed asset (%s)" % out.strip()[-70:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
kept_tags = subprocess.run(
    ["exiftool", "-s", "-Artist", "-Copyright", "-Software", attrib],
    capture_output=True,
    text=True,
).stdout
check("clean kept Artist", "Abhijeet" in kept_tags)
check("clean kept Copyright", "(c) Abhijeet 2026" in kept_tags)
check("clean removed Software", "Midjourney" not in kept_tags)
with open(attrib, "rb") as fh:
    check(
        "the generator value is gone from the bytes too", b"Midjourney" not in fh.read()
    )

# The other half, and the one a keep-list alone gets wrong: a detective value
# wearing an attributive field's name is still detective.
hiding = os.path.join(work, "hiding.png")
with open(hiding, "wb") as fh:
    fh.write(
        png(
            [
                text_chunk(b"Artist", b"Midjourney"),
                text_chunk(b"Copyright", b"(c) Abhijeet 2026"),
            ]
        )
    )
rc, out = verb(["inspect", hiding])
check(
    "Artist naming a generator is a finding (%s)" % out.strip()[-60:],
    rc == 1 and "binary.generator-tool" in out,
)
rc, out = verb(["clean", hiding])
check(
    "clean removes it (%s)" % out.strip()[-60:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
after_tags = subprocess.run(
    ["exiftool", "-s", "-Artist", "-Copyright", hiding], capture_output=True, text=True
).stdout
check(
    "the detective Artist did not survive as an attributive keep",
    "Midjourney" not in after_tags,
)
check("the real Copyright beside it did survive", "(c) Abhijeet 2026" in after_tags)

# A generator known to mark the PIXELS is unclearable however complete the
# metadata strip was, and says so in its own words rather than borrowing the
# manifest notice - there is no manifest here, which is the whole point.
synth = os.path.join(work, "imagen.png")
with open(synth, "wb") as fh:
    fh.write(png([text_chunk(b"Software", b"Google Imagen 4")]))
# Before any strip is attempted, inspect's notice must read as an untried
# question, not as an attempt that already failed.
_, inspect_out = verb(["inspect", synth])
check(
    "inspect (no strip attempted) uses the pre-strip VERDICT wording",
    "VERDICT: no local tool can verify this class as removed." in inspect_out,
)
check(
    "inspect's VERDICT does not borrow the post-strip wording",
    "VERDICT: removal could not be VERIFIED." not in inspect_out,
)
rc, out = verb(["clean", synth])
check(
    "clean does not call a known pixel-watermarking generator clean (%s)"
    % out.strip()[-70:],
    rc == 1 and "UNVERIFIED" in out and "CLEANED and VERIFIED" not in out,
)
# The corrected remedy contract: the verdict is that removal could not be
# VERIFIED, which is neither "clean" nor "marked", and EVERY remedy is named
# including using the asset as it is. "Replace it" alone assumes a replacement
# exists, which is false when the artefact is the work.
check(
    "the verdict is stated as unverified rather than as a refusal",
    "removal could not be VERIFIED" in out and "not the same claim as" in out,
)
# The two moments must not converge: a strip actually ran here, so the
# VERDICT sentence must say removal could not be VERIFIED, not repeat the
# pre-strip "no local tool can verify" wording that describes an attempt
# nobody made.
check(
    "clean (a strip ran) uses the post-strip VERDICT wording",
    "VERDICT: removal could not be VERIFIED." in out,
)
check(
    "clean's VERDICT does not fall back to the pre-strip wording",
    "VERDICT: no local tool can verify this class as removed." not in out,
)
check("it names the generator", "Identified generator: Google Imagen 4" in out)
check(
    "it names re-synthesis and vector recreation as remedies",
    "Re-synthesise the pixels" in out and "Recreate as VECTOR" in out,
)
check(
    "it flags transform as best-effort rather than as a clean",
    "BEST-EFFORT and" in out and "never earns a clean verdict" in out,
)
check(
    "and it leaves using the asset as it is on the table, as the author's call",
    "Use it as it is" in out and "does not make it for you" in out,
)
check(
    "the notice names the real evidence rather than a manifest",
    "embeds its watermark in the PIXELS" in out and "declares nothing" in out,
)

# The unclearable case: cleaned of everything removable, and still not clean.
unbound = os.path.join(work, "unbound.png")
with open(unbound, "wb") as fh:
    fh.write(png([c2pa_box(b"c2pa.assertions c2pa.watermarked.unbound")]))
rc, out = verb(["clean", unbound])
check("clean exits non-zero on an unbound watermark", rc != 0)
check(
    "clean says the mark is in the PIXELS and the declaration is not the mark",
    "carried in the\nPIXELS" in out or "carried in the" in out and "PIXELS" in out,
)
check(
    "clean does not call it cleaned",
    "CLEANED and VERIFIED" not in out and "UNVERIFIED" in out,
)
check(
    "and it still names every remedy rather than only replacement",
    "Use it as it is" in out and "Replace the asset" in out,
)

# `clean` must never touch a finding the allowlist excuses - otherwise running it
# over this repository would delete the bidi codepoints out of the filename
# detector's own character class.
legit = os.path.join(work, "system-maintenance", "zsh", "zshrc.d")
os.makedirs(legit, exist_ok=True)
subprocess.run(["git", "init", "--quiet", work], check=True)
target = os.path.join(legit, "80-archives-files.zsh")
guarded = (
    "case $f in *[" + chr(0x200B) + chr(0x202E) + chr(0xFEFF) + "]*) warn ;; esac\n"
)
with open(target, "w", encoding="utf-8") as fh:
    fh.write(guarded)
rc, out = verb(["clean", target], cwd=work)
with open(target, encoding="utf-8") as fh:
    kept = fh.read()
check("clean leaves an allowlisted file byte-identical", kept == guarded)
check(
    "clean says why it left it (%s)" % out.strip()[-70:],
    "allowlisted" in out or "ALLOWED" in out,
)
check(
    "clean wrote no backup for a file it did not change",
    not os.path.exists(target + ".orig"),
)

# A path whose every finding was excused is NOT a clean path, and the summary
# line is the one line a reader scans. Saying `clean` there is the same
# false-clean this gate exists to refuse, appearing in the gate's own output.
plain = os.path.join(work, "plain.md")
with open(plain, "w", encoding="utf-8") as fh:
    fh.write("nothing invisible here\n")
blocking = os.path.join(work, "blocking.md")
with open(blocking, "w", encoding="utf-8") as fh:
    fh.write("ships" + chr(0x202E) + "friday\n")

rc, out = verb(["inspect", target], cwd=work)
check(
    "inspect does not call an allowlisted-only path clean (%s)" % out.strip()[-70:],
    rc == 0 and "ALLOWED" in out and "3 allowed, 0 blocking" in out,
)
check(
    "inspect's summary for that path carries no bare `clean` verdict",
    ": clean  " not in out,
)
rc, out = verb(["inspect", plain], cwd=work)
check(
    "inspect still says clean for a path where nothing was found",
    rc == 0 and ": clean  " in out and "allowed" not in out,
)
rc, out = verb(["inspect", blocking], cwd=work)
check(
    "inspect on a real finding is unchanged",
    rc == 1 and "findings in" in out and "text.bidi" in out,
)

rc, out = verb(["verify", target], cwd=work)
check(
    "verify leads with the distinction rather than VERIFIED CLEAN (%s)"
    % out.strip()[-70:],
    rc == 0
    and "VERIFIED - 3 allowed, 0 blocking" in out
    and "VERIFIED CLEAN" not in out,
)
rc, out = verb(["verify", plain], cwd=work)
check(
    "verify still says VERIFIED CLEAN where nothing was found",
    rc == 0 and "VERIFIED CLEAN" in out,
)

# --- 8b. the writers, and whether they damage the asset ------------------------
#
# `clean` had no writer for half the formats the goal names: pdf and tiff ran
# the strip and the second byte witness correctly refused, and mp3/wav/mkv/svg
# and every zip-family container refused outright. Each case below asserts BOTH
# halves - the mark is gone AND the payload is unchanged - because a writer that
# reaches the value by destroying the asset has not cleaned anything.
writers = os.path.join(TMP, "writers")
os.makedirs(writers, exist_ok=True)


def ffprobe_hash(path, fmt):
    p = subprocess.run(
        ["ffmpeg", "-loglevel", "quiet", "-i", path, "-f", fmt, "-"],
        capture_output=True,
    )
    return hashlib.sha256(p.stdout).hexdigest() if p.returncode == 0 else None


def write(name, data):
    path = os.path.join(writers, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# PDF: exiftool's edit is an incremental update, so the value stayed in the
# bytes and the second witness refused. qpdf rebuilds from the object graph.
doc = write("doc.pdf", pdf({"Producer": "Midjourney v6", "Author": "Abhijeet"}))
rc, out = verb(["clean", doc])
check(
    "clean now rewrites a PDF instead of refusing (%s)" % out.strip()[-70:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
with open(doc, "rb") as fh:
    body = fh.read()
check("the generator value is gone from the PDF bytes", b"Midjourney" not in body)
kept = subprocess.run(
    ["exiftool", "-s", "-Author", doc], capture_output=True, text=True
).stdout
check("and the PDF's Author survived the rewrite", "Abhijeet" in kept)

# TIFF: exiftool cannot delete IFD0 from a TIFF in place, so ImageMagick
# re-encodes it - the pixel bytes are the witness that a re-encode did not
# also destroy the asset. Skipped loudly rather than silently if ImageMagick
# is not on this machine, same reason as the ffmpeg block below.
# `magick` on ImageMagick 7, `convert` on the 6 that Ubuntu still ships; the
# scanner resolves the same pair, so exercising whichever exists is what keeps
# this case measuring the TIFF writer rather than the runner's package set.
IM = next((n for n in ("magick", "convert") if shutil.which(n)), None)
if IM:
    tif = os.path.join(writers, "a.tiff")
    subprocess.run([IM, "-size", "24x16", "xc:navy", tif], check=True)
    subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            "-Software=Midjourney v6",
            "-Artist=Abhijeet",
            tif,
        ],
        check=True,
    )
    before = subprocess.run(
        [IM, tif, "-strip", "-depth", "8", "gray:-"], capture_output=True
    ).stdout
    rc, out = verb(["clean", tif])
    check(
        "clean now rewrites a TIFF instead of refusing (%s)" % out.strip()[-70:],
        rc == 0 and "CLEANED and VERIFIED" in out,
    )
    gone = subprocess.run(
        ["exiftool", "-s", "-Software", tif], capture_output=True, text=True
    ).stdout
    check("the generator is gone from the TIFF", "Software" not in gone)
    kept = subprocess.run(
        ["exiftool", "-s", "-Artist", tif], capture_output=True, text=True
    ).stdout
    check("and the TIFF's Artist survived the rewrite", "Abhijeet" in kept)
    after = subprocess.run(
        [IM, tif, "-strip", "-depth", "8", "gray:-"], capture_output=True
    ).stdout
    check(
        "and the TIFF's pixels are bit-identical after the rewrite",
        before and before == after,
    )
else:
    print("SKIP  ImageMagick is not installed, so the TIFF writer was not exercised")

# SVG: exiftool refuses to write one at all, so the removal is XML-level and the
# drawing is what must survive it.
svg = write(
    "art.svg",
    b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="16">'
    b'<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
    b'xmp:CreatorTool="Midjourney v6"/></rdf:RDF></metadata>'
    b'<rect width="24" height="16" fill="#2b8a3e"/>'
    b'<circle cx="12" cy="8" r="4" fill="#e03131"/></svg>\n',
)
rc, out = verb(["clean", svg])
check(
    "clean now rewrites an SVG instead of refusing (%s)" % out.strip()[-70:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
with open(svg, "rb") as fh:
    body = fh.read()
check("the generator is gone from the SVG", b"Midjourney" not in body)
check("and the drawing survived", b"<rect" in body and b"<circle" in body)

# A zip-family container: the marked member is rewritten, everything else is
# repacked as it was.
archive = write(
    "report.docx",
    docx(
        {
            "word/document.xml": b"<w:document>the body</w:document>",
            "word/media/image1.png": MARKED_PNG,
        }
    ),
)
rc, out = verb(["clean", archive])
check(
    "clean now rewrites a zip-family container instead of refusing (%s)"
    % out.strip()[-70:],
    rc == 0 and "CLEANED and VERIFIED" in out,
)
with zipfile.ZipFile(archive) as z:
    check("every member survived the repack", len(z.namelist()) == 3)
    check("the document body is unchanged", b"the body" in z.read("word/document.xml"))
    check(
        "and the marked member lost its generator tag",
        b"Midjourney" not in z.read("word/media/image1.png"),
    )

# An ID3 TXXX frame under BOTH exiftool spellings. Driven as records rather than
# through ffmpeg, because which frame a muxer picks and where exiftool puts the
# description are properties of the runner, not of the gate: ffmpeg 6.1.1 with
# exiftool 12.76 (ubuntu-24.04) reported this file clean while 9.0.1 with 13.55
# blocked it, and only the first shape reproduces that.
for label, record, want_rule in (
    (
        "exiftool 12.76's spelling (description inside the value)",
        {"ID3v2_4:UserDefinedText": "(comment) Generated with Suno"},
        "binary.generator-tool",
    ),
    (
        "exiftool 13.55's spelling (description promoted to the tag)",
        {"ID3v2_4:Comment": "Generated with Suno"},
        "binary.generator-tool",
    ),
):
    rules = [f[0] for f in ws.scan_exif(dict(record, SourceFile="a.mp3"))]
    check("a TXXX generator is blocking under %s" % label, want_rule in rules)

# The description is read as the FIELD, so an attributive one stays attributive
# and a human value in it is not a finding - the same rule the tag-named form
# gets, rather than a second policy for one frame type.
check(
    "a TXXX carrying authorship is not a finding",
    ws.scan_exif(
        {"SourceFile": "a.mp3", "ID3v2_4:UserDefinedText": "(artist) Abhijeet"}
    )
    == [],
)
check(
    "a TXXX carrying a detective value in an attributive field is still a finding",
    [
        f[0]
        for f in ws.scan_exif(
            {"SourceFile": "a.mp3", "ID3v2_4:UserDefinedText": "(artist) Midjourney v6"}
        )
    ]
    == ["binary.generator-tool"],
)
check(
    "a TXXX with no description at all is still read as a tool-written field",
    [
        f[0]
        for f in ws.scan_exif(
            {"SourceFile": "a.mp3", "ID3v2_4:UserDefinedText": "Generated with Suno"}
        )
    ]
    == ["binary.generator-tool"],
)

# ffmpeg's formats: `-c copy` re-encodes nothing, so the payload hash is the
# witness. Skipped loudly rather than silently if ffmpeg is not on this machine
# - a writer nothing exercised is a writer nothing certifies.
if shutil.which("ffmpeg"):
    FFMPEG_VER = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True
    ).stdout.split("\n")[0]
    EXIFTOOL_VER = subprocess.run(
        ["exiftool", "-ver"], capture_output=True, text=True
    ).stdout.strip()
    for name, source, fmt in (
        ("a.mp3", "sine=frequency=440:duration=0.4", "s16le"),
        ("a.wav", "sine=frequency=440:duration=0.4", "s16le"),
        ("a.mkv", "testsrc=duration=0.4:size=48x32:rate=10", "rawvideo"),
    ):
        target = os.path.join(writers, name)
        subprocess.run(
            ["ffmpeg", "-loglevel", "quiet", "-y", "-f", "lavfi", "-i", source]
            + (["-pix_fmt", "yuv420p"] if fmt == "rawvideo" else [])
            + [
                "-metadata",
                "comment=Generated with Suno",
                "-metadata",
                "artist=Abhijeet",
                target,
            ],
            check=True,
        )
        before = ffprobe_hash(target, fmt)
        rc, out = verb(["clean", target])
        ok = rc == 0 and "CLEANED and VERIFIED" in out
        if not ok:
            # A writer case that fails without naming what the inspector read is
            # undiagnosable off this machine: the runner's ffmpeg and exiftool
            # are not these, and the tag a muxer chooses is what decides.
            dump = subprocess.run(
                ["exiftool", "-G1", "-a", target], capture_output=True, text=True
            ).stdout
            print("DIAG  %s ffmpeg=%s exiftool=%s" % (name, FFMPEG_VER, EXIFTOOL_VER))
            print("DIAG  %s tags exiftool reports:\n%s" % (name, dump.rstrip()))
        check(
            "clean now rewrites %s instead of refusing (%s)"
            % (name, out.strip()[-60:]),
            ok,
        )
        with open(target, "rb") as fh:
            check("the generator is gone from %s" % name, b"Suno" not in fh.read())
        check(
            "and %s's payload is bit-identical after the rewrite" % name,
            before is not None and before == ffprobe_hash(target, fmt),
        )
        kept = subprocess.run(
            ["exiftool", "-s", "-Artist", target], capture_output=True, text=True
        ).stdout
        check("%s kept its Artist" % name, "Abhijeet" in kept)
else:
    print(
        "SKIP  ffmpeg is not installed, so the mp3/wav/mkv writers were not exercised"
    )

# A writer whose tool is missing must refuse by name, never fall through to one
# that cannot reach the value.
ok, why = ws.need("no-such-tool-xyz", "brew install nothing")
check(
    "a missing writer tool refuses and names the remedy (%s)" % why,
    not ok and "not installed" in why and "brew install nothing" in why,
)

# --- 8c. the verdict comes from the re-detection, structurally -----------------
#
# The captain's hard gate: every strip ends by RE-DETECTING with the full
# engine, and the verdict comes from that re-read alone. `clean_verdict` is the
# only place a clean verdict is produced and it takes the re-read as an
# argument, so there is no code path to `cleaned` that skips it. These cases
# drive that function directly over the whole outcome space, including inputs no
# file on this machine produces today - a path that CAN emit a clean verdict
# without a successful re-detection is a defect even if nothing reaches it.
GEN = ("binary.generator-tool", "Midjourney v6", "IFD0:Software", 0)
PIX = ("binary.pixel-watermark-known", "Google Imagen 4", "IFD0:Software", 0)
BAD = ("encoding.undecodable", "byte 0xFF at offset 3", "blob", 0)
TAG = ("binary.tool-tag", "Preview.app", "IFD0:Software", 0)

verdict, _lines, _replace = ws.clean_verdict([GEN], [], [])
check("a finding that is gone on the re-read is cleaned", verdict == "cleaned")

verdict, lines, _replace = ws.clean_verdict([GEN], [GEN], [])
check(
    "a finding the re-read still sees is NOT clean (%s)" % lines,
    verdict == "not-clean",
)

verdict, lines, _replace = ws.clean_verdict([GEN], [], ["Midjourney v6"])
check(
    "a value the byte witness still sees is NOT clean even when the re-read is empty",
    verdict == "not-clean" and "still in the bytes" in lines[0],
)

# The hole this function was written to close: an UNCLEARABLE class that only
# the RE-READ surfaces. The old code took its unclearable set from the
# pre-strip scan alone and filtered unclearable out of the residual, so this
# input printed CLEANED and VERIFIED.
verdict, lines, (replace, names) = ws.clean_verdict([GEN], [PIX], [])
check(
    "an unclearable class the RE-READ surfaces is never clean (%s)" % lines,
    verdict == "unverified" and "binary.pixel-watermark-known" in replace,
)
check(
    "and the message names the generator, not just the rule", "Google Imagen 4" in names
)

verdict, _lines, (replace, _names) = ws.clean_verdict([PIX], [], [])
check(
    "an unclearable class in the ORIGINAL is never clean however complete the strip",
    verdict == "unverified" and "binary.pixel-watermark-known" in replace,
)

# Re-detection that could not establish anything must never collapse to clean.
verdict, lines, _replace = ws.clean_verdict([GEN], [BAD], [])
check(
    "a re-read that could not establish the file is UNSCANNABLE, not clean (%s)"
    % lines,
    verdict == "unscannable",
)
verdict, _lines, _replace = ws.clean_verdict([], [BAD], [])
check(
    "and UNSCANNABLE wins even when nothing was found before the strip",
    verdict == "unscannable",
)

# An advisory is attributive and was never a candidate for removal, so it does
# not hold a file back - the one thing that must NOT block.
verdict, _lines, _replace = ws.clean_verdict([GEN], [TAG], [])
check("an advisory left on the re-read does not block", verdict == "cleaned")

rc, out = verb(["clean"])
check("clean with no path prints usage and exits 2", rc == 2)
rc, out = verb(["nonsense"])
check("an unknown verb exits 2", rc == 2 and "unknown verb" in out)

# --- ZZ. every rule in RULES is measured by something --------------------------
#
# A rule that no fixture exercises certifies what it never measured - this
# repository's dominant defect, seven instances on record. Half of these rules
# are covered by a (fires, neutralised) table above; the rest are covered by
# having been SEEN in a real CLI transcript, which is why this runs last and
# reads what the suite actually produced rather than a list someone wrote.
transcript = "\n".join(TRANSCRIPT)
observed = {rule for rule in ws.RULES if rule in transcript}
covered = TABLE_COVERED | observed
check(
    "every rule in RULES is measured by a fixture or observed firing (uncovered: %s)"
    % sorted(set(ws.RULES) - covered),
    covered == set(ws.RULES),
)
# And the transcript half cannot be satisfied vacuously: if it observed nothing,
# every rule it claims to cover was covered by the tables and this says so.
check(
    "the transcript actually observed rules firing (%d of %d)"
    % (len(observed), len(ws.RULES)),
    len(observed) >= len(ws.RULES) // 2,
)

subprocess.run(["rm", "-rf", TMP], check=False)

if failed:
    print(
        "\033[31m%d of %d watermark-scan checks failed\033[0m" % (len(failed), ran),
        file=sys.stderr,
    )
    sys.exit(1)
print(
    "\033[32mok\033[0m %d checks - watermark-scan detects detective provenance, "
    "keeps attributive provenance, and fails closed" % ran
)
