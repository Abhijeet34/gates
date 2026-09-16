#!/usr/bin/env python3
"""Refuse to push an artefact carrying DETECTIVE provenance.

The rule this enforces is the captain's, in ~/OPINIONS.md, "Provenance is for
readers, never for machine detection", and it covers every type of file and
data, code included. The test is PURPOSE, not medium:

  ATTRIBUTIVE provenance is kept in full and is never flagged - a cited source,
  a measured claim, an authorship line, a `Decided by:` record, a commit
  trailer, an EXIF `Software` naming the editor a human drove.

  DETECTIVE provenance is refused - a signal whose function is to let a MACHINE
  classify the artefact as machine-made. A C2PA claim asserting
  trainedAlgorithmicMedia, a `c2pa.watermarked.*` assertion, an invisible
  codepoint carrying a channel through prose or code.

TWO LEVELS, and which one you get is decided by the verb. The push GATE only
ever detects: `push` refuses and names the remedy, and never rewrites what you
are pushing, because a hook that mutated a commit would be rewriting history
behind the author. Stripping is an OPERATOR verb - `clean`, run deliberately on
a path - and it is bounded by one rule: a strip that cannot VERIFY removal
degrades to a refusal naming what remains and recommending replacement, never to
a silent pass. Removal is proven by re-detecting, never assumed from a command
exiting 0.

That rule is why some marks are UNCLEARABLE by construction. An UNBOUND
watermark lives in the pixels rather than in the metadata, so stripping the C2PA
manifest that declares it makes the file READ clean while the mark is still
there - a visible finding turned into a false pass, strictly worse than leaving
the file alone. Pixel-domain removal needs an external heavy-compute backend and
no tool can honestly certify a vendor detector will fail afterwards
(guillaumemeyer/watermarks-remover states the same limit). So the only honest
remedy there is to REPLACE the asset, and that is what the finding says.

POSTURE, taken from the gitleaks gate beside it: refuse when it cannot ESTABLISH
that the pushed commits are clean, not only when it finds something. A missing
tool, an unreadable object, an inspector that returned fewer records than it was
handed - each is a refusal. `git push --no-verify` still bypasses every hook;
this is an accident-catcher, not a control.

Usage (the machine-wide pre-push hook drives it; the ref list arrives on stdin
in git's pre-push format):

    WATERMARK_IDENTITY=<normalised origin identity> \
    WATERMARK_ALLOW=<path to the deployed allowlist> \
        watermark-scan.py <remote> [<url>]

Source of truth: system-maintenance/git-hooks/watermark-scan.py in the
gates repo. Deployed by system-maintenance/git-hooks/install.sh.
"""

import base64
import binascii
import io
import json
import os
import fnmatch
import gzip
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import threading
import unicodedata
import zipfile
import zlib

TOOL = "watermark-scan"
ZERO = "0" * 40

# Every detection this gate claims to make, by id. test-watermark-scan.sh
# requires a fixture that fires each one and asserts that neutralising the
# fixture clears it, so a rule cannot sit here certifying what it never
# measured. Adding a rule without a fixture fails that suite.
RULES = {
    "text.zero-width": "zero-width codepoint",
    "text.bidi": "bidi mark, embedding, override or isolate",
    "text.invisible-op": "word joiner or invisible operator",
    "text.soft-hyphen": "soft hyphen",
    "text.mongolian-blank": "Mongolian separator or free variation selector",
    "text.hangul-blank": "Hangul filler",
    "text.variation-selector": "variation selector with no emoji base",
    "text.zwnbsp": "zero-width no-break space",
    "text.reserved-ignorable": "reserved default-ignorable codepoint",
    "text.noncharacter": "Unicode noncharacter",
    "text.unicode-tag": "Unicode Tags block (steganographic channel)",
    "binary.c2pa-manifest": "C2PA/JUMBF provenance manifest",
    "binary.watermark-assertion": "declared watermark or soft binding",
    "binary.digital-source-type": "digitalSourceType asserting algorithmic media",
    "binary.generator-tool": "generator recorded in EXIF/XMP/PNG text",
    "binary.pixel-watermark-known": "generator known to watermark the PIXELS undeclared",
    "binary.tool-tag": "producing tool recorded in container metadata",
    "encoding.undecodable": "text-like blob no decoder could read in full",
    "binary.unreadable": "the binary inspector could not parse this container",
    "container.unexamined": "a container nested past the depth this gate opens",
}

# Which findings `clean` can actually remove, and which it must never claim to.
#
# The line is drawn by whether removal is VERIFIABLE on a re-read. Invisible
# codepoints and container metadata are either in the bytes afterwards or they
# are not. An unbound watermark lives in the PIXELS, so removing the assertion
# that declares it removes the declaration and not the mark - which is how a
# strip turns a visible finding into a false pass. `clean` therefore strips what
# it can, re-reads to prove it, and still reports NOT CLEAN with the wording
# below whenever an unclearable finding was in the original.
#
# `binary.pixel-watermark-known` is the same physics reached from the other end.
# Google's SynthID family embeds its mark in the PIXELS and does NOT declare it
# with a `c2pa.watermarked` assertion, so keying only on the declaration let
# `clean` print CLEANED and VERIFIED over a file a vendor detector still flags -
# the exact false pass this module exists to refuse, delivered by the tool
# itself. A generator on that list is therefore unclearable however successful
# the metadata strip was.
#
# The GENERIC case - an asset carrying an AI-generated marker from a generator
# NOT on that list - is a separate question and a captain decision, registered
# as `provenance-gate-audit-v7-decision-generic-ai-marker-posture`. Today's
# behaviour is clearable, which is what this gate has always done; the switch
# below is the whole change when the answer arrives, and nothing else in this
# file encodes that posture.
GENERIC_AI_MARKER_UNCLEARABLE = False

UNCLEARABLE = frozenset(
    ["binary.watermark-assertion", "binary.pixel-watermark-known"]
    + (["binary.digital-source-type"] if GENERIC_AI_MARKER_UNCLEARABLE else [])
)

# Findings that are REPORTED and never refuse on their own judgement.
#
# A container recording the tool that produced it - a PDF `Producer`, an EXIF
# `Software` - is attributive by default: `appifact kit` names a publishing tool
# the way Word or Acrobat does, and makes no claim that the artefact is
# machine-generated. Refusing on the bare tag would break every PDF in the tree.
# But SKIPPING it is the defect this tier exists to close: the gate used to say
# `clean` for a PDF whose Producer it had never looked at, which is the same
# empty-green this gate exists to prevent. So the tag is examined, named, and
# left for the allowlist or a human to judge - and `clean` never strips one,
# because attributive provenance is kept in full.
ADVISORY = frozenset(["binary.tool-tag"])

# Findings that are neither clearable nor a replace-the-asset case: the tool
# could not read the artefact in full, so nothing about it was established. Both
# block, and both have a remedy a strip cannot supply - re-save the file in one
# encoding, or regenerate the container so its own structure parses.
UNSCANNABLE = frozenset(
    ["encoding.undecodable", "binary.unreadable", "container.unexamined"]
)

# What the reader is told when a mark cannot be verified as removed.
#
# NOT "replace the asset", which is what this used to say. That wording assumes
# a replacement EXISTS, and when the artefact is precisely the thing wanted - a
# generated image or video that is the work - it is a refusal wearing a remedy's
# clothes, and it takes a decision that belongs to the author and makes it
# inside a tool. Corrected on the captain's instruction, 2026-09-02.
#
# The contract instead: state what was found, what was removed and verified,
# what could NOT be established, and every remedy that exists - including using
# the asset as it is, which is the author's call to make and must stay visible
# rather than being refused on their behalf. What the tool must never do is call
# it clean.
WHY_UNVERIFIABLE = {
    "binary.watermark-assertion": (
        "This asset DECLARES an unbound watermark: the mark is carried in the\n"
        "PIXELS, not in the metadata, so removing the manifest removes the\n"
        "declaration and not the mark."
    ),
    "binary.pixel-watermark-known": (
        "This generator embeds its watermark in the PIXELS and declares nothing\n"
        "a strip can remove, so a clean re-read says only that the metadata is\n"
        "gone - it is not evidence about the pixels."
    ),
}

REMEDIES = (
    "REMEDIES, in the order they preserve the work:\n"
    "  1. Re-synthesise the pixels through a generator that does not mark,\n"
    "     using this asset as the input - the composition survives, the pixels\n"
    "     that carry the mark do not. No local image model is installed here,\n"
    "     so this is a step you run elsewhere.\n"
    "  2. Recreate as VECTOR if this is a logo, icon, banner or diagram. Total,\n"
    "     and how this fleet's current assets are already made.\n"
    "  3. Re-encode or transform (crop, rescale, recompress). BEST-EFFORT and\n"
    "     UNVERIFIABLE - no local tool can certify a vendor detector will fail\n"
    "     afterwards, so this never earns a clean verdict.\n"
    "  4. Replace the asset, where a replacement exists.\n"
    "  5. Use it as it is. That is a decision about your own work and this tool\n"
    "     does not make it for you - what it will not do is tell you the file\n"
    "     is clean when nothing established that."
)


def unverifiable_notice(rules, names=(), stripped=False):
    """What was NOT established, why, and every remedy - never a bare refusal.

    `stripped` separates the two moments this is printed at. Detection has not
    tried anything yet, so claiming removal "could not be verified" there would
    be reporting an attempt nobody made.
    """
    lines = []
    for rule in sorted(rules):
        if rule in WHY_UNVERIFIABLE:
            lines.append(WHY_UNVERIFIABLE[rule])
    if names:
        # The generator, named. It is what lets the next asset come from a tool
        # that does not mark, which is the standing rule in ~/OPINIONS.md.
        lines.append("Identified generator: %s" % ", ".join(names))
    lines.append(
        (
            "VERDICT: removal could not be VERIFIED."
            if stripped
            else "VERDICT: no local tool can verify this class as removed."
        )
        + " That is not the same claim\n"
        "as 'this file is marked', and it is not the same claim as 'this file\n"
        "is clean' - nothing here detected the pixels either way."
    )
    lines.append(REMEDIES)
    return "\n".join(lines)


# A KNOWN CEILING, stated rather than left as a silent gap: a statistical or
# token-sampling text watermark - SynthID text among them - leaves no codepoint
# and no metadata, so nothing here detects it and nothing here can claim to
# remove it. Paraphrasing to defeat one is deliberately NOT implemented: it
# cannot be verified and it degrades the writing, which fails the quality bar in
# the other direction. The remedy for a service known to sample-watermark its
# output is not to use it; see the tool-choice rule in ~/OPINIONS.md.

# --- text ---------------------------------------------------------------------

# (first, last, rule id, label). Ranges, not a hand-listed set, because the
# reserved and Tags blocks are the channel: a codepoint nothing renders is
# usable whether or not Unicode has assigned it a name yet.
CODEPOINTS = [
    (0x00AD, 0x00AD, "text.soft-hyphen", "soft hyphen"),
    (
        0x180B,
        0x180F,
        "text.mongolian-blank",
        "Mongolian separator/free variation selector",
    ),
    (0x200B, 0x200B, "text.zero-width", "zero-width space"),
    (0x200C, 0x200D, "text.zero-width", "zero-width non-joiner/joiner"),
    (0x200E, 0x200F, "text.bidi", "bidi mark"),
    (0x202A, 0x202E, "text.bidi", "bidi embedding/override"),
    (0x2060, 0x2064, "text.invisible-op", "word joiner/invisible operator"),
    (0x2065, 0x2065, "text.reserved-ignorable", "reserved default-ignorable"),
    (0x2066, 0x2069, "text.bidi", "bidi isolate"),
    (0x3164, 0x3164, "text.hangul-blank", "Hangul filler"),
    (0xFDD0, 0xFDEF, "text.noncharacter", "noncharacter"),
    (0xFE00, 0xFE0F, "text.variation-selector", "variation selector"),
    (0xFEFF, 0xFEFF, "text.zwnbsp", "zero-width no-break space"),
    (0xFFA0, 0xFFA0, "text.hangul-blank", "halfwidth Hangul filler"),
    (0xFFF0, 0xFFF8, "text.reserved-ignorable", "reserved default-ignorable"),
    (0xE0000, 0xE007F, "text.unicode-tag", "Unicode Tags block"),
    (0xE0080, 0xE00FF, "text.reserved-ignorable", "reserved default-ignorable"),
    (0xE0100, 0xE01EF, "text.variation-selector", "variation selector supplement"),
    (0xE01F0, 0xE0FFF, "text.reserved-ignorable", "reserved default-ignorable"),
]
# The two noncharacters at the end of every plane, which the block above cannot
# express as one range.
CODEPOINTS += [
    (p + off, p + off, "text.noncharacter", "noncharacter")
    for p in range(0, 0x110000, 0x10000)
    for off in (0xFFFE, 0xFFFF)
]

CODEPOINT_INFO = {}
for _lo, _hi, _rule, _label in CODEPOINTS:
    for _c in range(_lo, _hi + 1):
        CODEPOINT_INFO[_c] = (_rule, _label)

# One compiled character class over the same table. Iterating every character of
# every blob in Python costs seconds on a first push of a large repository; a
# finditer over a class is C-speed and only the matches are then examined.
BAD_RE = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi) for lo, hi, _, _ in CODEPOINTS) + "]"
)

# Scripts in which ZWJ/ZWNJ are ordinary spelling rather than a channel:
# Arabic and its presentation forms, the Indic block range, and
# Thai/Lao/Tibetan/Myanmar.
JOINING_SCRIPTS = [
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0x0900, 0x0DFF),
    (0x0E00, 0x109F),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFE),
]


def _joining_script(ch):
    if not ch:
        return False
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in JOINING_SCRIPTS)


# Emoji=Yes characters that Unicode does NOT categorise as a symbol, listed
# explicitly because Python carries no Emoji property. The trap this exists for
# is real and was measured on 2026-08-31: U+2139 INFORMATION SOURCE, which
# no-mistakes writes as an emoji, has category `Ll` - the same category as the
# letter `a`, whose variation selector must still be a finding. A category test
# alone therefore gets this exactly backwards for the one base a fleet file uses.
EMOJI_BASE_EXTRA = frozenset(
    [0x0023, 0x002A, 0x203C, 0x2049, 0x2139, 0x2194, 0x2934, 0x2935, 0x3030, 0x303D]
    + list(range(0x0030, 0x003A))  # keycap bases 0-9
    + list(range(0x25FB, 0x25FF))
)


def _pictographic(ch):
    return bool(ch) and (
        unicodedata.category(ch) in ("So", "Sk") or ord(ch) in EMOJI_BASE_EXTRA
    )


def structurally_legitimate(text, i):
    """True when this codepoint is doing the job Unicode defines it for.

    A per-file allowlist cannot express these: an emoji is legitimate wherever
    it appears and in files nobody has written yet, so the exemption has to be
    about the CONTEXT rather than about the path. A variation selector after a
    letter, or a ZWJ inside English prose, is still a finding.
    """
    o = ord(text[i])
    prev = text[i - 1] if i else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if o in (0xFE0E, 0xFE0F):
        # Emoji presentation selector, or a keycap sequence base.
        return _pictographic(prev) or prev in "#*0123456789"
    if o == 0x200D:
        # An emoji ZWJ sequence, or an Indic/Arabic conjunct.
        if _pictographic(prev) and (
            _pictographic(nxt) or (nxt and ord(nxt) in (0xFE0E, 0xFE0F))
        ):
            return True
        return _joining_script(prev) or _joining_script(nxt)
    if o == 0x200C:
        return _joining_script(prev) or _joining_script(nxt)
    return False


def scan_text(text):
    """[(rule, label, 'U+XXXX', line)] for every codepoint that is not context-legitimate."""
    found = []
    for m in BAD_RE.finditer(text):
        i = m.start()
        if structurally_legitimate(text, i):
            continue
        o = ord(text[i])
        rule, label = CODEPOINT_INFO[o]
        found.append((rule, label, "U+%04X" % o, text.count("\n", 0, i) + 1))
    return found


# --- binaries -----------------------------------------------------------------

# Byte markers, matched against raw bytes. Binary blobs ONLY: source code
# legitimately names every one of these strings - this file does - and a
# detector that flags its own rules is the same defect as a filename-safety
# detector flagged for containing the RLO codepoint it exists to catch.
BYTE_MARKERS = [
    (b"c2pa.assertions", "binary.c2pa-manifest", "C2PA assertion store"),
    (b"c2pa.claim", "binary.c2pa-manifest", "C2PA claim"),
    (b"c2pa.signature", "binary.c2pa-manifest", "C2PA signature"),
    (b"urn:c2pa:", "binary.c2pa-manifest", "C2PA manifest URN"),
    (b"c2pa.watermarked", "binary.watermark-assertion", "C2PA watermark assertion"),
    (b"c2pa.soft-binding", "binary.watermark-assertion", "C2PA soft binding"),
    # Not a C2PA assertion and not removable: see PIXEL_WATERMARK_RE.
    (b"SynthID", "binary.pixel-watermark-known", "SynthID marker"),
    # The VALUE, never the bare field name: a camera writing
    # `digitalSourceType: digitalCapture` is recording how the picture was made
    # for a reader, which is attributive and stays. Only the algorithmic values
    # are a machine-detection claim. `scan_exif` draws the same line.
    (
        b"trainedAlgorithmicMedia",
        "binary.digital-source-type",
        "trainedAlgorithmicMedia",
    ),
    (
        b"compositeWithTrainedAlgorithmicMedia",
        "binary.digital-source-type",
        "compositeWithTrainedAlgorithmicMedia",
    ),
    (b"algorithmicMedia", "binary.digital-source-type", "algorithmicMedia"),
]

MEDIA_MAGIC = [
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF8",
    b"RIFF",
    b"II*\x00",
    b"MM\x00*",
    b"%PDF-",
    b"8BPS",
]
MEDIA_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
    ".bmp",
    ".psd",
    ".svg",
    ".pdf",
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".orf",
    ".raf",
    ".mp4",
    ".m4v",
    ".m4a",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    ".mp3",
    ".wav",
    ".flac",
    ".aiff",
    ".aif",
    ".ogg",
    ".opus",
}
# .ico/.icns/.cur are deliberately ABSENT: exiftool 13.55 answers `Unknown file
# type` and exits 1 on both, so listing them makes every repository carrying an
# application icon unpushable. An ICO wrapping a marked JPEG is reached by
# `embedded_media`, which works on any binary and needs no extension.

# Fields whose PURPOSE is to record the producing tool. A value here is read and
# matched; the field's presence alone is not a finding, because
# `Software: Preview.app` is a human's editor and squarely attributive.
GENERATOR_FIELDS = (
    "software",
    "creatortool",
    "xmptoolkit",
    "processingsoftware",
    "producer",
    "softwareagent",
    "softwareagentname",
    "claimgenerator",
    "claim_generator_infoname",
    "historysoftwareagent",
    "generator",
    "generatorname",
    "creator",
    "comment",
    "usercomment",
)

# Fields whose PURPOSE is to say who made this and under what terms. Matched
# EXACTLY, not by substring, because `creatortool` contains `creator` and is the
# opposite kind of field - the tool, not the author.
#
# Two things follow from one table. Detection never flags one of these carrying
# a human value, which is the "attributive provenance is kept in full" rule at
# the point it is read; and `clean` restores exactly these after the strip,
# which is that rule at the point it is written. A DETECTIVE value hiding in one
# - `Artist: Midjourney` - is still a finding and is still stripped, because the
# test is the value's purpose and never the field's name.
ATTRIBUTIVE_FIELDS = frozenset(
    [
        "artist",
        "author",
        "byline",
        "bylinetitle",
        "creator",
        "copyright",
        "copyrightnotice",
        "rights",
        "credit",
        "creditline",
        "source",
        "usageterms",
        "webstatement",
        "webstatementofrights",
        "licensor",
        "licensorname",
        "owner",
        "ownername",
        "contact",
        "marked",
    ]
)

# Fields whose PRESENCE is the marker: nothing but a generative pipeline writes
# a PNG text chunk called `parameters`, `prompt` or `workflow`.
GENERATOR_PRESENCE_FIELDS = (
    "parameters",
    "prompt",
    "workflow",
    "aigenerated",
    "synthid",
)

# Named generators, matched case-insensitively against a generator field's
# value. A named list fails SILENT on a generator nobody has heard of yet, which
# is exactly why it is the backstop and not the primary signal: the C2PA and
# digitalSourceType rules above are structural and vendor-independent, and they
# are what catches a tool that is not on this list.
# Refreshed 2026-09-02 after the audit measured Suno, ElevenLabs and
# "Runway Gen-4 Turbo" landing as non-blocking advisories: `runwayml` did not
# match `Runway`, and the audio pipelines were absent entirely. Audio and video
# generators rarely write C2PA or digitalSourceType, so the structural rules do
# not compensate for a stale name here the way they do for images.
# Every SHORT or dictionary-word name is anchored on both sides. Measured
# 2026-09-02 against the ASCII strings of a real 8.7MB binary: unanchored
# `udio` matched `AVFAudio.framework` and unanchored `sora` matched
# `0_successorAEyF`, so `Software: Studio One` and `Software: Accessor` would
# each have been a blocking finding. A name that is also an English or Spanish
# word - runway, imagen, gemini, firefly, luma, pika, kling - carries the same
# anchors for the same reason.
GENERATOR_RE = re.compile(
    r"gpt-?image|\bdall[·.\- ]?e\b|openai|midjourney|stable\s?diffusion|sdxl|"
    r"comfyui|automatic1111|invokeai|\bfirefly\b|\bimagen\b|nano\s?banana|"
    r"flux\.1|ideogram|leonardo\.ai|\brunway(ml)?\b|\bsora\b|recraft|"
    r"playground\s?ai|seedream|dreamstudio|novelai|craiyon|"
    r"bing\s+image\s+creator|grok[\s\-]?imagine|synthid|content\s+credentials|"
    r"\bsuno\b|\budio\b|eleven\s?labs|\bpika\b|\bkling\b|\bveo\s?[0-9]|"
    r"\bluma\b|hailuo|minimax|\bgemini\b|\blyria\b|\bmochi\b|\bwan\s?[0-9]|"
    r"\bqwen[\s\-]image",
    re.IGNORECASE,
)

# Generators MEASURED or vendor-documented to embed a mark in the PIXELS while
# declaring nothing a strip can remove. Google's SynthID family is the whole
# list today: Imagen, Veo, Lyria and Gemini image output carry it, and its
# metadata footprint is IPTC digitalSourceType plus credit text - so removing
# every detectable signal leaves the mark and a green verdict. See UNCLEARABLE.
PIXEL_WATERMARK_RE = re.compile(
    r"synthid|google\s+deepmind|made\s+with\s+google\s+ai|"
    r"\bimagen\b|\bveo\s?[0-9]|\blyria\b|\bgemini\b|nano\s?banana",
    re.IGNORECASE,
)
# Both lists above are matched only against a generator FIELD's value, never
# against a whole blob: `openai/whisper-large-v3` is a legitimate model path in
# a compiled binary, and a scan of every binary's ASCII strings would refuse the
# push that carries it. Measured on this repository's whisperkit-cli.


def generator_rule(text):
    """Which of the three tiers a generator field's VALUE lands in."""
    if PIXEL_WATERMARK_RE.search(text):
        return "binary.pixel-watermark-known"
    if GENERATOR_RE.search(text):
        return "binary.generator-tool"
    return "binary.tool-tag"


WATERMARK_VALUE_RE = re.compile(
    r"c2pa\.watermarked|soft[_\-]?binding|watermark", re.IGNORECASE
)
SOURCE_TYPE_RE = re.compile(
    r"trainedalgorithmic|compositewithtrained|\balgorithmicmedia\b", re.IGNORECASE
)

# exiftool exits 0 on much of what it cannot parse, and `scan_exif` ignored its
# warnings entirely - so a PDF whose xref table is wrong reported `clean` while
# `qpdf` reconstructing that xref, which is what every viewer does on open,
# recovered `Producer: Midjourney v6`. A container the inspector could not read
# was never established clean, which is this gate's whole posture.
#
# Scoped to the classes that mean STRUCTURE, not content: exiftool's ordinary
# noise is a tag-level complaint and is prefixed `[minor]`, and refusing on
# those would red-light routine photographs.
PARSE_FAILURE_RE = re.compile(
    r"invalid|truncated|corrupt|error reading|bad (header|format|offset)|"
    r"premature|unsupported file|format error|not a valid|"
    r"missing (required )?(header|trailer|footer)|"
    r"ignored (structurally )?bad|unrecognized file",
    re.IGNORECASE,
)


def parse_failure(value):
    """The warnings that mean the file was not read, out of everything exiftool says."""
    out = []
    for w in value if isinstance(value, list) else [value]:
        text = w if isinstance(w, str) else json.dumps(w)
        if text.strip().startswith("[minor]"):
            continue
        if PARSE_FAILURE_RE.search(text):
            out.append(text.strip())
    return out


def rules_from_exif(record):
    """{rule} for one exiftool-shaped record. The suite's handle on the tiers."""
    return {f[0] for f in scan_exif(record)}


def split_advisory(hits):
    """(blocking, noted) - the only place the advisory tier is applied."""
    return (
        [h for h in hits if h[0] not in ADVISORY],
        [h for h in hits if h[0] in ADVISORY],
    )


def split_advisory_rows(rows):
    """The same split over the push gate's (path, sha, rule, ...) rows."""
    return (
        [r for r in rows if r[2] not in ADVISORY],
        [r for r in rows if r[2] in ADVISORY],
    )


# --- containers ---------------------------------------------------------------
#
# A marked asset inside a container was invisible to all three checks: deflate
# destroys the byte markers, exiftool reads only the outer file, and a text blob
# is never byte-scanned. Measured: a docx holding a trainedAlgorithmicMedia PNG,
# a PDF embedding a Midjourney JPEG as a /DCTDecode XObject, that PNG as a
# base64 data: URI in an HTML file, and an ICO wrapping the JPEG - all four
# reported `clean`. A STORED zip was caught, which is what made the hole look
# smaller than it was: one docx in the audit's own corpus was caught only
# because deflate fell back to stored blocks on a high-entropy member.
#
# Every bound below exists because this runs on every push: the walk is breadth
# first with a depth cap, a member cap and a byte budget, and anything past a
# bound is simply not expanded rather than being an error.
CONTAINER_MAX_DEPTH = 2
CONTAINER_MAX_MEMBERS = 512
CONTAINER_MAX_BYTES = 64 * 1024 * 1024
CONTAINER_MAX_MEMBER_BYTES = 16 * 1024 * 1024
PDF_MAX_STREAMS = 64
DATA_URI_MAX = 32
# Base64 CHARACTERS, so ~96 bytes decoded: below any real asset, and the cost of
# going lower is only a decode - the decoded bytes reach exiftool solely when
# they carry a media magic, which random base64 does not.
DATA_URI_MIN_CHARS = 128
EMBEDDED_MAX = 8
EMBEDDED_MIN_BYTES = 128


class ContainerOverflow(Exception):
    """A member decompressed past its bound before the bound could be checked.

    Raised from inside the container walkers themselves, at the point of
    decompression, so the bytes never fully materialize - unlike checking
    length after the fact, which is what let a small gzip/zip/deflate bomb
    force unbounded memory before any size guard applied.
    """


ZIP_MAGIC = b"PK\x03\x04"
GZIP_MAGIC = b"\x1f\x8b"
PDF_STREAM_RE = re.compile(rb"stream\r?\n")
# A base64 run long enough to be an asset rather than an inline icon or a hash.
# The separators a bundler leaves between two halves of one base64 string, and
# the most this will bridge. Applied BETWEEN two runs, never to the document:
# stripping whitespace globally turns ordinary prose into one enormous
# alphanumeric run, which is how a first attempt at this produced 932 findings
# in automation where 8 are real.
JOINER_RE = re.compile(r"^[\"\'\s,+\\)(]{1,8}$")
# Base64 of binary data is ~15.6% digits by construction and never 0; English
# prose is 0, and so is a long identifier run in minified source. Set low rather
# than near the true mean because the share is noisy on a SMALL payload - a real
# 103-byte PNG measures 0.079, which a 0.08 threshold rejects by 0.001.
B64_MIN_DIGIT_SHARE = 0.02
B64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{%d,}={0,2}" % DATA_URI_MIN_CHARS)
# A FRAGMENT is what a bundler leaves when it splits one base64 string, so the
# minimum length applies to the MERGED run and not to the pieces: a 140-char
# asset split in half is two 70-char fragments, and searching only for full
# runs found neither. 24 is above almost every English word, which keeps two
# adjacent words from bridging into a candidate.
B64_FRAGMENT_RE = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
# What a base64-encoded media SIGNATURE looks like, so a run can be decoded from
# the right place rather than from wherever the run happens to start. Joining
# `"data:image/png;base64," + "iVBOR..."` leaves the literal `base64` inside the
# run, and base64 aligns in fours - a six-character prefix shifts every decoded
# byte and the asset reads as noise. Anchoring on the payload's own first bytes
# is exact and costs one search.
B64_MEDIA_PREFIX = re.compile(
    r"iVBORw0KGgo"  # PNG
    r"|/9j/"  # JPEG
    r"|R0lGOD"  # GIF
    r"|UklGR"  # RIFF (webp, wav, avi)
    r"|JVBERi0"  # PDF
    r"|SUkqA"  # TIFF little-endian
    r"|TU0AK"  # TIFF big-endian
    r"|AAAA[A-Za-z0-9+/]{2}ZnR5c"  # ISO-BMFF ftyp (mp4, mov, heic, avif)
    r"|UEsDB"  # zip family
)
# Strong signatures only. A bare `\xff\xd8\xff` occurs by chance roughly once
# per 16MB of compressed data; requiring a real marker byte after it, a
# terminator, and a minimum size takes that to nothing on the corpus measured.
EMBEDDED_SIGNATURES = [
    (re.compile(rb"\xff\xd8\xff[\xdb\xe0-\xef\xc0\xc4]"), b"\xff\xd9", ".jpg"),
    (re.compile(rb"\x89PNG\r\n\x1a\n"), b"IEND\xaeB`\x82", ".png"),
]


def zip_members(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()[:CONTAINER_MAX_MEMBERS]
            for info in infos:
                # The declared size is the archive's OWN claim and is not
                # trustworthy as a bound - only as a cheap pre-filter to skip
                # opening a member that is honestly huge.
                if info.is_dir() or info.file_size > CONTAINER_MAX_MEMBER_BYTES:
                    continue
                try:
                    with archive.open(info) as member:
                        body = member.read(CONTAINER_MAX_MEMBER_BYTES + 1)
                except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError):
                    continue
                if len(body) > CONTAINER_MAX_MEMBER_BYTES:
                    raise ContainerOverflow(info.filename)
                yield info.filename, body, True
    except (zipfile.BadZipFile, OSError):
        return


def gzip_member(data):
    """The one member a gzip stream carries, and its tar entries if it is a tar.

    A marked asset gzipped was invisible: not a zip, not a PDF, not text, and
    deflate destroys the signature the carve looks for.
    """
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            body = gz.read(CONTAINER_MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        return
    if len(body) > CONTAINER_MAX_BYTES:
        raise ContainerOverflow("gzip")
    yield "gzip", body, True
    if len(body) > 262 and body[257:262] == b"ustar":
        for name, member in tar_members(body):
            yield name, member, True


def tar_members(body):
    try:
        with tarfile.open(fileobj=io.BytesIO(body)) as archive:
            for info in archive.getmembers()[:CONTAINER_MAX_MEMBERS]:
                if not info.isfile() or info.size > CONTAINER_MAX_MEMBER_BYTES:
                    continue
                handle = archive.extractfile(info)
                if handle is None:
                    continue
                content = handle.read(CONTAINER_MAX_MEMBER_BYTES + 1)
                if len(content) > CONTAINER_MAX_MEMBER_BYTES:
                    raise ContainerOverflow(info.name)
                yield info.name, content
    except (tarfile.TarError, OSError, EOFError):
        return


def pdf_streams(data):
    """Stream payloads worth re-scanning: raw for /DCTDecode, inflated for /Flate.

    A regex rather than a PDF parser, deliberately: this needs the BYTES of
    embedded images, not their structure, and a parser is a second thing to keep
    correct against files whose whole problem is that they are malformed.
    """
    n = 0
    for m in PDF_STREAM_RE.finditer(data):
        if n >= PDF_MAX_STREAMS:
            return
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        payload = data[m.end() : end].rstrip(b"\r\n")
        if not payload or len(payload) > CONTAINER_MAX_MEMBER_BYTES:
            continue
        n += 1
        yield "stream@%d" % m.start(), payload, False
        decompressor = zlib.decompressobj()
        try:
            inflated = decompressor.decompress(payload, CONTAINER_MAX_MEMBER_BYTES + 1)
        except zlib.error:
            continue
        if decompressor.unconsumed_tail:
            raise ContainerOverflow("stream@%d (inflated)" % m.start())
        if inflated:
            yield "stream@%d (inflated)" % m.start(), inflated, False


def base64_members(text):
    """Every long base64 run, not only the ones behind a `data:` URI.

    The URI prefix was doing no work as a filter and was doing real work as an
    evasion: a bundler writes the same asset as two quoted halves joined by `+`,
    and a JSON field carries it with no prefix at all. Both passed clean.
    Adjacent runs separated only by quotes, commas, `+` and whitespace are
    joined before decoding, which is exactly the concatenation pattern.

    Measured over 21,000 blobs of automation, firstmate and meld: ZERO runs
    decode to something carrying a media magic or a byte marker, so the cost of
    dropping the prefix requirement is a decode and nothing else.
    """
    out, seen = [], set()

    def take(where, run):
        if len(run) < DATA_URI_MIN_CHARS or where in seen:
            return
        digits = sum(ch.isdigit() for ch in run)
        if digits < B64_MIN_DIGIT_SHARE * len(run):
            return
        seen.add(where)
        try:
            out.append(
                (
                    "base64@%d" % where,
                    base64.b64decode(run + "=" * (-len(run) % 4), validate=True),
                    # SPECULATIVE: this is a decode we guessed at, not a member
                    # any index named, so only a structural marker in it counts
                    # as evidence. See `attribute`.
                    False,
                )
            )
        except (ValueError, binascii.Error):
            return

    # Bridge two runs separated by nothing but quotes, whitespace and a `+` -
    # exactly what a bundler leaves when it splits one string in half.
    spans = [(m.start(), m.end(), m.group(0)) for m in B64_FRAGMENT_RE.finditer(text)]
    merged, i = [], 0
    while i < len(spans):
        start, end, run = spans[i]
        while i + 1 < len(spans) and JOINER_RE.match(text[end : spans[i + 1][0]]):
            run += spans[i + 1][2]
            end = spans[i + 1][1]
            i += 1
        merged.append((start, run))
        i += 1

    for i, (start, run) in enumerate(merged):
        if i >= DATA_URI_MAX:
            break
        take(start, run)
        # And again from the payload's own signature, wherever inside the run it
        # begins - which is what a concatenated `data:` URI leaves behind, since
        # the bridged run still carries the literal `base64` from the prefix and
        # base64 aligns in fours.
        sig = B64_MEDIA_PREFIX.search(run)
        if sig and sig.start():
            take(start + sig.start(), run[sig.start() :])
    return out


def embedded_media(data):
    """Whole images carried inside another binary - an ICO's payload, say.

    This is what covers a container nobody wrote a reader for: the asset is
    found by its own signature rather than by the wrapper's format.
    """
    n = 0
    for pattern, terminator, ext in EMBEDDED_SIGNATURES:
        for m in pattern.finditer(data, 1):
            if n >= EMBEDDED_MAX:
                return
            end = data.find(terminator, m.end())
            if end < 0 or end - m.start() < EMBEDDED_MIN_BYTES:
                continue
            n += 1
            yield (
                "embedded@%d%s" % (m.start(), ext),
                data[m.start() : end + len(terminator)],
                False,
            )


def container_members(path, data, text):
    """[(member name, bytes, declared)] one level down, whatever the container is.

    `declared` separates a member the container's own index names - a zip entry,
    a data: URI - from bytes CARVED out by a signature search. A carve that
    lands on compressed noise is not a file anyone shipped, so a parse failure
    there is an artefact of the carve rather than a finding; see `attribute`.
    """
    if data.startswith(ZIP_MAGIC):
        return list(zip_members(data))
    if data.startswith(b"%PDF-"):
        return list(pdf_streams(data))
    if data.startswith(GZIP_MAGIC):
        return list(gzip_member(data))
    if text is not None:
        return base64_members(text)
    return list(embedded_media(data))


def is_media(path, head):
    if os.path.splitext(path)[1].lower() in MEDIA_EXT:
        return True
    if any(head.startswith(m) for m in MEDIA_MAGIC):
        return True
    return len(head) >= 12 and head[4:8] == b"ftyp"


def scan_bytes(data):
    return [
        (rule, label, marker.decode("ascii", "replace"), 0)
        for marker, rule, label in BYTE_MARKERS
        if marker in data
    ]


# An ID3 TXXX frame carries its own field name, and the two exiftool versions in
# the fleet disagree about where they put it: 13.55 promotes the description to
# the tag (`ID3v2_4:Comment`), 12.76 reports `ID3v2_4:UserDefinedText` with the
# description parenthesised in the VALUE. Field scoping reads the tag, so the
# same file was blocking here and clean on ubuntu-24.04 - measured on an mp3
# ffmpeg 6.1.1 wrote, which routes `comment` to TXXX where 9.0.1 writes COMM.
# Folding the description back into the tag name makes both agree, and keeps the
# scoping honest: a TXXX is judged by the field it declares itself to be.
USER_DEFINED_RE = re.compile(r"^\(([^)]{1,64})\)\s*(.*)$", re.DOTALL)


def normalise_user_defined(low, text):
    """(tag, value) with a TXXX frame's own description promoted to the tag."""
    if low != "userdefinedtext":
        return low, text
    m = USER_DEFINED_RE.match(text.strip())
    if not m:
        # No description to read. Keep it a generator field rather than dropping
        # it: an unnamed tool-written frame is still a tool-written frame.
        return "comment", text
    desc, rest = m.group(1), m.group(2)
    return desc.lower().replace("-", "").replace("_", "").replace(" ", ""), rest


def scan_exif(record):
    """Findings from one exiftool JSON record."""
    found = []
    for key, value in record.items():
        if key in ("SourceFile", "ExifTool:ExifToolVersion"):
            continue
        group, _, tag = key.rpartition(":")
        if tag in ("Warning", "Error"):
            for w in parse_failure(value):
                found.append(("binary.unreadable", w[:120], key, 0))
            continue
        low = tag.lower().replace("-", "").replace("_", "")
        text = value if isinstance(value, str) else json.dumps(value)
        low, text = normalise_user_defined(low, text)
        if group.upper() == "JUMBF" or "c2pa" in low or "c2pa" in text.lower():
            found.append(("binary.c2pa-manifest", "C2PA/JUMBF metadata", key, 0))
        if "digitalsourcetype" in low and SOURCE_TYPE_RE.search(text):
            found.append(("binary.digital-source-type", text.strip(), key, 0))
        if WATERMARK_VALUE_RE.search(text) and (
            "action" in low or "watermark" in low or "binding" in low
        ):
            found.append(("binary.watermark-assertion", text.strip(), key, 0))
        if low in ATTRIBUTIVE_FIELDS:
            # Authorship, copyright and licensing terms. Reported only when the
            # VALUE is detective, because the field itself is what a reader uses.
            rule = generator_rule(text)
            if rule != "binary.tool-tag":
                found.append((rule, text.strip()[:120], key, 0))
        elif low in GENERATOR_PRESENCE_FIELDS:
            rule = (
                "binary.pixel-watermark-known"
                if low == "synthid"
                else "binary.generator-tool"
            )
            found.append((rule, "%s chunk present" % tag, key, 0))
        elif any(f in low for f in GENERATOR_FIELDS) and text.strip():
            found.append((generator_rule(text), text.strip()[:120], key, 0))
    # One artefact routinely trips several rules at once; report each rule once.
    seen, out = set(), []
    for f in found:
        if (f[0], f[1]) in seen:
            continue
        seen.add((f[0], f[1]))
        out.append(f)
    return out


# --- allowlist ----------------------------------------------------------------


class Allow:
    """Parsed allowlist. Every entry must name its reason, in the entry itself.

    An asset the captain has decided to keep, or a legitimate hit a structural
    exemption cannot express, stays VISIBLE here rather than being silently
    excused: the reason is printed on every push that touches the file.
    """

    def __init__(self, path):
        self.text = []  # (repo glob, path glob, {codepoints}, reason)
        self.blob = []  # (repo glob, blob sha, path glob, reason)
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                body, _, reason = raw.partition("#")
                body, reason = body.strip(), reason.strip()
                if not body:
                    continue
                fields = body.split()
                if not reason:
                    refuse(
                        "%s:%d: an allowlist entry must name its reason after '#'."
                        % (path, n)
                    )
                if len(fields) != 4:
                    refuse("%s:%d: expected 4 fields, got %d." % (path, n, len(fields)))
                kind, repo, key, detail = fields
                if kind == "text":
                    # Kept as ranges rather than expanded into a set: a legitimate
                    # entry can span the whole invisible surface, and expanding
                    # that is a million-element set built on every push.
                    points = []
                    for spec in detail.split(","):
                        bounds = spec.split("-")
                        try:
                            lo = int(bounds[0].replace("U+", ""), 16)
                            hi = int(bounds[-1].replace("U+", ""), 16)
                        except ValueError:
                            refuse(
                                "%s:%d: '%s' is not a U+XXXX codepoint or range."
                                % (path, n, spec)
                            )
                        points.append((lo, hi))
                    self.text.append((repo, key, points, reason))
                elif kind == "blob":
                    self.blob.append((repo, key, detail, reason))
                else:
                    refuse(
                        "%s:%d: unknown entry kind '%s' (expected text or blob)."
                        % (path, n, kind)
                    )

    @staticmethod
    def _match(glob, value):
        return fnmatch.fnmatchcase(value, glob)

    def text_reason(self, identity, path, codepoint):
        """identity None matches any repository - see `local_allow` below."""
        for repo, pat, points, reason in self.text:
            if identity is not None and not self._match(repo, identity):
                continue
            if not self._match(pat, path):
                continue
            if any(lo <= codepoint <= hi for lo, hi in points):
                return reason
        return None

    def blob_reason(self, identity, sha, path):
        for repo, key, pat, reason in self.blob:
            if identity is not None and not self._match(repo, identity):
                continue
            if key == sha and self._match(pat, path):
                return reason
        return None


# --- plumbing -----------------------------------------------------------------


def refuse(msg):
    sys.stderr.write("%s: %s\n" % (TOOL, msg))
    sys.stderr.write(
        "%s: COULD NOT ESTABLISH that this push is clean. Push refused.\n" % TOOL
    )
    sys.exit(1)


def die(msg):
    sys.stderr.write("%s: %s\n" % (TOOL, msg))
    sys.exit(2)


def git(*args):
    p = subprocess.run(["git"] + list(args), capture_output=True)
    if p.returncode != 0:
        refuse(
            "git %s failed: %s"
            % (" ".join(args), p.stderr.decode("utf-8", "replace").strip())
        )
    return p.stdout


def need_exiftool():
    """exiftool is the binary inspector AND the binary writer.

    Its absence is a refusal rather than a silent skip: a push nobody inspected
    must not look like a clean one, and a `clean` that removed nothing must not
    look like one that removed everything.
    """
    return (
        subprocess.run(
            ["sh", "-c", "command -v exiftool"], capture_output=True
        ).returncode
        == 0
    )


def run_exiftool(files, strict=True):
    """One exiftool invocation over every media artefact; one JSON record per file.

    `strict` is the difference between a FILE and a container MEMBER. A file in
    the tree that exiftool could not read is a refusal - that is this gate's
    posture and it has not changed. A member is bytes we unpacked or carved out
    ourselves, and exiftool exits 1 for the whole batch when any one of them is
    not an image it knows; refusing there would turn the first zip in any
    repository into an unpushable one. So a member's missing record is returned
    as None and `inspect_items` decides what it means, which is a finding for a
    member the container's own index NAMED and nothing for a carve.
    """
    cmd = [
        "exiftool",
        "-json",
        "-a",
        "-G1",
        "-n",
        "-charset",
        "filename=utf8",
        "-x",
        "OcspVals",
        "-x",
        "ThumbnailImage",
        "-x",
        "PreviewImage",
        "-x",
        "Directory",
        "-x",
        "FileName",
        "-x",
        "FileModifyDate",
        "-x",
        "FileAccessDate",
        "-x",
        "FileInodeChangeDate",
        "--",
    ] + files
    p = subprocess.run(cmd, capture_output=True)
    if strict and p.returncode != 0:
        refuse(
            "exiftool exited %d: %s"
            % (p.returncode, p.stderr.decode("utf-8", "replace").strip())
        )
    try:
        records = json.loads(p.stdout.decode("utf-8", "replace") or "[]")
    except ValueError as exc:
        refuse("exiftool did not return parseable JSON (%s)." % exc)
    # The same witness the gitleaks gate keeps on its commit count: a tool that
    # read fewer files than it was handed has not cleared the ones it skipped.
    if strict:
        if len(records) != len(files):
            refuse(
                "exiftool returned %d records for %d files."
                % (len(records), len(files))
            )
        return records
    by_file = {r.get("SourceFile"): r for r in records}
    return [by_file.get(f) for f in files]


# A leading byte-order mark, longest first: the UTF-32LE mark starts with the
# whole UTF-16LE mark, so testing in the other order reads every UTF-32 file as
# UTF-16 and finds a channel that is not there.
BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)

# Real text carries almost no C0 control other than tab, newline and return. A
# binary that happens to open with two BOM-shaped bytes decodes into a wall of
# them, and scanning that garbage would invent findings - a stray FE FF pair
# anywhere in it decodes to U+FEFF, which is a rule.
CONTROL_LIMIT = 0.05


def _too_much_control(text):
    if not text:
        return False
    head = text[:4096]
    n = sum(1 for ch in head if ch < " " and ch not in "\t\n\r")
    return n > CONTROL_LIMIT * len(head)


def decode_text(data):
    """(text, status). status is 'text', 'binary' or 'undecodable'.

    `undecodable` is the correction for the evasion this used to return None on:
    one non-UTF-8 byte anywhere in a text file routed the whole blob to the
    byte-marker scan, which knows no codepoints, and the file then reported
    `clean` - an evasion costing one byte whose output was indistinguishable
    from a genuinely unmarked file. Three answers, none of them silence:

      a byte-order mark is honoured, so a UTF-16 file is scanned as the text it
      is rather than as a binary full of NULs;

      an invalid byte with no NUL is decoded with `surrogateescape`, which keeps
      every VALID UTF-8 sequence - so the codepoint scan still runs over exactly
      what a UTF-8 reader would see, and the bad bytes become lone surrogates
      that are in no detection range;

      and either way the blob is additionally reported `undecodable`, because a
      decoder that could not read the file in full has not established that the
      file is clean, and this gate refuses what it cannot establish.
    """
    for bom, enc in BOMS:
        if data.startswith(bom):
            try:
                text = data.decode(enc)
            except (UnicodeDecodeError, ValueError):
                return None, "undecodable"
            return (None, "binary") if _too_much_control(text) else (text, "text")
    if b"\x00" in data:
        text = utf16_without_bom(data)
        return (text, "text") if text is not None else (None, "binary")
    try:
        return data.decode("utf-8"), "text"
    except UnicodeDecodeError:
        return data.decode("utf-8", "surrogateescape"), "undecodable"


def utf16_without_bom(data):
    """The decoded text if this is UTF-16 carrying no byte-order mark, else None.

    Requiring the mark left an evasion worth one saved byte: the same file
    without it is NUL-heavy, so it took the binary path and its codepoints were
    never scanned. Guessing is what has to be avoided, not detecting - a binary
    holding one stray FE FF pair decodes to U+FEFF, which is a rule, so the test
    is deliberately narrow: a quarter of the bytes NUL, every one of them at the
    SAME parity, a clean decode, and the result not a wall of control
    characters. Measured over 21,000 blobs of automation, firstmate and meld: 0
    binaries satisfy it.
    """
    if len(data) < 8 or len(data) % 2:
        return None
    head = data[:8192]
    nuls = [i for i, b in enumerate(head) if b == 0]
    if len(nuls) < len(head) * 0.25:
        return None
    parity = {i % 2 for i in nuls}
    if len(parity) != 1:
        return None
    try:
        text = data.decode("utf-16-le" if parity == {1} else "utf-16-be")
    except (UnicodeDecodeError, ValueError):
        return None
    return None if _too_much_control(text) else text


def undecodable_reason(data):
    """Where the decode failed, so the refusal names a byte rather than a file."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return "byte 0x%02X at offset %d (%s)" % (
            data[exc.start],
            exc.start,
            exc.reason,
        )
    return "no decoder read this blob in full"


def is_text(data):
    """The decoded text, or None. The one-answer form the `clean` writers want."""
    text, _ = decode_text(data)
    return text


def inspect_items(items):
    """{key: [(rule, label, where, line)]} for [(key, path, data)].

    Breadth-first over containers: every artefact is scanned by the three checks
    (codepoints, byte markers, exiftool), then unpacked one level and its
    members queued. One batched exiftool call per level, so `push` over a first
    push of a large repository and `inspect` over one file share exactly the
    same rules at every depth.

    A member's findings are attributed to the blob that CARRIES it, with the
    member named in the label - the allowlist grades a repository path, and a
    path inside a docx is not one.
    """
    found = {key: [] for key, _, _ in items}
    tmp = tempfile.mkdtemp(prefix="watermark-scan.")
    try:
        queue = [(key, path, data, "", True) for key, path, data in items]
        budget = {key: CONTAINER_MAX_BYTES for key, _, _ in items}
        for depth in range(CONTAINER_MAX_DEPTH + 1):
            media, nxt, seq = [], [], 0
            for key, path, data, member, declared in queue:
                media_blob = is_media(path, data[:16])
                text, status = decode_text(data)
                hits = []
                if not declared:
                    # A carve or a speculative decode: bytes we found rather than
                    # bytes any index named. Only a structural marker in them is
                    # evidence - running the codepoint scan over arbitrary
                    # decoded noise invents findings, which is what a first
                    # attempt at this did 1,420 times in one repository.
                    hits.extend(scan_bytes(data))
                elif status == "undecodable":
                    # Both scans, because this blob is neither cleanly text nor
                    # cleanly binary: the codepoint scan over what a UTF-8
                    # reader would see, and the byte markers the text branch
                    # skips.
                    hits.extend(scan_text(text or ""))
                    hits.extend(scan_bytes(data))
                    # A media blob is not expected to decode, and exiftool is
                    # its inspector - saying `undecodable` about every JPEG
                    # would drown the one case this exists for.
                    if not media_blob:
                        hits.append(
                            (
                                "encoding.undecodable",
                                undecodable_reason(data),
                                "blob",
                                0,
                            )
                        )
                elif text is not None:
                    hits.extend(scan_text(text))
                else:
                    hits.extend(scan_bytes(data))
                found[key].extend(attribute(hits, member, declared))
                if media_blob:
                    seq += 1
                    name = os.path.join(
                        tmp,
                        "%d-%d%s" % (depth, seq, os.path.splitext(path)[1].lower()),
                    )
                    with open(name, "wb") as fh:
                        fh.write(data)
                    media.append((name, key, member, declared))
                if depth >= CONTAINER_MAX_DEPTH:
                    # The bound has to REFUSE rather than pass, or it is just a
                    # deeper place to hide the same asset: a zip nested one level
                    # past the cap reported `clean` with the marked PNG intact
                    # inside it. Raising the cap only moves that; saying "this
                    # gate did not open it" is the gate's own posture and closes
                    # the class. Measured: 0 nested archives across 21,000 blobs
                    # of automation, firstmate and meld, so nothing real pays.
                    if declared:
                        try:
                            has_members = bool(container_members(path, data, text))
                        except ContainerOverflow:
                            has_members = True
                        if has_members:
                            found[key].append(
                                (
                                    "container.unexamined",
                                    "nested more than %d levels deep"
                                    % CONTAINER_MAX_DEPTH,
                                    "member",
                                    0,
                                )
                            )
                else:
                    # A member that cannot be expanded within its budget is the
                    # same posture as one nested past the depth cap: refuse
                    # rather than silently drop it, or a decompression bomb
                    # just needs one level of container to hide the same asset
                    # the depth cap already guards against.
                    try:
                        sub_members = container_members(path, data, text)
                    except ContainerOverflow:
                        found[key].append(
                            (
                                "container.unexamined",
                                "a member could not be expanded within its budget",
                                "member",
                                0,
                            )
                        )
                        sub_members = []
                    for sub_name, sub_data, sub_declared in sub_members:
                        if len(sub_data) > budget[key]:
                            continue
                        budget[key] -= len(sub_data)
                        nxt.append(
                            (
                                key,
                                sub_name,
                                sub_data,
                                "%s > %s" % (member, sub_name) if member else sub_name,
                                declared and sub_declared,
                            )
                        )
            if media:
                # Indexed rather than zipped: run_exiftool already refuses a
                # short record list, and a zip would silently truncate to it
                # instead - the same defect one layer up. `strict=True` would
                # say so too, but it needs Python 3.10 and nothing else here
                # does.
                records = run_exiftool([m[0] for m in media], strict=(depth == 0))
                for i, (_name, key, member, declared) in enumerate(media):
                    if records[i] is None:
                        # Only reachable for a member; a top-level short list
                        # refused inside run_exiftool.
                        if declared:
                            found[key].append(
                                (
                                    "binary.unreadable",
                                    "exiftool could not read %s" % member,
                                    "member",
                                    0,
                                )
                            )
                        continue
                    found[key].extend(
                        attribute(scan_exif(records[i]), member, declared)
                    )
            if not nxt:
                break
            queue = nxt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return found


def attribute(hits, member, declared=True):
    """Name the container member a finding came from, in the LABEL.

    Not in `where`, which the allowlist parses as a codepoint for every text
    rule, and not in the path, which is the repository path an allowlist entry
    is written against.

    A CARVED member drops every UNSCANNABLE finding: those bytes were found by a
    signature search rather than named by any index, so "this did not parse" and
    "no decoder read this in full" both say the carve was wrong rather than
    anything about the artefact. Measured on a qpdf-rewritten PDF, whose Flate
    content stream is carved raw and is not valid UTF-8 by construction.
    """
    if not declared:
        hits = [h for h in hits if h[0] not in UNSCANNABLE]
    if not member:
        return hits
    return [
        (rule, "%s (in %s)" % (label, member), where, line)
        for rule, label, where, line in hits
    ]


def counts(items):
    n_text = sum(1 for _, _, data in items if decode_text(data)[1] == "text")
    n_media = sum(1 for _, path, data in items if is_media(path, data[:16]))
    return n_text, len(items) - n_text, n_media


# --- the push gate -------------------------------------------------------------


def enumerate_objects(ranges):
    """([(blob sha, [path...])], [name], [commit sha]) for the pushed ranges.

    EVERY path per blob, not the first one seen. The allowlist is graded per
    path, so recording one path for a sha handed the whole blob whatever verdict
    that one path earned: the same marked content committed at an allowlisted
    path AND at `zz-evil/copy.zsh` passed the push at rc=0 with only the
    allowlisted path printed, and the same pair with the copy at `evil/copy.zsh`
    - which sorts first - refused and stripped the legitimate path of its
    exemption. Both directions were measured; enumeration order decided which.

    `names` is every path git names in these ranges - blobs, trees and submodule
    gitlinks alike - so a filename carrying a channel is scanned even when its
    content is clean.
    """
    listing = []
    for spec in ranges:
        listing.append(git("rev-list", "--objects", *spec.split()))
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(rest)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate(b"".join(listing))
    if proc.returncode != 0:
        refuse(
            "git cat-file --batch-check failed: %s"
            % err.decode("utf-8", "replace").strip()
        )
    blobs, paths, names, commits = [], {}, [], []
    tags = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 2:
            continue
        sha, kind = parts[0], parts[1]
        path = parts[2] if len(parts) == 3 else ""
        if kind == "commit":
            commits.append(sha)
            continue
        if kind == "tag":
            # An ANNOTATED tag carries a message of its own, and it rides the
            # same push. It was scanned by nothing: the walk filtered to blobs,
            # and the commit pass only ever saw commit objects.
            tags.append(sha)
            continue
        if path and path not in names:
            names.append(path)
        if kind != "blob" or not path:
            continue
        if sha not in paths:
            paths[sha] = []
            blobs.append(sha)
        if path not in paths[sha]:
            paths[sha].append(path)

    # `rev-list --objects` prints each object ONCE, so the loop above cannot see
    # a second path on its own - the dedupe is git's, not ours. The range's own
    # diffs name every path every change touched, which is the complete answer
    # and costs 0.18s over meld's 5,842 commits against a 27s scan.
    for sha, path in diff_paths(ranges):
        if path not in names:
            names.append(path)
        if sha in paths and path not in paths[sha]:
            paths[sha].append(path)
    return [(sha, paths[sha]) for sha in blobs], names, commits + tags


def diff_paths(ranges):
    """[(new blob sha, path)] for every change the pushed ranges make.

    NUL-delimited, because a path is bytes: `core.quotePath` escapes non-ASCII
    by default, which is how a filename carrying a zero-width space arrived here
    as the seven ASCII characters `\342\200\213` and matched nothing.
    """
    out = []
    for spec in ranges:
        raw = git(
            "log",
            "--format=%H",
            "--raw",
            "--no-abbrev",
            "--root",
            "--diff-merges=first-parent",
            "-z",
            *spec.split(),
        )
        fields = raw.split(b"\x00")
        i = 0
        while i < len(fields):
            head = fields[i].lstrip(b"\n")
            if not head.startswith(b":"):
                i += 1
                continue
            parts = head.split(b" ")
            if len(parts) < 5 or i + 1 >= len(fields):
                i += 1
                continue
            status = parts[4]
            # A rename or copy is followed by BOTH paths; the new object is at
            # the second. Rename detection is off by default here, so this is
            # defensive rather than routine.
            npaths = 2 if status[:1] in (b"R", b"C") else 1
            path = fields[i + npaths]
            if path:
                out.append(
                    (
                        parts[3].decode("ascii", "replace"),
                        path.decode("utf-8", "surrogateescape"),
                    )
                )
            i += 1 + npaths
    return out


def read_commit_messages(shas):
    """{sha: message} for the commits in this push, read as objects.

    The message is everything after the header's blank line, so this needs no
    pretty-format and cannot be truncated by one. A machine-readable channel in
    a commit message is the cheapest place to put one and was scanned by
    nothing: `enumerate_objects` walks the same ranges the blob scan does.
    """
    if not shas:
        return {}
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def feed():
        try:
            proc.stdin.write(b"".join(s.encode() + b"\n" for s in shas))
            proc.stdin.close()
        except BrokenPipeError:
            pass

    threading.Thread(target=feed, daemon=True).start()
    messages = {}
    for sha in shas:
        header = proc.stdout.readline().decode("utf-8", "replace").split()
        # `tag` as well as `commit`: an annotated tag object has the same
        # header-blank-line-message shape and rides the same push.
        if len(header) != 3 or header[1] not in ("commit", "tag"):
            refuse(
                "git cat-file --batch did not return a commit or tag for %s (%s)."
                % (sha, " ".join(header) or "no header")
            )
        size = int(header[2])
        body = proc.stdout.read(size)
        if len(body) != size:
            refuse(
                "git cat-file --batch returned %d of %d bytes for commit %s."
                % (len(body), size, sha)
            )
        proc.stdout.read(1)
        _, _, message = body.partition(b"\n\n")
        messages[sha] = message.decode("utf-8", "surrogateescape")
    proc.wait()
    return messages


def read_blobs(blobs):
    """Stream the contents back in one `git cat-file --batch`, in request order."""
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def feed():
        try:
            proc.stdin.write(b"".join(sha.encode() + b"\n" for sha, _ in blobs))
            proc.stdin.close()
        except BrokenPipeError:
            pass

    # A writer thread rather than one big write: the request list and the
    # response stream both exceed the pipe buffer on any real push, so writing
    # it all up front before reading anything deadlocks.
    threading.Thread(target=feed, daemon=True).start()
    for sha, path in blobs:
        header = proc.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3 or header[1] != "blob":
            refuse(
                "git cat-file --batch did not return blob %s (%s)."
                % (sha, " ".join(header) or "no header")
            )
        size = int(header[2])
        data = proc.stdout.read(size)
        if len(data) != size:
            refuse(
                "git cat-file --batch returned %d of %d bytes for %s."
                % (len(data), size, sha)
            )
        proc.stdout.read(1)
        yield sha, path, data
    proc.wait()


def opted_out(identity, path):
    """The reason this repository is exempt, or None. Refuses a reasonless entry.

    Parsed here rather than in the shell hook so that a malformed entry gets the
    same treatment as a malformed allowlist entry: a refusal naming the line,
    not a silent widening of the exemption.
    """
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            body, _, reason = raw.partition("#")
            body, reason = body.strip(), reason.strip()
            if not body:
                continue
            if not reason:
                refuse(
                    "%s:%d: an opt-out entry must name its reason after '#'."
                    % (path, n)
                )
            if len(body.split()) != 1:
                refuse(
                    "%s:%d: expected one glob before the reason, got '%s'."
                    % (path, n, body)
                )
            if identity and fnmatch.fnmatchcase(identity, body):
                return reason
    return None


def cmd_push(argv):
    # None means the hook did not set it, which is a wiring failure. Empty means
    # a repository with no origin - still this machine's work, still scanned.
    identity = os.environ.get("WATERMARK_IDENTITY")
    if identity is None:
        refuse("WATERMARK_IDENTITY is unset (the machine-wide pre-push hook sets it).")
    optout_path = os.environ.get("WATERMARK_OPTOUT")
    if not optout_path or not os.access(optout_path, os.R_OK):
        refuse("cannot read the opt-out list at %s." % (optout_path or "<unset>"))
    why = opted_out(identity, optout_path)
    if why:
        print("%s: %s is opted out - %s" % (TOOL, identity, why))
        return 0
    allow_path = os.environ.get("WATERMARK_ALLOW")
    if not allow_path or not os.access(allow_path, os.R_OK):
        refuse("cannot read the allowlist at %s." % (allow_path or "<unset>"))
    allow = Allow(allow_path)
    if not need_exiftool():
        refuse(
            "exiftool is not installed (brew install exiftool), so no binary can be inspected."
        )

    remote = argv[0] if argv else "origin"
    ranges = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _, local_sha, _, remote_sha = parts[:4]
        if local_sha == ZERO:
            continue
        ranges.append(
            "%s --not --remotes=%s" % (local_sha, remote)
            if remote_sha == ZERO
            else "%s..%s" % (remote_sha, local_sha)
        )
    if not ranges:
        print("%s: nothing to examine (no ref in this push introduces objects)." % TOOL)
        return 0

    blobs, names, commits = enumerate_objects(ranges)
    items = [
        (sha, path, data)
        for sha, path, data in read_blobs([(s_, p_[0]) for s_, p_ in blobs])
    ]
    if len(items) != len(blobs):
        refuse("examined %d of the %d blob(s) in this push." % (len(items), len(blobs)))
    paths = {sha: p_ for sha, p_ in blobs}
    found = inspect_items(items)

    findings, allowed = [], []
    for sha, hits in found.items():
        for rule, label, where, line in hits:
            # Once per PATH this content sits at: the allowlist is a statement
            # about a path, and one blob can be at several.
            for path in paths[sha]:
                reason = (
                    allow.text_reason(identity, path, int(where[2:], 16))
                    if rule.startswith("text.")
                    else allow.blob_reason(identity, sha, path)
                )
                (allowed if reason else findings).append(
                    (path, "blob %s" % sha[:12], rule, label, where, line, reason)
                )

    # Names and messages, scanned with the same codepoint engine. Neither is
    # blob CONTENT, so neither was ever examined - a filename carrying a
    # zero-width space and a commit message carrying a Unicode-tag channel both
    # rode a push through at rc=0.
    for name in names:
        # `_line` because a filename has no line number to report - the finding
        # is the tree entry itself.
        for rule, label, where, _line in scan_text(name):
            reason = allow.text_reason(identity, name, int(where[2:], 16))
            (allowed if reason else findings).append(
                (name, "filename", rule, label, where, 0, reason)
            )
    for sha, message in read_commit_messages(commits).items():
        for rule, label, where, line in scan_text(message):
            # No path to grade against, so no allowlist entry can match one -
            # deliberate: a message is written fresh with every commit, and an
            # exemption for one is an exemption for every future message.
            findings.append(
                (
                    "(commit message)",
                    "commit %s" % sha[:12],
                    rule,
                    label,
                    where,
                    line,
                    None,
                )
            )
    findings, noted = split_advisory_rows(findings)

    # Grouped, so one allowlisted artefact is one line and its reason is stated
    # once: a signed manifest trips four or five rules at a time, and printing
    # each of them separately buries the reason it is there to keep visible.
    grouped = {}
    for path, origin, rule, _label, _where, _line, reason in allowed:
        grouped.setdefault((path, origin, reason), set()).add(rule)
    for (path, origin, reason), rules in sorted(grouped.items()):
        print(
            "%s: ALLOWED  %s (%s)  %s" % (TOOL, path, origin, ", ".join(sorted(rules)))
        )
        print("%s:          %s" % (TOOL, reason))

    # Printed whether or not anything blocks, and BEFORE the verdict: the whole
    # correction is that a tag the gate examined and chose not to refuse on must
    # still be visible, rather than disappearing into a `clean` line.
    for path, origin, rule, label, where, _line, _reason in sorted(set(noted)):
        print(
            "%s: NOTED    %s (%s)  %s  %s [%s]"
            % (TOOL, path, origin, rule, label, where)
        )

    if findings:
        sys.stderr.write(
            "%s: DETECTIVE PROVENANCE in the commits being pushed. Push refused.\n\n"
            % TOOL
        )
        by_file = {}
        for path, origin, rule, label, where, line, _ in sorted(set(findings)):
            by_file.setdefault((path, origin), []).append((rule, label, where, line))
        for (path, origin), hits in sorted(by_file.items()):
            sys.stderr.write("  %s  (%s)\n" % (path, origin))
            for rule, label, where, line in hits:
                at = " line %d" % line if line else ""
                sys.stderr.write("    %-28s %s [%s]%s\n" % (rule, label, where, at))
            if any(h[0] in UNCLEARABLE for h in hits):
                sys.stderr.write(
                    "    -> "
                    + unverifiable_notice(
                        {h[0] for h in hits if h[0] in UNCLEARABLE},
                        sorted({h[1] for h in hits if h[0] in UNCLEARABLE}),
                    ).replace("\n", "\n       ")
                    + "\n"
                )
        if any(o == "filename" for _, o, *_ in findings):
            sys.stderr.write(
                "\n%s: a finding in a FILENAME is fixed by renaming the file, not by `clean` -\n"
                "%s: the codepoint is in the tree entry rather than in any blob's content.\n"
                % (TOOL, TOOL)
            )
        if any(o.startswith("commit ") for _, o, *_ in findings):
            sys.stderr.write(
                "\n%s: a finding in a COMMIT MESSAGE is fixed by rewording it - `git commit --amend`\n"
                "%s: for the tip, `git rebase -i` further back. There is no allowlist entry for a\n"
                "%s: message: an entry would excuse every message a path ever carries.\n"
                % ((TOOL,) * 3)
            )
        sys.stderr.write(
            "\n%s: the remedy is `watermark-scan.py clean <path>`, then commit and push again.\n"
            "%s: this gate never rewrites what you are pushing - a hook that mutated a commit\n"
            "%s: would be rewriting history behind you, which is worse than the finding.\n"
            "%s: attributive provenance is never flagged - cited sources, authorship, measured\n"
            "%s: claims, commit trailers. What is refused above is a signal whose purpose is to\n"
            "%s: let a MACHINE classify this artefact as machine-made. A deliberate exception\n"
            "%s: belongs in the allowlist, with its reason named.\n" % ((TOOL,) * 7)
        )
        return 1

    n_text, n_binary, n_media = counts(items)
    print(
        "%s: examined %d blob(s) - %d text, %d binary, %d inspected with exiftool - "
        "across %d range(s). No detective provenance found "
        "(%d allowlisted, %d tool tag(s) noted)."
        % (
            TOOL,
            len(items),
            n_text,
            n_binary,
            n_media,
            len(ranges),
            len(grouped),
            len(set(noted)),
        )
    )
    return 0


# --- the file verbs: inspect, clean, verify ------------------------------------


def local_allow():
    """The allowlist as the file verbs use it: path and content, not repository.

    The repository field is authoritative in `push`, where the hook hands the
    normalised origin identity over for free. A local `clean` has no such value
    without a second implementation of that normalisation, and a second
    implementation is what drifts - so the repo field is wildcarded here
    instead. The direction is safe: over-matching makes `clean` leave a file
    ALONE, never strip more of one.

    It has to be honoured at all because the allowlist is where legitimate uses
    live. Without it, `clean` on automation's own filename-safety detector would
    delete the bidi codepoints out of the character class that detector exists
    to match.
    """
    path = os.environ.get("WATERMARK_ALLOW") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "watermark-allow.conf"
    )
    if not os.access(path, os.R_OK):
        die("cannot read the allowlist at %s." % path)
    return Allow(path)


_TOPLEVEL = {}


def repo_relative(path):
    """The path as the allowlist writes it: relative to the work tree root.

    An allowlist entry names a repository-relative path, because that is the
    only form the push gate ever sees. A local `clean file.zsh` run from inside
    a subdirectory hands over something else entirely, and the entry would then
    silently fail to match - stripping the very codepoints it exists to keep.
    git is asked rather than guessed at, and the answer is cached per directory.
    """
    # realpath on both sides: `git rev-parse --show-toplevel` answers with the
    # physical path, so on macOS a file under $TMPDIR is /var/folders/... to
    # Python and /private/var/folders/... to git, and relpath between the two
    # produces a ../../.. walk that matches no allowlist entry.
    directory = os.path.dirname(os.path.realpath(path)) or "."
    if directory not in _TOPLEVEL:
        p = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        _TOPLEVEL[directory] = p.stdout.strip() if p.returncode == 0 else None
    top = _TOPLEVEL[directory]
    if not top:
        return path
    return os.path.relpath(os.path.realpath(path), os.path.realpath(top))


def blob_sha1(data):
    """git's own object name for this content, so a `blob` entry matches on disk."""
    # sha1 because git's object name is sha1; this is an identifier lookup
    # against the allowlist, not a security check.
    return hashlib.sha1(
        b"blob %d\0" % len(data) + data, usedforsecurity=False
    ).hexdigest()


def allowed_reason(allow, path, data, rule, where):
    key = repo_relative(path)
    if rule.startswith("text."):
        return allow.text_reason(None, key, int(where[2:], 16))
    return allow.blob_reason(None, blob_sha1(data), key)


def read_file(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        die("cannot read %s (%s)." % (path, exc))


def inspect_paths(paths, allow=None):
    """({path: [findings]}, {path: [(finding, reason)]}) for artefacts on disk."""
    data = {p: read_file(p) for p in paths}
    raw = inspect_items([(p, p, data[p]) for p in paths])
    found, excused = {}, {}
    for p in paths:
        found[p], excused[p] = [], []
        for hit in raw[p]:
            reason = (
                allowed_reason(allow, p, data[p], hit[0], hit[2]) if allow else None
            )
            (excused[p] if reason else found[p]).append(
                (hit, reason) if reason else hit
            )
    return found, excused


def report(hits, stream=sys.stdout):
    for rule, label, where, line in sorted(hits):
        at = " line %d" % line if line else ""
        tag = (
            "UNCLEARABLE"
            if rule in UNCLEARABLE
            else "noted"
            if rule in ADVISORY
            else "unscannable"
            if rule in UNSCANNABLE
            else "clearable"
        )
        stream.write("  %-28s %-11s %s [%s]%s\n" % (rule, tag, label, where, at))


def clean_text(path, data, allow):
    """Drop exactly the flagged codepoints, and nothing structural or allowlisted."""
    text = is_text(data)
    drop = set()
    for m in BAD_RE.finditer(text):
        i = m.start()
        if structurally_legitimate(text, i):
            continue
        if allow and allow.text_reason(None, repo_relative(path), ord(text[i])):
            continue
        drop.add(i)
    # surrogateescape both ways: an undecodable blob reaches here as text
    # carrying lone surrogates for its bad bytes, and a plain encode raises on
    # them - which would turn `clean` on the very file the encoding refusal
    # exists for into a traceback.
    return "".join(ch for i, ch in enumerate(text) if i not in drop).encode(
        "utf-8", "surrogateescape"
    )


# Formats exiftool cannot WRITE, measured on 13.55: it answers "Writing of MKV
# files is not yet supported", "Sorry, riff:Artist doesn't exist or isn't
# writable" and "does not yet support writing of SVG images". ffmpeg rewrites
# the container with `-c copy`, so no stream is re-encoded and the payload hash
# is unchanged. mp4/m4a/mov are absent deliberately: exiftool writes those today
# with pixel and frame signatures identical, and swapping a working writer for
# another one buys nothing.
FFMPEG_EXT = frozenset(
    [".mp3", ".wav", ".mkv", ".webm", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".avi"]
)
# The two ffmpeg keys with an unambiguous attributive meaning. `comment` is
# excluded on purpose - it is where every generator this gate catches writes its
# name, so carrying it across would restore the finding.
FFMPEG_KEEP = {
    "artist": "artist",
    "byline": "artist",
    "copyright": "copyright",
    "rights": "copyright",
    "creator": "artist",
}
TIFF_MAGIC = (b"II*\x00", b"MM\x00*")


def attributive_keep(path):
    """[tag] - the group-qualified tags `clean` must put back after the strip.

    A bare `-all=` is a correct strip and a wrong outcome: it wiped `Artist`,
    `Copyright` and `XMP-dc:Rights` off an asset alongside the Midjourney tag,
    which is the standing rule in ~/OPINIONS.md inverted - attributive
    provenance is kept in full, and only a signal whose function is to let a
    MACHINE classify the artefact is removed.

    Read from the file itself rather than from a fixed list, so the restore
    names only tags that are actually there, and each candidate is graded by its
    VALUE: `Artist: Midjourney` is detective wearing an attributive field's
    name, and is not kept.
    """
    keep = []
    for record in run_exiftool([path]):
        for key, value in record.items():
            group, _, tag = key.rpartition(":")
            low = tag.lower().replace("-", "").replace("_", "")
            if low not in ATTRIBUTIVE_FIELDS:
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            if not text.strip():
                continue
            if generator_rule(text) != "binary.tool-tag" or "c2pa" in text.lower():
                continue
            keep.append((group, tag, low, text))
    return keep


def keep_args(tags):
    """The exiftool copy arguments for a keep-set."""
    return sorted({"-%s:%s" % (g, t) if g else "-" + t for g, t, _, _ in tags})


def keep_ffmpeg(tags):
    """The ffmpeg -metadata arguments for a keep-set."""
    out = {}
    for _group, _tag, low, value in tags:
        if low in FFMPEG_KEEP:
            out.setdefault(FFMPEG_KEEP[low], value)
    args = []
    for key, value in sorted(out.items()):
        args += ["-metadata", "%s=%s" % (key, value)]
    return args


def clean_binary(path, keep=()):
    """exiftool strips every writable metadata block, in place on `path`.

    Measured 2026-08-31 on the firstmate banner: `-all= -overwrite_original`
    removes the whole signed C2PA manifest - zero JUMBF tags and zero `c2pa`
    byte markers afterwards, 3,011,594 bytes down to 2,982,495. It does NOT
    touch pixels, which is why an unbound watermark survives it and is reported
    UNCLEARABLE rather than cleaned.

    `keep` is copied back from the file's own pre-strip state in the same
    invocation - exiftool applies its arguments in order, so `-all=` empties the
    output and `-tagsFromFile @` then reads the ORIGINAL for the named tags.
    """
    cmd = ["exiftool", "-all="]
    if keep:
        cmd += ["-tagsFromFile", "@"] + list(keep)
    cmd += ["-overwrite_original", "--", path]
    p = subprocess.run(cmd, capture_output=True)
    return p.returncode == 0, (p.stderr or p.stdout).decode("utf-8", "replace").strip()


def need(tool, remedy):
    """(ok, refusal message) - a writer whose tool is absent refuses by name.

    Never a silent skip and never a fall-through to a writer that cannot reach
    the value: a `clean` that removed nothing must not look like one that
    removed everything.
    """
    if shutil.which(tool):
        return True, ""
    return False, "%s is not installed (%s), so this format has no writer" % (
        tool,
        remedy,
    )


# ImageMagick 7 renamed the driver to `magick`; Ubuntu still ships 6, whose
# binary is `convert`. Both take `<in> -strip <out>` identically, so resolving
# the name is the whole difference - and a `need magick` that did not was what
# took the secret-scan job down on ubuntu-24.04, where apt installs 6.q16.
IMAGEMAGICK_NAMES = ("magick", "convert")


def imagemagick():
    """The ImageMagick driver on this machine, or None."""
    for name in IMAGEMAGICK_NAMES:
        if shutil.which(name):
            return name
    return None


def clean_pdf(target, keep):
    """exiftool, then a full qpdf rewrite so the superseded objects are gone.

    exiftool's PDF edit is an INCREMENTAL update - it says so itself, "PDF edits
    are reversible" - so the old object survives in the bytes and the second
    witness in `cmd_clean` correctly refused. qpdf rebuilds the file from the
    object graph, which drops everything nothing points at.
    """
    ok, msg = clean_binary(target, keep)
    if not ok:
        return ok, msg
    ok, msg = need("qpdf", "brew install qpdf")
    if not ok:
        return ok, msg + " - exiftool alone leaves the value recoverable in the bytes"
    fresh = target + ".rewrite"
    p = subprocess.run(
        ["qpdf", "--object-streams=generate", "--", target, fresh], capture_output=True
    )
    # 0 is clean, 3 is warnings-but-written; 2 is an error and writes nothing.
    if p.returncode not in (0, 3) or not os.path.exists(fresh):
        return False, "qpdf could not rewrite this PDF: %s" % (
            p.stderr.decode("utf-8", "replace").strip() or "no output"
        )
    os.replace(fresh, target)
    return True, ""


def clean_tiff(target, keep):
    """Re-encode with ImageMagick, then copy the keep-set back.

    exiftool cannot delete IFD0 from a TIFF - it says `[minor] Can't delete IFD0
    from TIFF` and leaves the value in the bytes whether it writes in place or
    to a new file. `-strip` rebuilds the image, which is why the pixel signature
    is the witness here.
    """
    im = imagemagick()
    if im is None:
        return need("magick", "brew install imagemagick")
    fresh = target + ".rewrite"
    p = subprocess.run([im, target, "-strip", fresh], capture_output=True)
    if p.returncode != 0 or not os.path.exists(fresh):
        return False, "ImageMagick could not re-encode this TIFF: %s" % (
            p.stderr.decode("utf-8", "replace").strip() or "no output"
        )
    if keep:
        subprocess.run(
            ["exiftool", "-q", "-tagsFromFile", target]
            + keep
            + ["-overwrite_original", "--", fresh],
            capture_output=True,
        )
    os.replace(fresh, target)
    return True, ""


def clean_stream(target, tags):
    """ffmpeg rewrites the container and copies every stream untouched.

    `-c copy` re-encodes nothing, so the payload hash is the fidelity witness
    and it is unchanged. Only the two unambiguous attributive keys are carried
    across - see FFMPEG_KEEP for why `comment` is not one of them.
    """
    ok, msg = need("ffmpeg", "brew install ffmpeg")
    if not ok:
        return ok, msg
    # The suffix is load-bearing: ffmpeg picks its muxer from the extension.
    fresh = target + ".rewrite" + os.path.splitext(target)[1]
    p = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            target,
            "-map",
            "0",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
        ]
        + keep_ffmpeg(tags)
        + ["-c", "copy", fresh],
        capture_output=True,
    )
    if p.returncode != 0 or not os.path.exists(fresh):
        return False, "ffmpeg could not rewrite this container: %s" % (
            p.stderr.decode("utf-8", "replace").strip()[:200] or "no output"
        )
    os.replace(fresh, target)
    return True, ""


# An SVG's metadata lives in XML the drawing does not reference: a <metadata>
# element, or a bare XMP packet. Both are removed as whole elements rather than
# by editing attributes, so nothing in the drawing is touched.
SVG_METADATA_RE = re.compile(
    r"<metadata\b.*?</metadata\s*>"
    r"|<\?xpacket\b.*?<\?xpacket\s+end\s*=\s*.[rw].\s*\?>"
    r"|<x:xmpmeta\b.*?</x:xmpmeta\s*>"
    r"|<rdf:RDF\b.*?</rdf:RDF\s*>",
    re.DOTALL | re.IGNORECASE,
)


def clean_svg(target):
    """exiftool refuses to write SVG, so the removal is done at the XML level."""
    with open(target, "rb") as fh:
        data = fh.read()
    text = is_text(data)
    if text is None:
        return False, "this SVG is not decodable text"
    with open(target, "wb") as fh:
        fh.write(SVG_METADATA_RE.sub("", text).encode("utf-8", "surrogateescape"))
    return True, ""


def clean_zip(target):
    """Repack the archive with every MEDIA member cleaned by the same writers.

    Members are rewritten with their original compression and order, so an
    unchanged member is content-identical. Text members are deliberately left
    alone: a path inside an archive is not a path the allowlist can speak about,
    so stripping codepoints there would be an unreviewable edit - the residual
    re-inspect catches anything left and reports NOT CLEAN, which is the honest
    outcome rather than a silent one.
    """
    with open(target, "rb") as fh:
        data = fh.read()
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        return False, "not a readable archive (%s)" % exc
    work = tempfile.mkdtemp(prefix="watermark-scan.zip.")
    try:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dest:
            for info in source.infolist():
                if info.is_dir():
                    dest.writestr(info, b"")
                    continue
                body = source.read(info)
                if is_media(info.filename, body[:16]) or body.startswith(ZIP_MAGIC):
                    member = os.path.join(work, os.path.basename(info.filename) or "m")
                    with open(member, "wb") as fh:
                        fh.write(body)
                    ok, why = clean_media(member, info.filename, body)
                    if not ok:
                        return False, "%s: %s" % (info.filename, why)
                    with open(member, "rb") as fh:
                        body = fh.read()
                entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                entry.compress_type = info.compress_type
                entry.external_attr = info.external_attr
                dest.writestr(entry, body)
        with open(target, "wb") as fh:
            fh.write(out.getvalue())
        return True, ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def clean_media(target, path, data):
    """The writer that can actually rewrite this format, by format."""
    head, ext = data[:16], os.path.splitext(path)[1].lower()
    if data.startswith(ZIP_MAGIC):
        return clean_zip(target)
    if data.startswith(b"%PDF-"):
        return clean_pdf(target, keep_args(attributive_keep(target)))
    if ext in (".tif", ".tiff") or head[:4] in TIFF_MAGIC:
        return clean_tiff(target, keep_args(attributive_keep(target)))
    if ext in FFMPEG_EXT:
        return clean_stream(target, attributive_keep(target))
    if ext == ".svg" or b"<svg" in head or b"<svg" in data[:2048]:
        return clean_svg(target)
    return clean_binary(target, keep_args(attributive_keep(target)))


def allowed_phrase(n_allowed, n_noted=0):
    """The one line a reader scans when nothing blocking was found, or None.

    `clean` is reserved for a path where nothing was found AT ALL. A path whose
    findings were excused, or examined and noted, still carries something, so
    its summary must not read the same as an unmarked file's - that is the
    false-clean this gate exists to refuse, and it was in the gate's own output.
    """
    if not n_allowed and not n_noted:
        return None
    parts = []
    if n_allowed:
        parts.append("%d allowed" % n_allowed)
    if n_noted:
        parts.append("%d noted" % n_noted)
    return ", ".join(parts + ["0 blocking"])


def clean_verdict(before_hits, after_hits, survived):
    """(verdict, lines, replace_rules) - the ONLY place a clean verdict is produced.

    FOUR outcomes and no fifth, and every one of them is computed from
    `after_hits`, which is a re-detection of the artefact on disk. That is the
    structural half of the captain's rule: a caller cannot reach a clean verdict
    without having re-read the file, because there is no code path to `cleaned`
    that does not pass through this function's arguments. The strip tool's exit
    status is not an argument here and cannot be.

      cleaned      stripped, re-read, nothing found
      not-clean    a signal we CAN see is still there
      unverified   every removable signal is gone and re-verified, and a class
                   nothing local can measure remains. Not "clean", not "marked"
      unscannable  the artefact did not decode or did not parse, so nothing was
                   established in either direction

    Neither `unverified` nor `unscannable` ever collapses into `cleaned`.
    `unverified` is separate from `not-clean` because the two ask for different
    things from a reader: one says a detectable signal survived the strip, the
    other says the strip worked as far as it can be measured and the remaining
    question is not answerable here.
    """
    unscannable = sorted({(h[0], h[1]) for h in after_hits if h[0] in UNSCANNABLE})
    if unscannable:
        return (
            "unscannable",
            ["%s: %s" % (rule, label) for rule, label in unscannable],
            (set(), []),
        )

    # The byte witness first: it answers a question the re-read cannot, which is
    # whether a container kept the value in a block its own reader no longer
    # reports. Measured on a PDF, whose exiftool edit is an incremental update.
    if survived:
        return (
            "not-clean",
            ["still in the bytes: %s" % ", ".join(survived)],
            (set(), []),
        )

    # Everything the re-read still finds, advisories excluded - an advisory is
    # attributive and was never a candidate for removal.
    remaining = [h for h in after_hits if h[0] not in ADVISORY]
    # UNCLEARABLE from BOTH sides. From `after` because a re-read can surface a
    # class the first scan did not; from `before` because the whole point of
    # this verb is that a file can be verifiably free of every removable signal
    # and still not be clean - the mark is in the pixels, so a successful strip
    # is not evidence about it.
    replace = {h[0] for h in remaining if h[0] in UNCLEARABLE} | {
        h[0] for h in before_hits if h[0] in UNCLEARABLE
    }
    named = sorted(
        {h[1] for h in list(before_hits) + list(after_hits) if h[0] in UNCLEARABLE}
    )
    if replace:
        # Name the generator, not just the rule. It is the most actionable part:
        # knowing which tool marked this is what lets the next asset come from
        # one that does not.
        detail = ", ".join(sorted(replace))
        if named:
            detail += " - %s" % ", ".join(named)
        return (
            "unverified",
            ["%s: removal could not be VERIFIED" % detail],
            (replace, named),
        )
    if remaining:
        return (
            "not-clean",
            ["%d finding(s) survived the strip" % len(remaining)],
            (set(), []),
        )
    return "cleaned", [], (set(), [])


def print_noted(path, hits):
    for rule, label, where, line in sorted(hits):
        at = " line %d" % line if line else ""
        print("%s: NOTED    %s  %s  %s [%s]%s" % (TOOL, path, rule, label, where, at))


def cmd_inspect(argv):
    if not argv:
        die("usage: %s inspect <path>..." % TOOL)
    if not need_exiftool():
        die(
            "exiftool is not installed (brew install exiftool), so no binary can be inspected."
        )
    allow = local_allow()
    found, excused = inspect_paths(argv, allow)
    rc = 0
    for path in argv:
        for (rule, _label, where, line), reason in excused[path]:
            at = " line %d" % line if line else ""
            print(
                "%s: ALLOWED  %s  %s [%s]%s - %s"
                % (TOOL, path, rule, where, at, reason)
            )
        hits, noted = split_advisory(found[path])
        print_noted(path, noted)
        if not hits:
            phrase = allowed_phrase(len(excused[path]), len(noted))
            print("%s: %s  %s" % (TOOL, phrase or "clean", path))
            continue
        rc = 1
        print("%s: findings in %s" % (TOOL, path))
        report(hits)
        if any(h[0] in UNCLEARABLE for h in hits):
            print(
                "  -> "
                + unverifiable_notice(
                    {h[0] for h in hits if h[0] in UNCLEARABLE},
                    sorted({h[1] for h in hits if h[0] in UNCLEARABLE}),
                ).replace("\n", "\n     ")
            )
    return rc


def cmd_verify(argv):
    """Re-read the artefact and assert it is clean, trusting no earlier step.

    Two witnesses, the same two detection uses: the raw bytes and exiftool. The
    bytes matter on their own for a container that rewrites metadata by
    APPENDING rather than by replacing - a PDF incremental update leaves the old
    object in the file, where exiftool reports the tag gone and a byte scan does
    not.

    Its ceiling, stated because a green here is easy to over-read: it answers
    "is anything detectable left in this file", never "was this file ever
    marked". A pixel watermark is invisible to it by construction, so a copy of
    the firstmate banner whose manifest has been stripped VERIFIES CLEAN and is
    not. `clean`'s own report, or `inspect` on the original, is what answers the
    other question.
    """
    if not argv:
        die("usage: %s verify <path>..." % TOOL)
    if not need_exiftool():
        die(
            "exiftool is not installed (brew install exiftool), so no binary can be verified."
        )
    found, excused = inspect_paths(argv, local_allow())
    rc = 0
    for path in argv:
        hits, noted = split_advisory(found[path])
        print_noted(path, noted)
        if hits:
            rc = 1
            print("%s: NOT CLEAN  %s" % (TOOL, path))
            report(hits)
        else:
            phrase = allowed_phrase(len(excused[path]), len(noted))
            head = "VERIFIED CLEAN" if not phrase else "VERIFIED - " + phrase
            print(
                "%s: %s  %s - re-read from disk: no invisible codepoint, "
                "no byte marker, no exiftool tag" % (TOOL, head, path)
            )
    return rc


def cmd_clean(argv):
    """Remove what is removable, prove it, and never call the rest clean."""
    out_dir, paths = None, []
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            i += 1
            if i >= len(argv):
                die("--out needs a directory.")
            out_dir = argv[i]
        elif argv[i] == "--in-place":
            out_dir = None
        elif argv[i].startswith("-"):
            die("unknown option %s" % argv[i])
        else:
            paths.append(argv[i])
        i += 1
    if not paths:
        die("usage: %s clean [--out <dir>] <path>..." % TOOL)
    if not need_exiftool():
        die(
            "exiftool is not installed (brew install exiftool), so no binary can be cleaned."
        )
    if out_dir and not os.path.isdir(out_dir):
        die("%s is not a directory." % out_dir)

    allow = local_allow()
    before, excused = inspect_paths(paths, allow)
    rc = 0
    for path in paths:
        # Advisories are examined and printed, never stripped: a `Producer`
        # naming a publishing tool is provenance a reader uses, and the rule
        # keeps attributive provenance in full.
        hits, noted = split_advisory(before[path])
        print_noted(path, noted)
        clearable = [h for h in hits if h[0] not in UNCLEARABLE]
        for (rule, _label, where, line), reason in excused[path]:
            at = " line %d" % line if line else ""
            print(
                "%s: ALLOWED  %s  %s [%s]%s - %s"
                % (TOOL, path, rule, where, at, reason)
            )
        if not hits:
            left = []
            if excused[path]:
                left.append("%d allowlisted" % len(excused[path]))
            if noted:
                left.append("%d noted" % len(noted))
            note = " (%s, left in place)" % ", ".join(left) if left else ""
            print("%s: nothing to clean  %s%s" % (TOOL, path, note))
            continue

        data = read_file(path)
        if out_dir:
            target = os.path.join(out_dir, os.path.basename(path))
            with open(target, "wb") as fh:
                fh.write(data)
        else:
            # Never in place with no original alongside. Refusing to overwrite
            # an existing .orig is the difference between a backup and a second
            # copy of the already-cleaned file.
            target, backup = path, path + ".orig"
            if os.path.exists(backup):
                print(
                    "%s: NOT CLEANED  %s (%s already exists; move it aside first)"
                    % (TOOL, path, backup)
                )
                rc = 1
                continue
            with open(backup, "wb") as fh:
                fh.write(data)

        # A container can be BOTH, and the elif this replaces got that wrong: a
        # PDF whose objects hold no NUL byte decodes as text, so the text writer
        # claimed it and the metadata exiftool had just flagged was never
        # touched - `clean` then reported success having removed nothing.
        text = is_text(data)
        writers = []
        if text is not None:
            with open(target, "wb") as fh:
                fh.write(clean_text(path, data, allow))
            writers.append((True, ""))
        # A zip-family container is not `is_media` - no magic in the table, no
        # extension in the list - and it is exactly what `clean` used to refuse
        # with "no writer for this container".
        if is_media(path, data[:16]) or data.startswith(ZIP_MAGIC):
            writers.append(clean_media(target, path, data))
        if not writers:
            wrote, why = False, "no writer for this container"
        else:
            wrote = all(ok for ok, _ in writers)
            why = "; ".join(msg for ok, msg in writers if not ok)

        if not wrote:
            print("%s: NOT CLEANED  %s (%s)" % (TOOL, path, why))
            rc = 1
            continue

        # RE-DETECTION, with the full engine, on the artefact as it is now on
        # disk. Everything below is computed from this and from the raw bytes;
        # the writer's exit status has already been consumed above and takes no
        # part in the verdict.
        after_hits = inspect_paths([target], allow)[0][target]

        # A SECOND witness, because the inspector's own re-read is not one for
        # every container. Measured 2026-08-31: `-all= -overwrite_original` on a
        # PDF is an INCREMENTAL update - exiftool warns "PDF edits are
        # reversible", then reports Producer gone while `Midjourney v6` is still
        # in the bytes to be read.
        with open(target, "rb") as fh:
            after = fh.read()
        survived = sorted(
            {
                h[1]
                for h in clearable
                if h[0] == "binary.generator-tool"
                and h[1].encode("utf-8", "replace") in after
            }
        )
        verdict, lines, (replace, names) = clean_verdict(hits, after_hits, survived)
        removed = len(clearable) - len([h for h in after_hits if h[0] not in ADVISORY])
        if verdict == "unscannable":
            print(
                "%s: UNSCANNABLE  %s - the strip ran, but nothing established this "
                "file either way" % (TOOL, target)
            )
            for line in lines:
                print("%s:            %s" % (TOOL, line))
            print(
                "%s:            re-save it in one encoding, or regenerate the "
                "container so its own structure parses." % TOOL
            )
            rc = 1
            continue
        if verdict == "unverified":
            # Not "clean" and not "marked": the metadata-borne signals were
            # removed and re-detected as gone, and one class remains that
            # nothing local can measure either way. The work that succeeded is
            # reported because it is not wasted, and every remedy is named
            # because withholding one substitutes this tool's judgement for the
            # author's.
            print(
                "%s: UNVERIFIED  %s - removed and re-verified %d finding(s); %s"
                % (TOOL, target, removed, lines[0])
            )
            print(
                "  -> "
                + unverifiable_notice(replace, names, stripped=True).replace(
                    "\n", "\n     "
                )
            )
            rc = 1
            continue
        if verdict == "not-clean":
            print("%s: NOT CLEAN  %s - %s" % (TOOL, target, lines[0]))
            if survived:
                print(
                    "%s:            the strip did not reach it - a container that "
                    "rewrites metadata by APPENDING leaves the old object behind, and "
                    "a container the writer cannot rebuild keeps the value in an "
                    "untouched block. Regenerate the artefact without the tag." % TOOL
                )
            else:
                report([h for h in after_hits if h[0] not in ADVISORY])
            rc = 1
            continue
        print(
            "%s: CLEANED and VERIFIED  %s - removed %d finding(s), re-read shows none"
            % (TOOL, target, removed)
        )
    return rc


USAGE = """usage:
  watermark-scan.py push <remote> [<url>]   git pre-push; the ref list on stdin
  watermark-scan.py inspect <path>...       report findings, clearable or not
  watermark-scan.py clean [--out DIR] <path>...   remove what is removable, then verify
  watermark-scan.py verify <path>...        re-read and assert the artefact is clean
"""


def main():
    argv = sys.argv[1:]
    if not argv:
        die(USAGE.rstrip())
    verb, rest = argv[0], argv[1:]
    if verb == "push":
        return cmd_push(rest)
    if verb == "inspect":
        return cmd_inspect(rest)
    if verb == "clean":
        return cmd_clean(rest)
    if verb == "verify":
        return cmd_verify(rest)
    die("unknown verb '%s'\n%s" % (verb, USAGE.rstrip()))


if __name__ == "__main__":
    sys.exit(main())
