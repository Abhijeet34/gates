# gates-issue-3 — can gates tag itself so the updater can bump callers?

Backlog id `gates-tagging-for-bumps-t4`, scout. Measured 2026-11-16 against `main` at `1739f45`.

## What I did not do, and why

**I did not run the throwaway-tag experiment** (a workflow tagging a commit with gates' own `GITHUB_TOKEN`, then deleting the tag). Here is why:

1. **The question it answers is no longer the blocker.** The backlog item (found 2026-09-17) says "gates publishes no releases and no tags." That stopped being true on 2026-10-16. gates now has eight `vN` tags, each with a published GitHub release. All of them are on `main`, and the README documents them as the release mechanism (evidence below). So the updater has a version order to work with, whichever credential cuts the tags.
2. **It would have meant shipping a write-token workflow in a public repository.** Every workflow here grants only `contents: read`. A tagging job needs `contents: write`. `run/permissions-diff.py` treats a workflow's `permissions:` block as protected surface (`run/permissions-diff.py:123`, `:195`: "a permissions: block changed"). A tag is also seen at once by every caller's Dependabot, even a throwaway one: a stray `v9`, even briefly, could be proposed as a bump. Nothing gained justifies that.
3. A push from this session uses the session's credential, not gates' Actions `GITHUB_TOKEN`, so it would not have answered the question either.

So the narrow question "can gates' own `GITHUB_TOKEN` create a tag?" is **still untested**. Nothing on record shows it either way: no tag or release was ever made by `github-actions[bot]`.

## What I found

### 1. gates has been tagged since 2026-10-16

```
$ git ls-remote --tags origin        # refs/tags/v1 … refs/tags/v8
$ for t in v1..v8; git merge-base --is-ancestor $t^{commit} origin/main
v1 on-main 2026-10-16    v5 on-main 2026-10-26
v2 on-main 2026-10-21    v6 on-main 2026-11-02
v3 on-main 2026-10-22    v7 on-main 2026-11-02
v4 on-main 2026-10-25    v8 on-main 2026-11-03
commits on main since v8: 17 (latest 1739f45, 2026-11-14)
```

### 2. Who cut them: the builder App and the maintainer, never the Actions token

From the GitHub releases list (`list_releases`) and `git for-each-ref refs/tags`:

| Tag | Release author | Tag object |
| --- | --- | --- |
| v1 | `hw-bodhi[bot]` (App id 343227966) | lightweight |
| v2–v5 | `hw-bodhi[bot]` | annotated, tagger `hw-bodhi[bot]` |
| v6–v8 | `Abhijeet34` (the maintainer) | lightweight |

`hw-bodhi[bot]` is the same identity `run/bump-identity` names as the only allowed lock-bump author (`run/bump-identity`, last line: `hw-bodhi[bot] 343227966`).

### 3. The release policy the backlog called "a decision" is already written down

`README.md:82-87`, "Pinning":

- `:82` — "The pin is a commit with a `# vN` comment, because Dependabot's `github-actions` updater proposes a new commit and rewrites the comment from the `vN` tags this repository cuts."
- `:83` — "A release is a tag on a commit of `main` whose `checks` run is green."
- `:84` — the procedure: `gh workflow run ci.yml --ref main`. Then, once `checks` passes on that commit, `gh release create vN --target <that commit> --generate-notes`.
- `:87` — "tags are never moved."

`.github/workflows/ci.yml:19-23` adds `workflow_dispatch` so that a release commit gets the green `checks` run `run/lock.sh` requires.

So the version scheme (`vN`), what a tag means (a green `main` commit) and the immutability rule are all settled. Cadence is manual and unstated.

## What this means for the original goal

- **The updater's stated reason for declining (no tags) is gone.** The 2026-09-17 run that answered `Latest version is ` (empty) was against a tagless gates. That run is not evidence about today's gates.
- **Still unmeasured:** whether a caller pinned as `@<sha> # vN` now gets a Dependabot bump to `v8`. That lives in caller repositories, which this session cannot read. It is the real follow-up experiment.
- **Gap that remains:** releases are cut by hand (or by the builder App). Tags arrive only when someone cuts one. Seventeen commits have landed on `main` since `v8` (2026-11-03) without a release. Unattended propagation is therefore bounded by release cadence, not by Dependabot's schedule alone.
- The backlog's caveat still stands: even with tags, the updater runs at best daily, so an urgent fix still travels by the coordinated sweep or `bin/gates.py bump`.

## Recommendations

1. **Close the "can gates tag itself" hypothesis as moot.** Tagging already works, through the maintainer and `hw-bodhi[bot]`. No new credential and no `contents: write` workflow is needed, and adding one would widen gates' security surface for nothing.
2. **Run the real experiment in one caller:** pin a gates bootstrap at `v7`'s commit with `# v7`, trigger Dependabot's `github-actions` update, and check that it proposes `v8` with the comment rewritten. Use the same control as the original run (another action bumped in the same job).
3. **If that works, decide release cadence** (e.g. cut `vN` after each green `main` merge that touches `run/` or a `shared-*.yml`, or weekly). If cadence should be unattended, give the job to `hw-bodhi[bot]`, which already cut v1–v5, rather than to the Actions `GITHUB_TOKEN`.
4. Consider cutting `v9` now: `main` is 17 commits past `v8`.
5. Only if (3) specifically needs the Actions token should the throwaway-tag test be run. It would need a dispatched, `contents: write`, one-job workflow on a branch, with a class-2 decision for the permissions surface. That test is still open.
