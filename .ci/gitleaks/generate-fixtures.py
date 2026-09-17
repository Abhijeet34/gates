#!/usr/bin/env python3
"""Derive every value in fixtures/known-secrets.tsv, and break every format check.

    .ci/gitleaks/generate-fixtures.py            rewrite the fixture
    .ci/gitleaks/generate-fixtures.py --check    fail if the committed file differs

Every character that looks random is drawn from SHA-256 over SEED and the row's
label, so each value is computed from public input rather than issued by a
provider. Where a provider format carries a checksum or a fixed structure that
can be checked offline, the draw is repeated until that check FAILS against the
value, and every run prints the failing check. The rule regexes are not copied
here: test-rules.sh is what proves each value still matches its rule.
"""

import base64
import hashlib
import re
import string
import sys
import zlib
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures/known-secrets.tsv"
SEED = b"gates/.ci/gitleaks/fixtures/known-secrets.tsv"

# Alphanumerics only, a subset of every rule's character class, so no value can
# end on a `-` that would defeat a rule's trailing `\b`.
ALNUM = string.digits + string.ascii_uppercase + string.ascii_lowercase
LOWER_ALNUM = string.digits + string.ascii_lowercase
HEX = "0123456789abcdef"
BASE32 = string.ascii_uppercase + "234567"

HEADER = """\
# SYNTHETIC, NON-FUNCTIONAL credentials, written by
# .ci/gitleaks/generate-fixtures.py. Do not edit by hand: run it to regenerate,
# or with --check to prove this file is exactly its output.
#
# Every value is drawn from SHA-256 over a fixed public seed and the row's
# label, so none was issued by any provider. Where a format carries a checksum
# or structure checkable offline - the GitHub classic PAT CRC32, the account id
# inside an AWS key id, the minisign secret key length, the SHA-256 shape of an
# R2 secret - the generator draws until that check fails, and prints why.
# They exist so that .ci/gitleaks/test-rules.sh fails the moment a rule stops
# firing - a rule nobody exercises is indistinguishable from a rule that does
# not work.
#
# Columns, tab-separated: expected rule id | label | the line to scan.
"""


class Draw:
    """Characters from SHA-256(SEED/label/counter), one byte per draw."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.counter = 0
        self.pool = b""

    def chars(self, alphabet: str, n: int) -> str:
        # Rejection sampling keeps every character equally likely.
        limit = 256 - 256 % len(alphabet)
        out = []
        while len(out) < n:
            if not self.pool:
                block = f"{self.label}/{self.counter}".encode()
                self.pool = hashlib.sha256(SEED + b"/" + block).digest()
                self.counter += 1
            byte, self.pool = self.pool[0], self.pool[1:]
            if byte < limit:
                out.append(alphabet[byte % len(alphabet)])
        return "".join(out)


def base62(n: int, alphabet: str) -> str:
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = alphabet[r] + out
    return out.rjust(6, alphabet[0])


def github_checksum(value: str) -> tuple[bool, str]:
    # github.blog, "Behind GitHub's new authentication token formats": the last
    # 6 characters are CRC32, base62 with leading zeros. Neither the alphabet
    # order nor whether the prefix is hashed is published, so all four readings
    # must disagree with the token.
    tail = value[-6:]
    readings = {
        base62(zlib.crc32(body.encode()), alphabet)
        for body in (value[4:-6], value[:-6])
        for alphabet in (
            ALNUM,
            string.digits + string.ascii_lowercase + string.ascii_uppercase,
        )
    }
    return tail not in readings, (
        f"ends {tail!r}; CRC32/base62 of its body reads {sorted(readings)}"
    )


def aws_account_id(value: str) -> tuple[bool, str]:
    # awsteele.com/blog/2020/09/26/aws-access-key-format.html: for a key id whose
    # 5th character is Q or later, characters 5-12 in base32, less "QAAAAAAA",
    # doubled, plus 1 when character 13 is Q or later, is the 12-digit account.
    def b32(s: str) -> int:
        n = 0
        for c in s:
            n = n * 32 + BASE32.index(c)
        return n

    q = BASE32.index("Q")
    account = (b32(value[4:12]) - b32("QAAAAAAA")) * 2 + (BASE32.index(value[12]) >= q)
    applies = BASE32.index(value[4]) >= q
    return applies and account > 999_999_999_999, (
        f"encodes account id {account}, {len(str(account))} digits; "
        f"an AWS account id has 12"
    )


def minisign_length(raw: str) -> tuple[bool, str]:
    # jedisct1.github.io/minisign, secret key format: Ed || Sc || B2 || 32-byte
    # salt || 8-byte opslimit || 8-byte memlimit || 104-byte keynum_sk.
    n = len(base64.b64decode(raw, validate=True))
    return n != 158, f"decodes to {n} bytes; a minisign secret key is 158"


def minisign_file_length(value: str) -> tuple[bool, str]:
    return minisign_length(base64.b64decode(value).decode().splitlines()[1])


def r2_secret_shape(value: str) -> tuple[bool, str]:
    # developers.cloudflare.com/r2/api/tokens: the Secret Access Key is "The
    # SHA-256 hash of the API token value", which is 64 hex characters.
    ok = re.fullmatch(r"[0-9a-f]{64}", value) is None
    return ok, f"is {len(value)} characters, not the 64 hex of a SHA-256"


def minisign_raw(d: Draw) -> str:
    # 100 characters decode to 75 bytes: short of the 158 a real key needs.
    return "RWRTY0Iy" + d.chars(ALNUM, 92)


def minisign_file(d: Draw) -> str:
    text = f"untrusted comment: rsign encrypted secret key\n{minisign_raw(d)}\n"
    return base64.b64encode(text.encode()).decode()


def hf_token(d: Draw) -> str:
    # The label promises digits: that is what the default hf rule misses.
    while True:
        body = d.chars(ALNUM, 34)
        if any(c.isdigit() for c in body):
            return "hf_" + body


def slack_bot(d: Draw) -> str:
    return (
        f"xoxb-{d.chars('123456789', 1)}{d.chars(string.digits, 12)}"
        f"-{d.chars('123456789', 1)}{d.chars(string.digits, 12)}-{d.chars(ALNUM, 24)}"
    )


# rule id, label, line with {} for the value, value maker, check that must fail.
ROWS = [
    ("openai-compatible-api-key", "moonshot-kimi", 'MODELS = {{"kimi": "{}"}}',
     lambda d: "sk-" + d.chars(ALNUM, 48), None),
    ("openai-compatible-api-key", "deepseek", 'MODELS = {{"deepseek": "{}"}}',
     lambda d: "sk-" + d.chars(LOWER_ALNUM, 32), None),
    ("openai-api-key", "openai-project", 'CLIENT = OpenAI("{}")',
     lambda d: f"sk-proj-{d.chars(ALNUM, 74)}T3BlbkFJ{d.chars(ALNUM, 74)}", None),
    ("anthropic-api-key", "anthropic", 'CLIENT = Anthropic("{}")',
     lambda d: f"sk-ant-api03-{d.chars(ALNUM, 93)}AA", None),
    ("xai-api-key", "xai-grok", 'MODELS = {{"grok": "{}"}}',
     lambda d: "xai-" + d.chars(ALNUM, 80), None),
    ("groq-api-key", "groq", 'MODELS = {{"groq": "{}"}}',
     lambda d: "gsk_" + d.chars(ALNUM, 52), None),
    ("openrouter-api-key", "openrouter", 'MODELS = {{"router": "{}"}}',
     lambda d: "sk-or-v1-" + d.chars(HEX, 64), None),
    ("huggingface-access-token-alnum", "hf-with-digits", 'MODELS = {{"hub": "{}"}}',
     hf_token, None),
    ("gcp-api-key", "google-gemini", 'MODELS = {{"gemini": "{}"}}',
     lambda d: "AIza" + d.chars(ALNUM, 35), None),
    ("aws-access-token", "aws-r2-key-id", 'ENDPOINT = {{"id": "{}"}}',
     lambda d: "AKIA" + d.chars(BASE32[BASE32.index("Q"):], 1) + d.chars(BASE32, 15),
     aws_account_id),
    ("github-pat", "github-classic", 'REMOTE = {{"auth": "{}"}}',
     lambda d: "ghp_" + d.chars(ALNUM, 36), github_checksum),
    ("github-fine-grained-pat", "github-fine", 'REMOTE = {{"auth": "{}"}}',
     lambda d: "github_pat_" + d.chars(ALNUM, 82), None),
    ("slack-bot-token", "slack-bot", 'HOOKS = {{"bot": "{}"}}', slack_bot, None),
    ("minisign-secret-key", "minisign-raw", 'UPDATER = {{"sk": "{}"}}',
     minisign_raw, minisign_length),
    ("minisign-secret-key", "minisign-b64", 'UPDATER = {{"sk": "{}"}}',
     minisign_file, minisign_file_length),
    ("generic-api-key", "r2-secret-keyword", 'R2_SECRET_ACCESS_KEY = "{}"',
     lambda d: d.chars(ALNUM, 40), r2_secret_shape),
]  # fmt: skip


def generate() -> str:
    lines = [HEADER]
    for rule, label, template, make, check in ROWS:
        draw = Draw(label)
        while True:
            value = make(draw)
            if check is None:
                print(f"{label}: no offline format check is published")
                break
            broken, why = check(value)
            if broken:
                print(f"{label}: check FAILS - {why}")
                break
        lines.append(f"{rule}\t{label}\t{template.format(value)}\n")
    return "".join(lines)


def main() -> int:
    text = generate()
    if sys.argv[1:] == ["--check"]:
        if FIXTURE.read_text() != text:
            print(
                f"{FIXTURE} is not this generator's output; rerun it", file=sys.stderr
            )
            return 1
        print(f"ok {FIXTURE.name} matches the generator")
        return 0
    if sys.argv[1:]:
        print("usage: generate-fixtures.py [--check]", file=sys.stderr)
        return 2
    FIXTURE.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
