#!/usr/bin/env bash
# test-upstream-owner-refs.sh - every upstream maintainer's account name left in
# this repository is declared here, with the reason it survives.
#
# Twice now a pass has removed the obvious references, reported the repository
# clean, and left live ones behind. Prose cannot enumerate; this can. The rule it
# enforces is not "no upstream owner appears" - some genuinely must - it is that
# every surviving occurrence is one somebody wrote a reason for, and that the
# reason is one of the three the captain accepts:
#
#   record   a dated measurement or decision log. Rewriting it to say something
#            else happened is falsification, not cleanup.
#   cache    a file that is upstream's own bytes, kept so that diffing a later
#            upstream release against it means anything.
#   literal  a string another program emits and we only match. Editing our copy
#            renames nothing; it breaks the match.
#
# "This install path must resolve to upstream" is NOT one of them. Where we hold
# a build of our own the reference points at ours. Where we do not, the row says
# `gap` - which is not a reason to keep the name, it is a recorded admission that
# nothing of ours exists to point at yet.
#
# The account names are read from .ci/upstream-owners.txt, the one place any of
# them is spelled out, so this file adds no occurrence of its own.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

MANIFEST=.ci/upstream-owners.txt

# path <TAB> class <TAB> max matching lines <TAB> reason.
# A path ending in `/` is a directory prefix. `*` means "any count", and is only
# for a prefix whose membership legitimately grows.
# `read -r -d ''` rather than `$(cat <<'DECL')`: bash scans a command substitution
# for quote pairs even when the heredoc inside it is quoted, so an odd number of
# apostrophes across these reasons is a syntax error. Measured - adding one word
# with an apostrophe broke the file. This form takes the bytes verbatim.
IFS= read -r -d '' DECLARED <<'DECL' || true
.ci/upstream-owners.txt	registry	*	the owner list itself, and the one place an account is named
.ci/fixtures/lavish-axi-31-CHANGELOG.md	cache	2	lavish-axi#31's CHANGELOG.md at HEAD, byte for byte; test-guard-generated-files.sh asserts its blob id against the sha GitHub reports for that pull request, so scrubbing the two fork-point links breaks the check it exists to prove
.github/workflows/shared-no-mistakes-required.yml	literal	1	the marker the no-mistakes binary writes; editing it reds every caller's PR gate
.ci/test-no-mistakes-required.sh	literal	1	the same marker, pinned so the workflow's copy cannot drift
DECL

owners=$(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST" | sort -u)

# An empty owner set would pass every case below without reading a single file,
# which is the exact shape of gate this repository keeps finding. Refuse first.
if [ -z "$owners" ]; then
    printf 'FAIL no upstream owners parsed from %s - the check would pass vacuously\n' "$MANIFEST"
    exit 1
fi

# One grep per owner over the whole repository. `--untracked` is load-bearing: a
# brand-new file is exactly the case this exists to catch, and a new file is
# untracked until it is added. Without it a planted reference passed, measured
# while falsifying this check. It still skips .gitignored paths, so build output
# and evidence logs stay out. `git grep -c` counts matching LINES, which is what
# the declared counts mean.
hits=$(
    for o in $owners; do git grep -I --untracked -ic -- "$o" || true; done \
        | awk -F: '{ n[$1] += $2 } END { for (f in n) printf "%s\t%s\n", f, n[f] }' \
        | sort
)

# Resolve a hit file to the declaration covering it: an exact row wins, then a
# directory-prefix row. Prints "<declared path>\t<class>\t<max>", or nothing.
declared_for() { # <path>
    printf '%s\n' "$DECLARED" | awk -F'\t' -v p="$1" '
        $1 == p { print $1 "\t" $2 "\t" $3; exact = 1; exit }
        substr($1, length($1)) == "/" && index(p, $1) == 1 { pre = $1 "\t" $2 "\t" $3 }
        END { if (!exact && pre != "") print pre }'
}

failed=0
matched_decls=""

while IFS=$'\t' read -r file count; do
    [ -n "$file" ] || continue
    IFS=$'\t' read -r dpath class max <<<"$(declared_for "$file")"
    if [ -z "${dpath:-}" ]; then
        printf 'FAIL %s carries an upstream owner and is not declared (%s line(s))\n' "$file" "$count"
        printf '     Point it at our own build, or add a row above with one of\n'
        printf '     record / cache / literal / gap, and the reason.\n'
        failed=1
        continue
    fi
    matched_decls="$matched_decls$dpath
"
    if [ "$max" != '*' ] && [ "$count" -gt "$max" ]; then
        printf 'FAIL %s: %s line(s) carry an upstream owner, declared at most %s (%s)\n' \
            "$file" "$count" "$max" "$class"
        printf '     A declared file is not a licence to add more; raise the cap only with a reason.\n'
        failed=1
        continue
    fi
    printf 'ok   %-70s %s(%s)\n' "$file" "$class" "$count"
done <<HITS
$hits
HITS

# The other half: a declaration nobody matched is a claim about a reference that
# is no longer there. Left standing it becomes cover for the next one to move
# into that file unnoticed.
while IFS=$'\t' read -r path class _max _reason; do
    [ -n "$path" ] || continue
    printf '%s' "$matched_decls" | grep -qxF "$path" && continue
    printf 'FAIL declared %s (%s) but no file there carries an upstream owner - drop the row\n' \
        "$path" "$class"
    failed=1
done <<DECLS
$DECLARED
DECLS

if [ "$failed" -eq 0 ]; then
    printf '\nupstream-owner refs ok: %s owner(s) from %s, every occurrence declared\n' \
        "$(printf '%s\n' "$owners" | wc -l | tr -d ' ')" "$MANIFEST"
fi
exit "$failed"
