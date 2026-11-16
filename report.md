# gates-issue-16 — npm lockfile entries osv-scanner judges by a lie (scout report)

Task: `factory-p3a-sr3f2-lockfile-lies` (sr3-3, sr3-5, sr3-8, sr3-9; S2).
Base: `main` at `1739f4557dad8e59583bda236eb3b380b25a97ea`. Branch: `fac-remote/gates-issue-16`.
Deliverable: this report only. No code under `run/`, `.ci/` or `policy/` is changed on this branch.

## What I could not do (read first)

1. **I could not read the source report** (`data/factory-p3a-sr3-security-rereview/report.md`, section 6). It is in the private repository, and this session can reach only `gates`.
   I could not map sr3-3, sr3-5, sr3-8 and sr3-9 one by one to the cases below. I took the defects from the backlog goal text and measured each one.
2. **I could not inspect or measure the factory "sweep" or its `Locks` walk.** It is not in this repository: `grep -rn "Locks\b"` and `grep -rni sweep` over the tree find no lockfile sweep (results below).
   For the factory half I can only give the oracle the sweep has to match, which is osv-scanner 2.7.0's measured directory skip set (section 3.4).
3. **I tested against a synthetic database, not the real OSV one.** It is a one-advisory `all.zip` (`GHSA-test-0001`: npm `lodash`, introduced 0, fixed 4.17.21) at `$DB/osv-scalibr/npm/all.zip`, the layout `.ci/test-tool-gates.py --real` uses.
   The verdicts below show what osv-scanner reads from the lockfile. They do not show real advisory coverage.

## What I did

- Read `run/tool-gates.py` (the `osv` row: `undecided_npm_entries` at lines 332–360, `lockfiles` at 363–372, `osv` at 375–425), the README's "What the rows do not read" (README.md:545–560), the `advisories-osv*` rows in `.ci/gate-registry.toml:957–1025`, and `run/permissions-diff.py:338–350, 475`.
- Downloaded the pinned `osv-scanner_linux_amd64` v2.7.0 release asset. Its sha256 is `160c6253…218b`, which is the hash README.md:540 pins.
- Built fixture trees and ran each one twice:
  - through osv-scanner directly, with the exact flags the gate uses (`scan source --config policy/osv-scanner.toml --offline --offline-vulnerabilities --no-ignore -r .`);
  - through the gate (`python3 run/tool-gates.py osv <tree>`, with the real binary on `PATH`).
- Wrote a candidate stricter predicate and ran it over every npm lockfile this repository tracks, to check that it does not refuse real npm output.
- Ran the existing suite as a baseline: `python3 .ci/test-tool-gates.py` → `test-tool-gates (stub): 90 of 90 cases passed`.

## 1. Findings

The gate's pre-check (`run/tool-gates.py:352–359`) refuses an entry only when all of these hold: `"version" in entry`, no truthy `resolved`, and no truthy `link`.
Every entry outside that shape goes to osv-scanner, which trusts the lockfile's `version` (and `name`) fields and never checks them against `resolved`.

All rows below are measured. "osv" is osv-scanner's exit code; "gate" is the last line `tool-gates.py osv` printed.
Control rows: `good` (lodash 4.17.15, matching resolved) gives osv=1 and the gate reports known vulnerabilities. `fixed` (4.17.21, matching) gives osv=0 and the gate passes.

| # | lockfile entry (key `node_modules/lodash` unless stated) | osv | gate verdict | should be |
|---|---|---|---|---|
| F1 | `{"resolved": ".../lodash-4.17.15.tgz"}`, **no version** | 0 | **pass** ("no known vulnerabilities") | undecided |
| F1 | `{}`, empty entry | 0 | **pass** | undecided |
| F1 | v1 `dependencies.lodash` with resolved and no version | 0 | **pass** | undecided |
| F2 | `version 4.17.21`, **resolved `lodash-4.17.15.tgz`** (npm installs the tarball, osv judges 4.17.21) | 0 | **pass** | undecided |
| F2 | the same in a v1 `dependencies` map | 0 | **pass** | undecided |
| F2 | key `node_modules/ms`, version 2.1.3, **resolved `lodash-4.17.15.tgz`** | 0 | **pass** | undecided |
| F2 | version 4.17.21, resolved `git+ssh://…lodash.git#abc1234` | 0 | **pass** | undecided |
| F2 | version 4.17.21, resolved `file:vendor/lodash-4.17.15.tgz` | 0 | **pass** | undecided |
| F3 | `{"version": "4.17.15", "link": true}` **beside a real entry** (`ms`) | 0 | **pass**: osv reads 0 packages from it | undecided |
| F3 | `link: true` + version + registry resolved, beside a real entry | 0 | **pass** | undecided |
| F3 | the same with no other entry in the lock | 128 | undecided ("no package source"), only by luck | undecided |
| ok | npm alias `node_modules/ms` with `"name": "lodash"`, matching lodash resolved | 1 | finding: osv uses `name` | finding |
| ok | `"version": 4` (not a string) alone | 128 | undecided | undecided |
| ok | `"version": 4` in `web/`, beside a clean root lock | 127 | refused `tool-error` | undecided (clearer message) |

Evidence lines from osv-scanner's own output:

```
Scanned fx/noversion/package-lock.json file and found 1 package        -> exit 0, no row
Scanned fx/mismatch/package-lock.json file and found 1 package         -> exit 0, no row
Scanned fx/fakelink/package-lock.json file and found 0 packages        -> exit 128
| https://osv.dev/GHSA-test-0001 | | npm | lodash | 4.17.15 | 4.17.21 | package-lock.json |   (control: good)
| https://osv.dev/GHSA-test-0001 | | npm | lodash | 4.17.15 | 4.17.21 | package-lock.json |   (alias: name field honoured)
```

- **F1, no version.** osv-scanner counts the package but has no version to match, so it reports nothing.
  The gate's guard `"version" in entry` (`run/tool-gates.py:356`) skips exactly these entries.
  README.md:557 describes the rule as "has a `version`, no `resolved` and no `link`", so the doc carries the same hole.
- **F2, mismatched resolved.** osv judges the declared `version` (and the key's or `name`'s package), never the tarball `npm ci` fetches.
  A change can pin a vulnerable tarball under a fixed version's label, or another package's tarball under a harmless name, and the row reads it clean.
  The gate never reads `resolved` beyond "is it truthy" (`run/tool-gates.py:357`).
- **F3, `link: true` exempts any entry.** `not entry.get("link")` (`run/tool-gates.py:358`) trusts the flag.
  osv 2.7.0 drops link entries, so a registry package marked `link: true` disappears from the scan whenever the lock has another real entry.
  A real npm workspace link has no version, no integrity, and a relative in-tree `resolved` (`.ci/test-tool-gates.py:646`: `{"resolved": "packages/a", "link": true}`).
- The non-string version case is already refused (127 or 128). Only its message is unclear.

## 2. How severe

These are bypasses of a Security row (`advisories-osv*` are in `SECURITY_ROWS`, `run/permissions-diff.py:346–348`).
A one-line lockfile edit makes `osv` read clean over a known-vulnerable install, and every variant exits 0 with "no known vulnerabilities".
F1 and F2 need no exotic shape. F3 needs a lockfile with at least one other entry, which every real lockfile has.

## 3. Walk and skip sets

### 3.1 Search for the sweep in this repository

```
$ grep -rn "Locks\b" --exclude-dir=.git . | grep -v corpus     -> no match
$ grep -rni "sweep" ...    -> README prose only (fact-change sweep, fakes sweep); no lockfile sweep
```

### 3.2 The gate's own walkers skip only `.git`

`undecided_npm_entries` (`run/tool-gates.py:336`) and `lockfiles` (`:367`) both run `dirs[:] = [d for d in dirs if d != ".git"]`.

### 3.3 osv-scanner 2.7.0's measured skip set

Each tree had a clean root lock plus a vulnerable lock in the named directory:

```
node_modules       osv=0  (skipped)        node_modules/a   osv=0 (skipped)
pkg/node_modules   osv=0  (skipped)        Node_Modules     osv=1 (walked; match is case-sensitive)
vendor .venv target dist build .hidden third_party bower_components .yarn .pnpm-store __pycache__  osv=1 (walked)
.git/x             osv=1  (walked: osv does NOT skip .git)
```

A root directory that is itself named `node_modules` is skipped whole (osv=128). This does not matter for a checkout.

### 3.4 What this means for the gate and for the sweep

- **Gate:** the mismatch is safe in direction. A lockfile under `node_modules` is pre-checked by the gate and listed by `lockfiles()`, but osv ignores it, so the worst case is an extra `undecided` refusal.
  osv walks `.git` and the gate does not, but a checkout's `.git` holds no lockfile, so nothing is lost there.
- **Sweep (factory, not inspected):** if the sweep's `Locks` decides which lockfiles are covered, its skip set has to equal the scanner's. Against 2.7.0 that means skipping only directories named exactly `node_modules`, case-sensitive, at any depth.
  Skipping `.git` matches nothing osv reads in practice. Any further name the sweep skips (vendor, target, …) is a lockfile osv judges but the sweep would not account for.
  If the sweep's job is to find lockfiles osv does not read, then directories under `node_modules` are precisely the ones it must list, not skip.
  I cannot tell which of these readings the backlog's "skips only `.git` and `node_modules`" points at without the report.
  The measured set above is the oracle either way. Re-measure it on each osv-scanner pin bump.

## 4. Recommendation

Replace the predicate at `run/tool-gates.py:352–359` with a rule that makes "decided" positive: an entry is judged only when its lockfile fields cannot lie to osv-scanner.
The candidate I tested (scratch, not committed):

```python
def why_undecided(key, entry):
    v, r = entry.get("version"), entry.get("resolved")
    if entry.get("link"):
        if "version" in entry or "integrity" in entry:
            return "is a link that also carries a version or integrity"
        if not isinstance(r, str) or urlsplit(r).scheme or r.startswith(("/", "..")) or "/../" in r:
            return "is a link whose resolved is not a relative path inside the tree"
        return None
    if not isinstance(v, str) or not v:          return "has no version"
    if not r:                                     return "has a version and no resolved URL"
    if not isinstance(r, str) or not r.startswith(REGISTRY):
        return "resolves outside the registry"
    name = entry.get("name") or key.rsplit("node_modules/", 1)[-1]   # v1: the map key
    if r != f"{REGISTRY}{name}/-/{name.rsplit('/', 1)[-1]}-{v}.tgz":
        return f"has version {v} but resolves to {r}"
```

Results:

- **Fixtures:** it refuses every F1, F2 and F3 row above. It passes `good`, `fixed` and the npm alias. It turns the non-string version into "has no version".
- **No regression on real npm output:** all 229 entries in this repository's 7 tracked npm lockfiles pass, scoped `@babel/*` included:
  ```
  run/mutation-judge/package-lock.json: 222 entries, 0 undecided
  corpus/licence/**/package-lock.json:   3–4 entries each, 0 undecided
  ```
  The existing protected rows' `clean` inputs stay clean. `advisories-osv` (`.ci/gate-registry.toml:964–970`) uses `{}`, which has no entries. `advisories-osv-undecided` (`:990–996`) and `-v1` (`:1017`) carry a matching resolved, and so does `ci.yml:98`.
  **No existing `SECURITY_ROWS` row needs editing.**

Things the implementer has to decide or do:

1. **Registry allowlist.** A caller on a private registry (GitHub Packages, Verdaccio, Artifactory) would be refused.
   Read the accepted registry prefixes from a `policy/` file (already on the protected surface, `run/permissions-diff.py:474`) rather than from the tree under review, defaulting to `https://registry.npmjs.org/`.
   Whether npm ever writes a registry tarball name that is not `<base>-<version>.tgz` (build metadata, for example) was not measured. Refusal is the safe failure.
2. **Git and `file:` dependencies** are refused by this rule. osv 2.7.0 judges a git dependency by its declared semver here, not by the commit.
   If a caller needs them, that is a recorded relaxation, not a pass.
3. **Tests.** Add to `.ci/test-tool-gates.py`:
   - stub cases for each F row: no version (v2/v3 and v1), empty entry, version/resolved mismatch (v2/v3, v1, other-name), git and file resolved, `link: true` with a version beside a real entry, and a link with an absolute or URL resolved;
   - pass cases for a scoped package and an npm alias;
   - `--real` cases mirroring F1–F3 beside a real entry, so a future osv pin that starts checking these shows up.
4. **Gate proof.** Add **new** `.ci/gate-registry.toml` rows (for example `advisories-osv-undecided-noversion`, `-mismatch`, `-link`) with red/clean inputs shaped like the existing `advisories-osv-undecided`, and add the ids to `SECURITY_ROWS`.
   New rows are additions. Only an edit to an existing protected row is class-2.
5. **Class-2 decision.** `run/tool-gates.py` is on the permissions-diff surface (`run/permissions-diff.py:475`), so this change needs a class-2 decision once a caller has promoted that gate out of shadow. Land it before P3a-M6 shadows these rows, as the backlog says.
6. **Docs.** Rewrite README.md:557–560 and the module docstring (`run/tool-gates.py:14–17`) to state the positive rule. Keep the line "Whether npm ever writes a registry package without `resolved` was not measured".
7. **Sweep (factory).** Make its lockfile skip set equal osv's measured set (`node_modules` exactly, case-sensitive, any depth) and pin that set with a test. Make it apply the same per-entry predicate, so the sweep and the gate cannot disagree about which entries are judged. Re-measure section 3.3 on each osv-scanner pin bump.

## Reproduce

```bash
S=$(mktemp -d); curl -sSL -o $S/osv-scanner https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64
sha256sum $S/osv-scanner   # 160c6253…218b, as README.md:540
chmod +x $S/osv-scanner; mkdir -p $S/db/osv-scalibr/npm
# all.zip holding one OSV record: npm lodash, introduced 0, fixed 4.17.21
mkdir $S/t && echo '{"lockfileVersion":3,"packages":{"":{"name":"fx","version":"1.0.0"},"node_modules/lodash":{"version":"4.17.21","resolved":"https://registry.npmjs.org/lodash/-/lodash-4.17.15.tgz"}}}' > $S/t/package-lock.json
PATH=$S:$PATH OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$S/db python3 run/tool-gates.py osv $S/t
# -> tool-gates: osv-scanner 2.7.0: no known vulnerabilities (1 database file(s))
```
