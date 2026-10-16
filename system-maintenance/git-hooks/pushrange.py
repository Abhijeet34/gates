"""The commit ranges a push publishes, for every pre-push gate that reads them.

Not a gate and not executable: watermark-scan.py, exposure-scan.py and
signature-check import it so that all of them, and .githooks/pre-push beside
them, ask the same question of the same place. Three copies of this logic once
disagreed about what a push contains (the watermark gate read remote-tracking
refs while the others asked the destination), so one push was judged on 12
commits by two gates and on 3,560 blobs by the third.

A new branch excludes what the DESTINATION advertises, asked of the URL git
passes as $2 and never the remote name in $1, which a `remote.<name>.pushurl`
splits from it. system-maintenance/git-hooks/README.md, "Latency", owns the why.
"""

import os
import re
import subprocess

ZERO = "0" * 40


def advertised(url, remote):
    """Rev-list exclusions for a new branch: the destination's advertised shas.

    Only a sha that resolves locally is excluded; one we do not have, and every
    sha when the destination cannot be asked, stays in the range. Both errors
    scan MORE, which is the safe direction. An `ls-remote` that fails falls back
    to the named remote's tracking refs.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-remote", url or remote], capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["--remotes=" + remote]
    if listing.returncode != 0:
        return ["--remotes=" + remote]
    wanted = "".join(
        ln.split()[0] + "^{commit}\n"
        for ln in listing.stdout.decode("utf-8", "replace").splitlines()
        if ln.strip()
    )
    # One `cat-file --batch-check`, not a `rev-parse` per sha: 0.018s against
    # 1.917s over 201 shas. An object we lack prints "<input> missing", which
    # the fullmatch drops.
    proc = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname)"],
        input=wanted.encode(),
        capture_output=True,
    )
    return sorted(
        {
            ln
            for ln in proc.stdout.decode().split("\n")
            if re.fullmatch(r"[0-9a-f]{40,64}", ln)
        }
    )


def ranges_from(stdin, remote, url):
    """The rev-list specs (argument lists) this push publishes.

    Pushes to the pipeline's staging bare under ~/.no-mistakes/repos/, which
    starts empty and so advertises nothing, additionally exclude everything any
    remote already holds: what has to be caught there is work no remote has
    seen, and the gate's own delivery push re-scans the rest against the real
    destination.
    """
    ranges = []
    seen = set()
    advert = None
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] == ZERO:
            continue
        local_sha, remote_sha = parts[1], parts[3]
        if remote_sha != ZERO:
            spec = [remote_sha + ".." + local_sha]
        else:
            if advert is None:
                advert = advertised(url, remote)
                if (url or "").startswith(
                    os.path.join(os.environ.get("HOME", ""), ".no-mistakes/repos/")
                ):
                    advert.append("--remotes")
            # Exactly one `--not`: a second reverses the sense rather than adding to it.
            spec = [local_sha, "--not", *advert] if advert else [local_sha]
        # Many refs at one commit (`git push --all`, a tag per branch) are one
        # range read once: 600 of them took 17 s here, reading one history 600 times.
        if tuple(spec) not in seen:
            seen.add(tuple(spec))
            ranges.append(spec)
    return ranges
