# Report: recurring redacted secret scan over agent transcripts

**Task:** `transcript-secret-scan-g7`
**Run:** `r20261116T232924-35279`
**Scope:** scout — what exists, what a transcript secret scan needs, and what blocks it.

---

## 1. What was investigated

The goal is a recurring scan of the Claude Code harness's per-project
transcript store (`~/.claude/projects/<project-hash>/<session-id>.jsonl`)
for credential-shaped strings, homed in gates beside the existing
watermark-scan gate. The backlog states that s4 found 4 credential-shaped
strings only in transcripts.

## 2. The transcript store

Claude Code stores session transcripts as JSONL files under
`~/.claude/projects/`. Each project directory is named by a path-derived
hash (e.g. `-home-user-gates`), and each session within it is a UUID-named
`.jsonl` file. Each line is a JSON object with a `type` field (`user`,
`assistant`, `queue-operation`, `attachment`, etc.) carrying the full
conversation content — user prompts, tool calls, tool results, and model
responses.

**Observed structure on this machine:**
```
~/.claude/projects/-home-user-gates/ab52f4aa-....jsonl   (187 lines, 828 KB for one session)
```

A single session transcript can be hundreds of KB; a developer working
across multiple projects accumulates many such files. The store is not
git-tracked and is local to each developer's machine.

## 3. Why transcripts carry credential-shaped strings

A transcript records everything the LLM saw and produced. Credential-shaped
strings enter through several paths:

1. **Tool output:** `cat`, `grep`, or `Read` over a file containing a key
   (e.g. `.env`, a config file, a test fixture).
2. **User input:** a developer pastes a key into the prompt.
3. **Model output:** the LLM quotes a key it read from a file.
4. **Fixture echo:** the conversation reads test fixtures that deliberately
   contain synthetic secret-shaped values (this is what I observed in this
   session — reading `.ci/gitleaks/fixtures/known-secrets.tsv` put all its
   synthetic keys into the transcript).

The harness does not redact transcripts at write time (at least not for
all patterns), so a real credential that enters any of these paths persists
in the JSONL indefinitely.

## 4. Proof of concept: scanning this session's own transcript

I wrote a quick Python scanner (`scratchpad/scan_transcript.py`) applying
the same regex patterns from `.gitleaks.toml` against the current session's
transcript. Result:

```
Total findings: 36
  line 103: openai-compatible-api-key    (redactor test vectors from allowlist-cases.tsv)
  line 175: openai-compatible-api-key    (known-secrets.tsv fixture values)
  line 175: xai-api-key                  (known-secrets.tsv fixture values)
  line 175: groq-api-key                 (known-secrets.tsv fixture values)
  line 175: openrouter-api-key           (known-secrets.tsv fixture values)
  line 175: huggingface-access-token-alnum  (known-secrets.tsv fixture values)
  line 175: anthropic-api-key            (known-secrets.tsv fixture values)
  line 175: openai-api-key               (known-secrets.tsv fixture values)
  line 175: github-pat                   (known-secrets.tsv fixture values)
  line 175: github-fine-grained-pat      (known-secrets.tsv fixture values)
  line 175: aws-access-token             (known-secrets.tsv fixture values)
  line 175: gcp-api-key                  (known-secrets.tsv fixture values)
  ... (each pattern appears twice because the TSV was read and then its
       content appeared in the conversation output)
```

All 36 findings in this session are **synthetic test fixture values** from
`.ci/gitleaks/fixtures/known-secrets.tsv`, not real credentials. This
demonstrates both the need (the scanner fires) and the main design
challenge (distinguishing fixtures from real leaks).

## 5. Existing infrastructure the scan would use

### 5.1 Secret-scan rules (`.gitleaks.toml`)

The fleet's canonical gitleaks configuration carries:
- 7 custom rules (OpenAI-compatible, xAI, Groq, OpenRouter, Hugging Face,
  minisign, plus the defaults' own ~30 rules)
- Path-scoped allowlists that excuse test fixtures, build output, and
  specific known-safe assignments

These rules are designed for git-tracked files. The transcript store is
**not** git-tracked, so `gitleaks dir` and `gitleaks git` are both
usable (`dir` on the transcript directory), but the path-based allowlists
would need extending or the scan would need its own allowlist.

### 5.2 Watermark-scan (`system-maintenance/git-hooks/watermark-scan.py`)

The sibling gate the task names. Key design properties:
- Runs as a pre-push gate (deployed to `~/.git-hooks/`)
- Also runs as a CI backstop (`shared-watermark-scan.yml`)
- Python, ~111 KB, pattern-matching over file contents
- Allowlist in `watermark-allow.conf` / `watermark-optout.conf`
- Test suite in `test-watermark-scan.py` (~83 KB, exercising every rule)

### 5.3 Exposure-scan (`system-maintenance/git-hooks/exposure-scan.py`)

Closest analogy for a local-machine scan:
- Derives needles at runtime (hostname, MAC, paths) — never writes them
- Scans pushed content, not the transcript store
- Redacts findings in its own output

### 5.4 Artifact-scan (`system-maintenance/git-hooks/artifact-scan.py`)

Scans release binaries for machine identifiers. Reuses
exposure-scan's needle derivation. Shows the pattern for a scan that
operates outside the git tree.

### 5.5 Gate registry (`.ci/gate-registry.toml`)

Every gate needs a row with executable red/clean inputs. A transcript
scan would need:
- A `command` (the scanner script)
- A `red` input (a transcript JSONL containing a credential-shaped string)
- A `clean` input (a transcript JSONL without one)
- A `finding` string the red case must print

### 5.6 Install infrastructure (`install.sh`)

A new gate would need a `PAYLOAD` entry in `install.sh` for the scanner
script, and would run as part of the pre-push dispatcher or as a
standalone recurring job.

## 6. Design considerations for a transcript secret scan

### 6.1 Where it runs

The transcript store is local to each developer machine, not in any git
repository. This rules out CI as the execution environment. The scan
must run:

- **On each developer's machine**, either:
  - As a new gate in the pre-push dispatcher (runs on every push, scans
    transcripts accumulated since last scan)
  - As a standalone cron job / launchd plist
  - As a Claude Code hook (e.g. a session-end hook)

The pre-push dispatcher is the natural home for consistency with the
existing gates, but a transcript scan has a different cadence — it
should run periodically regardless of push activity.

**Recommendation:** a standalone script in `system-maintenance/git-hooks/`
beside `watermark-scan.py`, deployed by `install.sh`, and run by both:
1. The pre-push dispatcher (so a push always checks)
2. A periodic trigger (so transcripts are scanned even when no push
   happens)

### 6.2 What it scans

```
~/.claude/projects/*/   — every project directory
  *.jsonl               — every session transcript
```

Each JSONL line is scanned independently. The scanner reads each line's
JSON, extracts text content from message fields, and applies the same
regex patterns as `.gitleaks.toml`.

### 6.3 Allowlisting and false positives

The main challenge. Transcripts contain:
- **Synthetic test fixtures:** any session that reads
  `.ci/gitleaks/fixtures/known-secrets.tsv` puts its values into the
  transcript. These are not real credentials.
- **Code examples:** model-generated code may contain placeholder keys
  like `sk-your-key-here`.
- **Documentation quotes:** reading a README that discusses API key
  formats.

**Options:**
1. **Value-based allowlist:** maintain a list of known-synthetic values
   (the fixtures from `known-secrets.tsv`). Tight but brittle — a new
   fixture needs a new allowlist entry.
2. **Source-based allowlist:** if the matched string also appears in a
   git-tracked file that is itself allowlisted in `.gitleaks.toml`,
   suppress the finding. This is what I'd recommend — it means "if gitleaks
   already excuses this value in the tree, the transcript quoting it is
   not a new leak."
3. **Entropy-only on unknowns:** report only findings whose matched value
   is not found verbatim in any git-tracked, allowlisted file. This
   separates "the transcript quoted a known fixture" from "the transcript
   contains a credential the tree does not".

### 6.4 Posture: refuse or report

The existing gates refuse (exit non-zero, block the push). A transcript
scan that blocks pushes would be disruptive — the developer may not even
know their transcript contains a credential from a session days ago.

**Recommendation:** report mode by default (exit 0, print warnings),
refuse mode opt-in (exit 1 when a credential is found that is NOT in
any allowlisted tree file). The pre-push integration would use report
mode; a periodic audit could use refuse mode to flag for manual review.

### 6.5 Redaction in the transcript

The scan itself must never print the matched credential. The existing
pattern (`--redact` in gitleaks, `exposure.redact()` in the Python
scanners) applies. Output: `rule-id  line-number  <session-file>`.

### 6.6 Remediation

When a real credential is found:
1. Rotate the credential immediately (the transcript is a local file, but
   the credential was likely used in an API call or committed somewhere).
2. Delete or truncate the offending transcript line(s).
3. Optionally: the scanner could offer a `clean` verb (like
   `watermark-scan.py clean`) that redacts the matched value in-place in
   the JSONL, replacing it with `[REDACTED]`.

## 7. What blocks implementation

### 7.1 No gitleaks on this machine

Gitleaks is not installed in this cloud environment, so I could not test
`gitleaks dir ~/.claude/projects/` directly. The Python proof-of-concept
above demonstrates that the same regex patterns fire, but a production
scanner should use gitleaks itself (or its rules as a library) for
consistency with the fleet's canonical configuration.

### 7.2 Transcript store contract is undocumented

The JSONL schema is observed, not specified. The scanner needs to know:
- Which JSON fields contain user/model text (observed: `.message.content`
  for `type: "user"` and `type: "assistant"`)
- Whether the harness ever redacts before writing (observed: it does not,
  at least not for the patterns tested)
- Whether old transcripts are ever pruned (observed:
  `~/.claude/.last-cleanup` exists, suggesting some cleanup runs)

### 7.3 Fleet deployment mechanism

The existing gates deploy through `install.sh`, which writes to
`~/.git-hooks/`. A transcript scan deployed beside the other gates
would follow the same path. A periodic trigger (cron/launchd) would
need an additional deployment step that `install.sh` does not currently
handle.

### 7.4 The 4 findings s4 reported

This scout session cannot reproduce the specific 4 credential-shaped
strings s4 found, because those were in transcripts on a different
machine. This session's transcript scan found 36 matches, all from
synthetic test fixtures read during the session. The 4 findings from
s4 may have been:
- Real credentials pasted into prompts
- Credentials read from `.env` or config files via tool calls
- Credentials in API responses captured in tool output

Without access to those transcripts, I cannot classify them further.

## 8. Recommended implementation plan

### Phase 1: the scanner script

Create `system-maintenance/git-hooks/transcript-scan.py` beside
`watermark-scan.py`, with:
- A `scan` verb that walks `~/.claude/projects/*//*.jsonl`
- Applies `.gitleaks.toml` rules (or the Python equivalents from
  `generate-fixtures.py`) to text content in each JSONL line
- Suppresses findings for values that appear in git-tracked files
  excused by `.gitleaks.toml` allowlists (source-based allowlist)
- Prints `rule-id  line  <redacted-path>` per finding, never the value
- Exits 0 on no findings, 1 on findings, 2 on errors

### Phase 2: test suite

Create `system-maintenance/git-hooks/test-transcript-scan.py` with:
- Fixture JSONL files containing synthetic credentials (from
  `known-secrets.tsv`) — must be found
- Fixture JSONL files with allowlisted values only — must be clean
- A JSONL with a credential in a `type: "user"` message
- A JSONL with a credential in tool output (`type: "result"`)
- Each rule exercised (same coverage standard as `test-watermark-scan.py`)

### Phase 3: gate registration

Add a row to `.ci/gate-registry.toml` with red/clean inputs built from
the test fixtures.

### Phase 4: deployment

Add a `PAYLOAD` entry to `install.sh`. Optionally add a `start` line
to the pre-push dispatcher for a push-time check, though the primary
trigger should be periodic.

### Phase 5: periodic trigger

A `launchd` plist (macOS) or cron entry that runs the scanner daily
or hourly. `install.sh deploy` could write the plist; `install.sh check`
could report its absence.

## 9. What I could not do

- **Install and run gitleaks** against the transcript store (not available
  in this environment).
- **Access the s4 transcripts** where the 4 credential-shaped strings were
  found (different machine).
- **Test the source-based allowlist strategy** end-to-end (needs gitleaks
  or a full reimplementation of its allowlist evaluation).
- **Measure scan time** on a realistic transcript corpus (this machine has
  one session's transcript; a developer machine may have hundreds).
