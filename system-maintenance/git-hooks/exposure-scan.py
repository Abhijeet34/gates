#!/usr/bin/env python3
"""Refuse a push that publishes who pushed it or the machine it came from.

    exposure-scan.py push <remote> <url>  < pre-push ref lines   (the hook)
    exposure-scan.py needles                                   (what this machine hides)

Two checks over the pushed range, one verdict:

  identity  every author, committer and tagger address, and every
            Co-authored-by and Signed-off-by trailer, must be an allowlisted
            noreply address.
  content   no added line, commit or tag header, message or path may carry
            this machine's home path, hostname, MAC address, serial number or
            hardware UUID; and, for a public destination only, no
            vulnerability or alert count.

A finding REFUSES when the destination is public, or when its visibility
cannot be established, and WARNS when it is proven private.

The needles are DERIVED here at run time and never written down: a hook that
shipped a list of this machine's identifiers would publish exactly what it
hides. system-maintenance/git-hooks/README.md, "The exposure gate", owns the
design and the measurements behind it.
"""

import fnmatch
import os
import pwd
import re
import shutil
import subprocess
import sys

TOOL = "exposure-scan"
ZERO = "0" * 40

# Addresses that identify no person and no machine. A glob per entry, matched
# case-insensitively against the whole address, with the reason beside it.
ALLOWED_IDENTITIES = (
    ("*@users.noreply.github.com", "GitHub's per-account noreply form, bots included"),
    ("noreply@github.com", "the committer GitHub writes for a web-flow merge or edit"),
    ("support@github.com", "the address Dependabot signs off its own commits with"),
    ("noreply@anthropic.com", "a vendor noreply address, naming no person"),
)

TRAILER = re.compile(r"^\s*(co-authored-by|signed-off-by)\s*:(.*)$", re.I | re.M)

# A count of open security findings is fleet topology in a public repository.
# Each branch needs a security source or a security noun: a bare "open alerts"
# also names a threshold ("past N open alerts") and a log format this
# repository's own advisory suite asserts, and fired on both over its history.
VULN_COUNT = re.compile(
    r"(?<![\w.])\d+\s+"
    r"(?:(?:open|unresolved|outstanding|active|known)\s+)?"
    r"(?:"
    r"(?:dependabot|code[\s-]scanning|secret[\s-]scanning|codeql|security)\s+"
    r"(?:alerts?|advisor(?:y|ies)|vulnerabilit(?:y|ies)|findings?)"
    r"|vulnerabilit(?:y|ies)|cves?"
    r")\b",
    re.I,
)


def refuse(msg):
    sys.stderr.write("%s: %s\n" % (TOOL, msg))
    sys.stderr.write(
        "%s: COULD NOT ESTABLISH that this push is clean. Push refused.\n" % TOOL
    )
    sys.exit(1)


def run(*argv, check=True):
    """stdout of argv as text, or None when it failed and check is False."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            refuse("cannot run %s: %s" % (argv[0], exc))
        return None
    if proc.returncode != 0:
        if check:
            refuse(
                "%s exited %d: %s"
                % (
                    " ".join(argv),
                    proc.returncode,
                    proc.stderr.decode("utf-8", "replace").strip(),
                )
            )
        return None
    return proc.stdout.decode("utf-8", "surrogateescape")


# --- needles -------------------------------------------------------------------


def tool(name):
    path = shutil.which(name)
    if not path:
        refuse(
            "`%s` is not on PATH, so this machine's identifiers cannot be derived "
            "and nothing can be checked for them." % name
        )
    return path


def derive_needles():
    """[(class, source, compiled pattern)] for this machine, or a refusal.

    Refuses rather than dropping a class: a check that silently lost the
    hostname would pass the exact push it exists to stop.
    """
    homes = {os.environ.get("HOME", ""), pwd.getpwuid(os.getuid()).pw_dir}
    hosts, macs, ids = {os.uname().nodename}, set(), []
    if sys.platform == "darwin":
        hosts.add(run(tool("scutil"), "--get", "LocalHostName").strip())
        # HostName is unset on most Macs, and scutil exits 1 for that.
        hosts.add((run(tool("scutil"), "--get", "HostName", check=False) or "").strip())
        macs.update(
            re.findall(r"\bether\s+([0-9a-f:]{17})\b", run(tool("ifconfig")), re.I)
        )
        platform = run(tool("ioreg"), "-rd1", "-c", "IOPlatformExpertDevice")
        for key, label in (
            ("IOPlatformSerialNumber", "serial number (ioreg)"),
            ("IOPlatformUUID", "hardware UUID (ioreg)"),
        ):
            m = re.search(r'"%s"\s*=\s*"([^"]+)"' % key, platform)
            if not m:
                refuse("ioreg reported no %s." % key)
            ids.append((label, m.group(1)))
    elif sys.platform.startswith("linux"):
        net = "/sys/class/net"
        for iface in sorted(os.listdir(net)) if os.path.isdir(net) else ():
            try:
                with open(os.path.join(net, iface, "address")) as fh:
                    macs.add(fh.read().strip())
            except OSError:
                pass
        try:
            with open("/etc/machine-id") as fh:
                ids.append(("machine id (/etc/machine-id)", fh.read().strip()))
        except OSError:
            pass
    else:
        refuse("no identifier source is known for platform %s." % sys.platform)

    needles = []
    for home in homes:
        # "/" or "/root" would match half of any shell script.
        if len(home.rstrip("/")) >= 6:
            pat = re.escape(home.rstrip("/")) + r"(?![\w.-])"
            needles.append(("machine.home-path", "home directory", re.compile(pat)))
    for host in {h for h in hosts if h}:
        for name in {host, host.split(".")[0]}:
            if len(name) >= 4 and name.lower() != "localhost":
                pat = r"(?<![\w-])" + re.escape(name) + r"(?![\w-])"
                needles.append(("machine.hostname", "hostname", re.compile(pat, re.I)))
    for mac in macs:
        octets = mac.lower().split(":")
        if (
            len(octets) != 6
            or set(mac.lower()) <= set("0:")
            or set(mac.lower()) <= set("f:")
        ):
            continue
        # Any separator or none: ifconfig's colons, Windows' hyphens, a router's
        # bare hex and Cisco's dotted groups are all the same address.
        pat = r"(?<![0-9a-f])" + r"[:.\- ]?".join(octets) + r"(?![0-9a-f])"
        needles.append(
            ("machine.mac-address", "network hardware address", re.compile(pat, re.I))
        )
    for label, value in ids:
        if len(value) >= 8:
            body = "-?".join(re.escape(part) for part in value.split("-"))
            pat = r"(?<![0-9A-Za-z])" + body + r"(?![0-9A-Za-z])"
            needles.append(("machine.hardware-id", label, re.compile(pat, re.I)))
    return needles


# --- the pushed range ----------------------------------------------------------


def ranges_from(stdin, remote, url):
    """The rev-list specs this push publishes, the same set .githooks/pre-push scans.

    A new branch excludes what the DESTINATION advertises, asked of the URL git
    passes as $2, and falls back to the named remote's tracking refs. Without
    that, the first push of any branch re-reads the repository's whole history
    and refuses forever over a leak that is already published.
    """
    ranges = []
    advert = None
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] == ZERO:
            continue
        local_sha, remote_sha = parts[1], parts[3]
        if remote_sha != ZERO:
            ranges.append([remote_sha + ".." + local_sha])
            continue
        if advert is None:
            listing = run("git", "ls-remote", url or remote, check=False)
            if listing is None:
                advert = ["--remotes=" + remote]
            else:
                wanted = "".join(
                    ln.split()[0] + "^{commit}\n"
                    for ln in listing.splitlines()
                    if ln.strip()
                )
                proc = subprocess.run(
                    ["git", "cat-file", "--batch-check=%(objectname)"],
                    input=wanted.encode(),
                    capture_output=True,
                )
                advert = sorted(
                    {
                        ln
                        for ln in proc.stdout.decode().split("\n")
                        if re.fullmatch(r"[0-9a-f]{40,64}", ln)
                    }
                )
            if (url or "").startswith(
                os.path.join(os.environ.get("HOME", ""), ".no-mistakes/repos/")
            ):
                # The pipeline's staging bare starts empty; the delivery push
                # re-scans the rest against the real destination.
                advert.append("--remotes")
        ranges.append([local_sha, "--not", *advert] if advert else [local_sha])
    return ranges


def pushed_tags(stdin):
    """Annotated tag objects named by the ref lines, peeled through nesting."""
    tags = []
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] == ZERO:
            continue
        sha = parts[1]
        while (run("git", "cat-file", "-t", sha, check=False) or "").strip() == "tag":
            tags.append(sha)
            body = run("git", "cat-file", "tag", sha)
            sha = body.split("\n", 1)[0].split()[1]
    return tags


def read_objects(shas):
    """{sha: raw text} for commit and tag objects, one `cat-file --batch`."""
    if not shas:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="".join(s + "\n" for s in shas).encode(),
        capture_output=True,
    )
    if proc.returncode != 0:
        refuse(
            "git cat-file --batch failed: %s" % proc.stderr.decode("utf-8", "replace")
        )
    out, pos, objects = proc.stdout, 0, {}
    for sha in shas:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode().split()
        if len(header) != 3:
            refuse("git cat-file --batch returned no object for %s." % sha)
        size = int(header[2])
        objects[sha] = out[nl + 1 : nl + 1 + size].decode("utf-8", "surrogateescape")
        pos = nl + 1 + size + 1
    return objects


def added_lines(spec):
    """Yield (commit, path, line number, text) for every line the range adds.

    `--diff-merges=remerge` shows a merge only where it differs from git's own
    automatic merge, so a conflict resolution is read and a merge of already
    published work is not re-read as new.
    """
    raw = run(
        "git",
        "-c",
        "core.quotePath=false",
        "log",
        "-p",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--diff-merges=remerge",
        "--format=%x00commit %H",
        *spec,
    )
    commit = path = None
    lineno, prev = 0, ""
    for line in raw.split("\n"):
        # A header only straight after `--- `: an added line whose own text
        # begins "++ " arrives as "+++ " too.
        header, prev = prev.startswith("--- ") and line.startswith("+++ "), line
        if line.startswith("\x00commit "):
            commit, path = line.split()[1], None
        elif header:
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and path is not None:
            yield commit, path, lineno, line[1:]
            lineno += 1
        elif line.startswith(("diff --git ", "rename to ", "copy to ")) and commit:
            # The path itself is published, even for a rename or a binary.
            yield commit, "(path)", 0, line


# --- visibility ----------------------------------------------------------------


def repo_of(url):
    """(host, owner/name) for a network URL, or None for a local path."""
    if not url:
        return None
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::\d+)?/(.+)$", url, re.I)
    if not m:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):(?!/)(.+)$", url)
    if not m or url.startswith("file://"):
        return None
    return m.group(1).lower(), re.sub(r"\.git$", "", m.group(2).strip("/"))


def visibility(url):
    """('public'|'private'|'unknown', why). Only a positive answer is private.

    A local destination is judged by `origin`, because the no-mistakes staging
    bare forwards there; a repository with neither publishes nothing.
    """
    target = repo_of(url)
    if target is None:
        origin = run("git", "config", "--get", "remote.origin.url", check=False)
        target = repo_of((origin or "").strip())
        if target is None:
            return "private", "a local destination with no network origin"
    host, name = target
    if host != "github.com":
        return "unknown", "no visibility lookup exists for %s" % host
    answer = run("gh", "api", "repos/" + name, "--jq", ".visibility", check=False)
    answer = (answer or "").strip()
    if answer in ("private", "internal"):
        return "private", "github.com/%s is %s" % (name, answer)
    if answer == "public":
        return "public", "github.com/%s is public" % name
    return "unknown", "`gh api repos/%s` did not answer" % name


# --- the push ------------------------------------------------------------------


def mask(email):
    local, _, domain = email.partition("@")
    return "%s***@%s" % (local[:1], domain) if domain else "***"


def allowed(email):
    return any(
        fnmatch.fnmatchcase(email.lower(), glob) for glob, _ in ALLOWED_IDENTITIES
    )


def identity_findings(sha, text):
    header, _, message = text.partition("\n\n")
    where = (
        "tag %s" % sha[:12] if text.startswith("object ") else "commit %s" % sha[:12]
    )
    for line in header.split("\n"):
        m = re.match(r"^(author|committer|tagger) .*<([^>]*)>", line)
        if m and not allowed(m.group(2)):
            yield where, "identity." + m.group(1), mask(m.group(2))
    for m in TRAILER.finditer(message):
        # `Name <address>`, or a bare address where the brackets were dropped.
        found = re.search(r"<([^>]*)>|([^\s<>]+@[^\s<>]+)", m.group(2))
        email = found and (found.group(1) or found.group(2))
        if email and not allowed(email):
            yield where, "identity." + m.group(1).lower(), mask(email)


def redact(needles, text):
    """The text with every needle replaced, so a refusal never re-publishes one."""
    for _, _, pat in needles:
        text = pat.sub("<redacted>", text)
    return text


def scan_text(needles, text):
    for rule, source, pat in needles:
        if pat.search(text):
            yield rule, source


def cmd_push(argv):
    remote = argv[0] if argv else "origin"
    url = argv[1] if len(argv) > 1 else ""
    needles = derive_needles()
    stdin = sys.stdin.read()
    ranges = ranges_from(stdin, remote, url)
    if not ranges:
        print("%s: nothing to examine (no ref in this push introduces objects)." % TOOL)
        return 0

    commits = []
    for spec in ranges:
        for sha in run("git", "rev-list", *spec).split():
            if sha not in commits:
                commits.append(sha)
    objects = read_objects(commits + pushed_tags(stdin))

    findings, public_only = [], []
    for sha, text in objects.items():
        findings.extend(identity_findings(sha, text))
        kind = "tag" if text.startswith("object ") else "commit"
        # Headers minus the signature block, which is base64 noise.
        body = re.sub(r"\ngpgsig .*?(?=\n\S|\n\n)", "", text, flags=re.S)
        for rule, source in scan_text(needles, body):
            findings.append(
                ("%s %s" % (kind, sha[:12]), rule, "%s, in headers or message" % source)
            )
        if VULN_COUNT.search(body.partition("\n\n")[2]):
            public_only.append(
                ("%s %s" % (kind, sha[:12]), "content.vulnerability-count", "message")
            )
    seen = set()
    for spec in ranges:
        for commit, path, lineno, text in added_lines(spec):
            if (commit, path, lineno, text) in seen:
                continue
            seen.add((commit, path, lineno, text))
            shown = redact(needles, path) if lineno else "(a path)"
            at = (
                "%s:%d (commit %s)" % (shown, lineno, commit[:12])
                if lineno
                else "%s (commit %s)" % (shown, commit[:12])
            )
            for rule, source in scan_text(needles, text):
                findings.append((at, rule, source))
            if VULN_COUNT.search(text):
                public_only.append(
                    (
                        at,
                        "content.vulnerability-count",
                        "an alert or vulnerability count",
                    )
                )

    if not findings and not public_only:
        print(
            "%s: examined %d commit(s) and tag(s) across %d range(s) against %d derived "
            "identifier(s). No identity or machine exposure found."
            % (TOOL, len(objects), len(ranges), len(needles))
        )
        return 0

    vis, why = visibility(url)
    if vis == "private":
        findings = sorted(set(findings))
        if findings:
            sys.stderr.write(
                "%s: WARNING - exposure in a push to a PRIVATE destination (%s):\n"
                % (TOOL, why)
            )
            for where, rule, detail in findings:
                sys.stderr.write(
                    "  %-28s %s  [%s]\n" % (rule, where, redact(needles, detail))
                )
            sys.stderr.write(
                "%s: allowed here; the same commits are refused on any public push.\n"
                % TOOL
            )
        else:
            print(
                "%s: private destination (%s); vulnerability counts are only refused on a public one."
                % (TOOL, why)
            )
        return 0

    sys.stderr.write(
        "%s: IDENTITY OR MACHINE EXPOSURE in the commits being pushed to a %s destination (%s). Push refused.\n\n"
        % (TOOL, vis.upper(), why)
    )
    for where, rule, detail in sorted(set(findings + public_only)):
        # A masked address still carries its domain, and `<login>@<hostname>`
        # is exactly the address whose domain is the leak.
        sys.stderr.write("  %-28s %s  [%s]\n" % (rule, where, redact(needles, detail)))
    sys.stderr.write(
        "\n%s: identity.*  re-author with the noreply address, e.g.\n"
        "%s:   git -c user.email=<id>+<login>@users.noreply.github.com commit --amend --reset-author --no-edit\n"
        "%s: machine.*   remove the value from the file or message; the refusal never prints it.\n"
        "%s: content.*   state the finding without its count.\n"
        "%s: allowlisted identities: %s\n"
        % ((TOOL,) * 5 + (", ".join(g for g, _ in ALLOWED_IDENTITIES),))
    )
    return 1


def cmd_needles():
    """Print each derived needle's class and source, never its value."""
    for rule, source, _ in derive_needles():
        print("%s  %s" % (rule, source))
    return 0


def main():
    verb, argv = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if verb == "push":
        return cmd_push(argv)
    if verb == "needles":
        return cmd_needles()
    sys.stderr.write("usage: %s push <remote> <url> | needles\n" % TOOL)
    return 2


if __name__ == "__main__":
    sys.exit(main())
