#!/usr/bin/env python3
"""Scan a pull request's title and body for text a public repository must not carry.

Usage: gh pr view N --json title,body | pr-text.py

Reads one JSON object with `title` and `body` on stdin. Exit status: 0 clean,
1 a finding, 2 could not decide (not JSON, no title, too large). Each finding
is one line, `pr-text: title|body:LINE: RULE: "text"`, and RULE is one of:

em-dash          U+2014. "Never use the em dash" (the maintainer's global agent
                 instructions, "Writing and reporting"); VOICE.md, "What to avoid".
slop-phrase      A phrase from the draft skill's hard rule 4 or VOICE.md, "What to
                 avoid". Only phrases that mean the same thing wherever they
                 appear: "leverage" as a verb, "robust" as filler and empty
                 intensifiers need a reader, so they are left to review.
attribution      A "Generated with" or "Co-authored-by" line naming an agent, the
                 refusal the commit-msg hook makes for a commit message, applied to
                 the text that becomes the squash commit's message.
home-path        /Users/<name> or /home/<name>, which names an account on a machine.
security-marker  A count of security findings (the same shape exposure-scan.py
                 refuses on a push), a Dependabot, code-scanning or secret-scanning
                 alert by number, or a GHSA or CVE id: the pull request line stays
                 neutral about what a finding was.

The rules are deterministic and read nothing but the text.
"""

import json
import re
import sys

MAX_BYTES = 1_000_000
EM_DASH = "—"
# The vendor words of system-maintenance/git-hooks/commit-msg, with the same
# boundaries: a non-letter before, and after either a non-letter or a bot suffix.
VENDORS = (
    "claude|anthropic|codex|openai|chatgpt|gpt|gemini|cursor|copilot|warp|kimi|moonshot"
    "|deepseek|qwen|glm|zhipu|zhipuai|grok|xai|x\\.ai|opencode|devin|aider|bard|windsurf|codeium"
)
VENDOR_TAIL = rf"[^A-Za-z0-9](?:{VENDORS})(?:AI|Bot|Agent|Code|CLI)?(?:[^A-Za-z0-9]|$)"
APOS = "['’]"
SLOP = [
    rf"\bin today{APOS}s (?:fast-paced world|post)\b",
    rf"\bit(?:{APOS}s| is) worth noting\b",
    r"\bdelv(?:e|es|ed|ing)\b",
    r"\bgame[- ]chang(?:er|ers|ing)\b",
    r"\bseamless(?:ly)?\b",
    r"\bat the end of the day\b",
    r"\bin conclusion\b",
    r"\bto summari[sz]e\b",
    r"\bit could be argued that\b",
    r"\bsome may feel\b",
    r"\bexciting times\b",
    r"\breally interesting space\b",
    r"\bbest-in-class\b",
    r"\bmillion-dollar\b",
]
# Names that stand for "your account" in an example, and the two accounts that are not a person's.
NOT_A_PERSON = "runner|Shared|user|username|name|you|me|example"
# The count pattern is exposure-scan.py's VULN_COUNT, which owns why each branch needs a noun.
FINDING_COUNT = (
    r"(?<![\w.])\d+\s+(?:(?:open|unresolved|outstanding|active|known)\s+)?"
    r"(?:(?:dependabot|code[\s-]scanning|secret[\s-]scanning|codeql|security)\s+"
    r"(?:alerts?|advisor(?:y|ies)|vulnerabilit(?:y|ies)|findings?)"
    r"|vulnerabilit(?:y|ies)|cves?)\b"
)

RULES = [
    ("em-dash", re.compile(EM_DASH)),
    ("slop-phrase", re.compile("|".join(SLOP), re.I)),
    (
        "attribution",
        re.compile(
            rf"^\W*(?:co-authored-by:|generated (?:with|by)\W).*{VENDOR_TAIL}",
            re.I | re.M,
        ),
    ),
    (
        "home-path",
        re.compile(
            rf"(?<![\w./~-])/(?:Users|home)/(?!(?:{NOT_A_PERSON})\b)[A-Za-z0-9._-]+"
        ),
    ),
    (
        "security-marker",
        re.compile(
            FINDING_COUNT
            + r"|\b(?:dependabot|code[\s-]scanning|secret[\s-]scanning|codeql)\s+alerts?\s+#?\d+"
            + r"|\bGHSA(?:-[0-9a-z]{4}){3}\b|\bCVE-\d{4}-\d{4,}\b",
            re.I,
        ),
    ),
]


def scan(label, text):
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES:
            hit = pattern.search(line)
            if hit:
                shown = hit.group().strip()
                found.append(f'pr-text: {label}:{number}: {rule}: "{shown[:80]}"')
    return found


def main():
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        print(f"pr-text: input is over {MAX_BYTES} bytes", file=sys.stderr)
        return 2
    try:
        pr = json.loads(raw.decode())
        title, body = pr["title"], pr.get("body") or ""
        if not isinstance(title, str) or not isinstance(body, str):
            raise TypeError("title and body must be strings")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print(
            f"pr-text: stdin is not a pull request's title and body: {e!r}",
            file=sys.stderr,
        )
        return 2
    found = scan("title", title) + scan("body", body)
    for line in found:
        print(line)
    if found:
        print(f"pr-text: {len(found)} finding(s) in the pull request text")
        return 1
    print("pr-text: title and body are clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
