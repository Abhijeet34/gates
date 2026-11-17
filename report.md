# Scout report — `gates-issue-19` (P3a-SR3 S3/S4 findings to fold into owning rows)

**Verdict: blocked.** The one input this task depends on — the P3a-SR3 security
re-review report, cited as `data/factory-p3a-sr3-security-rereview/report.md`
section 6 and the finding sections it names — is not present in the task
repository (`gates`) and does not exist anywhere in the history of the private
source repository (`automation`) either. Without that report I cannot read the
text, severity, or owning row of any of the named findings, so I cannot fold
them into "each owning row's next change." The honest deliverable is therefore
this account of what was searched, what was found, and exactly what is needed to
unblock — not an invented set of finding-to-row mappings.

---

## 1. What the task asked for

- Backlog id `factory-p3a-sr3-later-touch`, kind defect, priority 3, delivery
  `report` (a scout: the deliverable is a written report, not a change).
- Goal: take the P3a-SR3 S3/S4 findings — `sr3-10` … `sr3-17`, `sr2-12`,
  `sr2-14` … `sr2-16`, `SR-16`, `SR-18`, `SR-22`, `SR-23`, `SR-25` — and fold
  each into its owning row's next change, landing before milestone P3a-M6 puts
  any of those rows in shadow.
- Source named by the contract: `data/factory-p3a-sr3-security-rereview/report.md`
  section 6 "(and the finding sections it names)."

Folding a finding requires three things from the source report, per finding: the
finding's text (what the defect/observation is), its severity (why it is S3 or
S4), and the row it belongs to (section 6 is described as the part that routes
each finding to an owning row). None of these three are available, because the
report is not reachable from this session. The rest of this report establishes
that absence with evidence, then records what *is* verifiable so the work can be
completed as a single pass the moment the report is supplied.

## 2. The source report is absent from the task repo (`gates`)

`gates` is a public, history-free snapshot (`AGENTS.md:3`). There is no `data/`
directory, and none of the finding IDs, the report path, or the review's name
appear anywhere in the tree.

```
$ ls -d data 2>/dev/null            # no output: directory does not exist
$ for id in sr3-10 sr3-17 sr2-12 sr2-14 SR-16 SR-18 SR-22 SR-23 SR-25 SR3 \
            "S3/S4" factory-p3a-sr3 security-rereview; do
    echo "$id: $(grep -rln -- "$id" . --exclude-dir=.git | wc -l) files"; done
sr3-10: 0 files
sr3-17: 0 files
sr2-12: 0 files
sr2-14: 0 files
SR-16: 0 files
SR-18: 0 files
SR-22: 0 files
SR-23: 0 files
SR-25: 0 files
SR3: 0 files
S3/S4: 0 files
factory-p3a-sr3: 0 files
security-rereview: 0 files
```

The only `P3a` strings present are design-of-record pointers in `README.md`
(plan rows `P3a-ROWS`, `P3a-RT`, `P3a-RP`, `P3a-CRIT`, `P3a-LIC`, `P3a-M6`) and a
hash fragment in `run/mutation-judge/package-lock.json`. None is a finding or a
re-review report. This matches the repository's stated design: it "is public, so
tracked text names no private repository, alert count, run id or local path," and
"the private `automation` repository holds the history and the dated records
these files cite" (`AGENTS.md:3-6`). The P3a-SR3 report is exactly such a dated
record.

## 3. The source report never existed in the private repo (`automation`) either

The private source repository was added to this session's scope and cloned
(shallow, then deepened to the full 675-commit history of `main`). The report
path and the finding IDs are absent from the working tree and from every commit
reachable on `main`.

```
$ git -C <automation> log --oneline origin/main | wc -l
675
$ ls <automation>/data
fleet-stale-branch-sweep-s2          # the ONLY entry in data/

# Any commit that ever touched the report path or an SR3/re-review artifact:
$ git -C <automation> log --all --oneline --name-only \
    | grep -iE "factory-p3a|sr3|security-rereview|re-?review"
(no matching path in 675 commits)

# Every data/ subdirectory that has ever existed on any ref:
$ git -C <automation> log --all --name-only | grep -E "^data/" \
    | sed 's#\(data/[^/]*\)/.*#\1#' | sort -u
data/fleet-stale-branch-sweep-s2
```

Grepping the `automation` working tree for the finding IDs and the review name
also returns nothing. The one literal "re-review" hit is an unrelated comment in
`.ci/authority-scan-allow.tsv`. The factory itself is present only as hygiene
scripts (`system-maintenance/claude-code/config/scripts/factory-hygiene.sh` and
its test), not as a committed plan or review corpus.

**Conclusion.** `data/factory-p3a-sr3-security-rereview/report.md` is a run-time
artifact of the factory process — it lives in the factory's own run directory
(the contract's `run` field names that run), not in the tracked tree of either
repository this session can reach. The scout was handed a pointer to a file that
was not delivered alongside the pointer.

## 4. What *is* verifiable, and why it matters for this task

Although the findings are opaque, the mechanism the task is racing against is
fully grounded in `gates`, and recording it precisely is the part of the scout
work that can be done now.

### 4a. "Owning rows" and which rows are security rows

The findings are to be folded into "rows." The gate rows this repository ships
are registered in `.ci/gate-registry.toml` (each with a `surface`, `gates`,
`test`, `suite`, and gates-proof inputs). The subset that count as **security
rows** is defined concretely in `run/permissions-diff.py:337-358`:

- **Security surfaces** (`SECURITY_SURFACES`): `Security`, `Code review`,
  `Gate pinning`.
- **Security rows** (`SECURITY_ROWS`, 19 ids): `workflow-policy`,
  `dependency-advisories`, `signature-check`, `exposure-scan`, `commit-msg`,
  `workflow-zizmor`, `workflow-actionlint`, `advisories-osv`,
  `advisories-osv-undecided`, `advisories-osv-stale`, `tool-missing`,
  `pr-text-slop`, `pr-text-em-dash`, `pr-text-attribution`, `pr-text-home-path`,
  `pr-text-security-marker`, `licence-refused`, `licence-review`,
  `licence-vendored`.

`protected(row)` (`run/permissions-diff.py:361-362`) returns true for any row on
a security surface or in that id set.

### 4b. Why "land before P3a-M6 puts any of these rows in shadow" is a hard timing constraint

`permissions-diff.py` refuses any change to security-design-7.2's protected
surface, which explicitly includes "an existing `.ci/gate-registry.toml` row
whose `surface` is in its `SECURITY_SURFACES` or whose id is in its
`SECURITY_ROWS`" (`AGENTS.md`, the permissions-diff entry). The consequence,
stated there: "Changing a Security row or a workflow token therefore needs a
**class-2 decision** once a caller promotes the gate out of shadow."

The shadow/promotion machinery:

- A caller runs each rule as a shadow row in its `.github/gates.toml`;
  `run/gate.py` "keeps a shadow row from blocking until it earns promotion"
  (`README.md:13`).
- `shared-gate-shadow.yml` records the runner's verdicts "so the 14-day
  comparison of plan row **P3a-M6** can be read later" (`README.md:1726`).

So the window the task is protecting is: **while the owning rows can still be
edited cheaply.** Once P3a-M6's comparison promotes a security row out of shadow,
the `permissions-diff` gate turns any further edit of that row into a class-2
governance decision. Low-severity (S3/S4) cosmetic/robustness fixes are exactly
the kind of change you want to make *before* that cost applies — hence "fold into
each owning row's next change" and "land before P3a-M6." Note that many security
rows already carry `shadow_since` dates in `README.md` (e.g. `permissions-diff`
`shadow_since = 2026-10-19`), so for any finding whose owning row is already in
shadow, the cheap window may be short or closed; the source report's per-finding
row assignment is what decides this, and that is the missing input.

### 4c. Why I did not produce a finding-to-row table

Section 6 of the source report is described by the contract as the section that
names each finding's owning row. With that section unavailable, any mapping of
`sr3-10 … SR-25` onto the 19 security rows (or onto hooks, `run/` scripts, or
`.ci` scripts) would be guesswork dressed as a result. The finding IDs carry no
embedded row information, the counts give no reliable signal, and inventing
assignments would send the owning-row edits to the wrong places. A scout report
that fabricates its central table is worse than one that reports the blocker, so
I have deliberately not written that table.

## 5. What is needed to unblock, and recommended next steps

1. **Supply the source report to the branch fac consumes, or to a repo in
   scope.** The one blocker is read access to
   `data/factory-p3a-sr3-security-rereview/report.md` (at minimum section 6 and
   the finding sections it names). Options, in order of preference:
   - Have the factory stage that report into the run's input set so the scout
     session can read it (it is a factory run artifact, not a committed file).
   - Commit it to a path a scoped session can read, or paste section 6 plus the
     named finding sections into the task contract's goal/source text.
2. **Once the report is available, the fold is a single mechanical pass** using
   the grounding in section 4: for each of the 17 findings, read its text and
   owning row from section 6, confirm the row against `.ci/gate-registry.toml`
   and the `SECURITY_ROWS`/`SECURITY_SURFACES` sets, and record the concrete edit
   to make on that row's next change. Prioritise findings whose owning row is a
   security row that has not yet been promoted out of shadow, since those are the
   ones P3a-M6 will soon make expensive to touch.
3. **Treat this as a defect in task packaging, not in the repository.** The repo
   is internally consistent (the report is correctly *absent* from the public
   snapshot by design); the gap is that the scout task referenced a run-time
   artifact without delivering it.

## 6. What I did (so this report stands alone)

- Confirmed the branch and HEAD of `gates` (`1739f45`), then searched the full
  tree for all 13 finding IDs, the report path, and the review name — zero hits
  (§2).
- Added the private source repository to session scope, cloned it, deepened
  `main` to its full 675-commit history, and searched both the working tree and
  all-ref history for the report path, the finding IDs, and any `data/` subtree —
  the report has never existed there; the only `data/` entry is
  `fleet-stale-branch-sweep-s2` (§3).
- Read and cite the verifiable mechanism: `run/permissions-diff.py:337-362`
  (security rows/surfaces and `protected`), `.ci/gate-registry.toml` (the row
  registry), and `README.md:13,1726` plus `AGENTS.md` (shadow promotion, P3a-M6's
  14-day comparison, and the class-2 cost of editing a promoted security row)
  (§4).
- Did **not** fabricate finding-to-row mappings, because the only source of those
  mappings is unreachable (§4c).

### Appendix — the findings as named by the contract (content not available)

| Group | IDs | Round |
|---|---|---|
| New this re-review | `sr3-10`, `sr3-11`, `sr3-12`, `sr3-13`, `sr3-14`, `sr3-15`, `sr3-16`, `sr3-17` | SR3 |
| Carried from SR2 | `sr2-12`, `sr2-14`, `sr2-15`, `sr2-16` | SR2 |
| Carried from base SR | `SR-16`, `SR-18`, `SR-22`, `SR-23`, `SR-25` | SR |

Seventeen findings in total (grouping inferred only from the ID prefixes; the
per-finding text, severity, and owning row remain unavailable pending the source
report).
