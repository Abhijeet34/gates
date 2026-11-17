# gates-issue-16-2: npm lockfile lies past `advisories-osv`, and the lockfile walk's skip set

Scout report for backlog `factory-p3a-sr3f2-lockfile-lies` (findings sr3-3, sr3-5, sr3-8, sr3-9).
Base: `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`. No code was changed; this file is the only deliverable.

## What I could not do (read first)

- **The "sweep" and its `Locks` walker are not in this repository.** `grep -rn "Locks\|class Locks"` over the tree finds nothing, and no file here owns a sweep of lockfiles; it lives on the factory side, which this session cannot reach.
  So the sweep half of the task (sr3-8/sr3-9, "Locks skips only .git and node_modules") is answered only by measuring osv-scanner's own skip set, which is what the sweep's skip set has to agree with (below, finding 3). Its code was not read and not changed.
- The security re-review report the backlog cites (`data/factory-p3a-sr3-security-rereview/report.md`) is not in this repository either, so I mapped the findings from the backlog goal text, not from the report. The sr3-N labels below follow that text and may not match the report's numbering exactly.
- Not tested: `link: true` entries with a registry `resolved`. One attempt failed because of a mistake in how I built the fixture, so it shows nothing either way.

## What I did

1. Read `run/tool-gates.py` (the `osv` check), its tests `.ci/test-tool-gates.py`, the `advisories-osv*` rows in `.ci/gate-registry.toml` and README.md, "The tool rows".
2. Downloaded the pinned **osv-scanner 2.7.0** (`osv-scanner_linux_amd64`, sha256 `160c6253…4218b`, which matches README.md:604) and let it download a fresh npm database (`osv-scalibr/npm/all.zip`, 221 MB, 2026-11-17).
3. Ran a probe script over 30-odd fixture trees. It ran osv-scanner with exactly the gate's flags (`scan source --config policy/osv-scanner.toml --offline --offline-vulnerabilities --no-ignore -r .`, plus `--format json` so I could read which packages it judged), and it ran `run/tool-gates.py osv .` on the same tree.
4. Checked what npm 10.9.9 actually installs from the lockfiles that lie, using `npm ci --ignore-scripts` against the public registry.
5. Baseline: `python3 .ci/test-tool-gates.py` → `test-tool-gates (stub): 90 of 90 cases passed`.

Lodash 4.17.15 has 5 advisories in the database. **4.18.1 has none.** 4.17.21 now has 2 (fixed in 4.17.23 and 4.18.0), so it is no longer a clean control.

## Findings

### 1. An entry with no `version` is passed clean, and npm installs it (sr3-3; S2)

`undecided_npm_entries` (`run/tool-gates.py:352-359`) flags an entry only when it has a `"version"`. osv-scanner 2.7.0 silently drops an entry without one. So the gate passes it, and the gate's own docstring (`run/tool-gates.py:14-16`) and README.md:621 describe only the version-but-no-`resolved` shape.

| Fixture (resolved = lodash 4.17.15 tarball) | osv rc / packages judged | gate |
|---|---|---|
| v3 `node_modules/lodash: {resolved, integrity}` | `0`, `[]` | `0` "no known vulnerabilities" |
| v3 `node_modules/lodash: {integrity}` only | `0`, `[]` | `0` |
| v1 `dependencies.lodash: {resolved}` | `0`, `[]` | `0` |
| control: v3 `{version: 4.17.15, resolved}` | `1`, `[lodash 4.17.15, 5 vulns]` | `1` |

What npm does with these lockfiles:

```
v1-noversion: npm ci -> npm warn old lockfile  added 1 package | installed=4.17.15 | lock changed: no
v3-noversion: npm ci -> npm error code EUSAGE ... lock file's lodash@ does not satisfy lodash@4.18.1
              npm install -> installed 4.18.1, rewrites the lock
```

A v1 lockfile with no `version` installs the vulnerable tarball under `npm ci`, while the gate reports a clean tree. npm ci refuses the v3 shape, but the gate still calls a tree it never judged clean, which is the "undecided, never a quiet pass" contract (`run/tool-gates.py:2`) broken either way.

### 2. A `resolved` that disagrees with `version` is judged by the claimed version, and npm installs the resolved one (sr3-5; S2)

osv-scanner judges `version` (and `name`) only. It never reads `resolved`. The gate checks only that `resolved` is non-empty (`run/tool-gates.py:357`).

| Fixture | osv rc / packages | gate |
|---|---|---|
| v3 `{version: 4.18.1, resolved: lodash-4.17.15.tgz, integrity}` | `0`, `[]` | `0` |
| v1 `{version: 4.18.1, resolved: lodash-4.17.15.tgz}` | `0`, `[]` | `0` |
| v3 `{version: 4.18.1, resolved: …/minimist/-/minimist-0.0.8.tgz}` (another package) | `0`, `[]` | `0` |
| v3 `{version: 4.18.1, resolved: https://evil.example/lodash-4.17.15.tgz}` | `0`, `[]` | `0` |
| v3 `{version: 4.17.15, resolved: git+ssh://…#abc}` | `0`, `[]` | `0` |
| v3 `{version: 4.17.15, resolved: file:lodash-4.17.15.tgz}` | `0`, `[]` | `0` |

The last two show that osv-scanner 2.7.0 also skips git and `file:` sources outright, even when the version it was given is a vulnerable one.

What npm does with these lockfiles, with the integrity of the real 4.17.15 tarball:

```
v3-mismatch: npm ci -> added 1 package | installed=4.17.15 | lock changed: no
v1-mismatch: npm ci -> npm warn old lockfile  added 1 package | installed=4.17.15 | lock changed: no
```

So a lockfile that says 4.18.1, points at the 4.17.15 tarball and carries that tarball's integrity installs 4.17.15 under `npm ci`, with no warning about the mismatch. Both osv-scanner and the gate pass it. This is the more serious of the two holes, because npm ci accepts it in both lockfile shapes.

The alias case, `node_modules/notlodash: {name: lodash, version: 4.17.15}`, is judged correctly: osv reads `name` and finds the 5 advisories.

### 3. Skip sets: osv-scanner skips exactly `node_modules`, the gate's walks skip exactly `.git` (sr3-8, sr3-9)

I made one tree with a vulnerable `x/package-lock.json` under each of 17 directory names. osv-scanner 2.7.0 judged every one of them except the one under `node_modules`. The ones it judged include `.git`, `NODE_MODULES`, `vendor`, `bower_components`, `.venv`, `venv`, `site-packages`, `dist`, `build`, `target`, `__pycache__`, `.yarn`, `.pnpm-store`, `jspm_packages`, `.hg` and `.svn`.

So osv's skip set is `{node_modules}` (case-sensitive, at any depth). `undecided_npm_entries` and `lockfiles` skip `{.git}` (`run/tool-gates.py:336`, `:367`).

What follows:

| Fixture | osv | gate |
|---|---|---|
| only `node_modules/x/package-lock.json` (vulnerable) | `128` no package source | `2 undecided`: caught only because `lockfiles()` sees it |
| clean root lock + `node_modules/x/package-lock.json` (vulnerable, with `resolved`) | `0`, the nested lock is never read | **`0` pass** |
| clean root lock + `a/node_modules/b/npm-shrinkwrap.json` (vulnerable) | `0` | **`0` pass** |
| clean root lock + `node_modules/x/package-lock.json` (no `resolved`) | `0` | `2 undecided`: the precheck walks node_modules |
| clean root lock + `vendor/x/…` or `.cache/x/…` (vulnerable) | `1` | `1` |

The exit-128 guard (`run/tool-gates.py:409-415`) catches a lockfile under `node_modules` only when it is the tree's only package source. Next to any other lockfile, it is silently unjudged.

This is lower severity than findings 1 and 2. npm does not install from a lockfile nested in `node_modules`, except for a dependency's own `npm-shrinkwrap.json`, which npm does honour. But a committed `node_modules/` holds code that ships, and the gate claims nothing under it was skipped.

For the factory sweep: if its `Locks` walker skips `.git` and `node_modules`, it matches osv on `node_modules` and so never sees the gap either. The two directions need different answers:
- A walker that **predicts what osv will skip** (the "undecided" precheck) must *not* skip `node_modules`. It must flag any npm lockfile found there as undecided, because that is exactly what osv drops.
- A walker that **lists what osv reads** must skip `node_modules` and nothing else. In particular it must not skip `vendor`, dot-directories, `dist` or `build`, all of which osv reads.

Skipping `.git` is harmless for a checked-out tree, since nothing tracked lives there. osv itself does read `.git/**`.

### 4. Smaller observations

- An entry whose key has no `node_modules/` (`packages."vendor/lodash": {version: 4.17.15}` with no `resolved`) passes both osv and the gate. npm treats such a key as a local folder whose source is in the tree, as with workspaces (`packages/a`). I believe that is correct and did not count it as a finding, but the precheck's `"node_modules/" in key` filter (`run/tool-gates.py:350`) is what makes it so, and it should say why in a comment.
- A non-string `version` makes osv fail to parse the file (`cannot unmarshal number … of type string`) and exit 128. The gate refuses `undecided` only through the exit-128 guard. Next to another lockfile, osv logs the extraction error and goes on, and the gate passes. Not reproduced with a second lockfile; worth one probe in the fix.

## Recommendation

Fix it in `run/tool-gates.py` `undecided_npm_entries`, so it reaches callers through a lock bump. Under the npm-specific rules below, treat an entry as **undecided** unless all of these hold:

1. It is a workspace link: `link` is truthy and `resolved` is a relative path that does not start with `http`, `git` or `file:`. Otherwise:
2. `version` is present and is a string, **and**
3. `resolved` is an `http(s)` tarball URL whose path ends `/<name>/-/<basename>-<version>.tgz`, where `<name>` (scoped `@s/n` or bare) equals `entry.get("name")`, or else the last `node_modules/` segment of the key or the v1 map key, and `<version>` equals `version`. Compare the path only, not the host, so private registries and mirrors still pass. A host allowlist could be added under `policy/` if wanted, but that is a design decision.
4. Refuse git, `file:` and other non-tarball `resolved` values as undecided too, since osv skips them (finding 2, last two rows).

Also:
- Walk `node_modules` in `undecided_npm_entries` and flag **any** `package-lock.json` or `npm-shrinkwrap.json` under a `node_modules` directory (not the key form) as undecided, because osv 2.7.0 never reads it (finding 3). Decide separately whether `node_modules/.package-lock.json`, npm's hidden lock, belongs in that rule; I did not measure whether osv parses it.
- Update the docstring (`run/tool-gates.py:13-18`, `:39-42`) and README.md:621-624 to state the full rule. README.md:624 ("Whether npm ever writes a registry package without `resolved` was not measured") can now cite the npm ci results above.
- Tests in `.ci/test-tool-gates.py`, stub mode next to lines 555-650, one case for each fixture row above that currently passes: v3 and v1 with no version; v3 and v1 with a mismatched version; another package's tarball; git and `file:` resolved; a lockfile under `node_modules` next to a clean root lock. Add a clean control for a scoped package (`@s/n/-/n-1.0.0.tgz`) and a passing workspace link. In the `--real` list (around line 849), add the mismatch and no-version cases with the 4.18.1 / 4.17.15 pair.
- Registry: one red input per new refusal type in `.ci/gate-registry.toml`, next to `advisories-osv-undecided` (line 979). For example, a `{"version": "4.18.1", "resolved": ".../lodash-4.17.15.tgz"}` red with the existing clean, so `gates-proof` shows the neutered command lets it through. Note that `run/tool-gates.py` sits in 7.2's protected surface (README.md:181), so the change needs whatever decision class that surface requires once the row leaves shadow.
- The factory sweep's `Locks`: apply the two-direction rule in finding 3, and share one skip-set constant with `tool-gates.py`, or test the two against each other, so they cannot drift again.

Order: findings 2 and 1 first, because npm ci installs the vulnerable code while the gate passes. Then finding 3. All of this should land before P3a-M6 puts the rows in shadow, as the backlog says.

## How to reproduce

The probe script lives outside the repository. Rebuild it from these steps:

```bash
curl -fsSLO https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64   # sha256 160c6253…4218b
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$DB ./osv-scanner_linux_amd64 scan source --offline-vulnerabilities --download-offline-databases -r <tree-with-npm-lock>
# DB layout written by 2.7.0: $DB/osv-scalibr/npm/all.zip
# then, per fixture tree:
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$DB PATH=<dir with osv-scanner>:$PATH python3 run/tool-gates.py osv .
# npm side (needs registry access):
I=$(npm view lodash@4.17.15 dist.integrity)
# lockfile v3: "node_modules/lodash": {"version":"4.18.1","resolved":"https://registry.npmjs.org/lodash/-/lodash-4.17.15.tgz","integrity":"$I"}
npm ci --ignore-scripts && node -p 'require("./node_modules/lodash/package.json").version'   # prints 4.17.15
```
