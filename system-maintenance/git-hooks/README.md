# Machine-wide git hooks

The six hooks git runs from `~/.git-hooks`, tracked here so the machine can be rebuilt from the repository, plus the two scan gates one of them chains.

`install.sh deploy` writes them out; `install.sh check` reports drift and never absorbs it.

## Why the secret scan moved up here

`.githooks/pre-push` at the repository root is the canonical gitleaks gate, and `.ci/gitleaks/sync.sh` copies it to every sibling repository.
It is armed by `git config core.hooksPath .githooks`, which is repository config and not a tracked file, so no commit can turn it on and **a fresh clone starts inert**.
That was measured on 2026-08-17: a `--depth 1` clone of `Abhijeet34/papertrace` carries `.githooks/pre-push` in its tree and reports an empty `core.hooksPath`, so the gate is present and does nothing.

Machine-wide is the only level at which a clone is covered the moment it exists, because global config applies to every repository that has not overridden it - including every clone that does not exist yet.

## What it does and does not execute

The dispatcher never runs a script that arrived with the repository being pushed.

Git deliberately does not populate `.git/hooks` from a clone, precisely so that cloning hostile code cannot execute it.
A global hook that chained into `"$toplevel/.githooks/pre-push"` would hand that property straight back, and third-party clones do live on this machine - a clone of `gitlab.gnome.org/GNOME/meld` is one.

So the scanner is always `~/.git-hooks/gitleaks-pre-push`, a byte copy of `.githooks/pre-push` deployed from this repository, and the rules are always `~/.git-hooks/gitleaks.toml`, a byte copy of `.gitleaks.toml`.
Nothing out of a pushed repository is executed or read as configuration, allowlisted or not.
`test-global-hooks.sh` proves it with a throwaway repository whose own `.githooks/pre-push` writes a marker file: the marker must not exist, both before and after that repository is allowlisted.

## The allowlist

The allowlist is therefore about blast radius, not about code execution.
A repository not on it is left completely alone - not scanned, not refused, and on the same code path it had before this hook grew a scan.

Entries are matched as shell globs against the normalised identity of the repository's `origin` remote: host, owner and name, with the scheme, any userinfo, any port and any trailing `.git` removed.
The built-in entry is `github.com/Abhijeet34/*`.

Normalising that URL is where a sloppy parser becomes a spoof, so the userinfo strip only fires on an `@` that lies before the first `/` - the authority, not the path.
A `${url#*@}` applied to the whole string normalises `https://evil.example/x@github.com/Abhijeet34/y` to `github.com/Abhijeet34/y`, which matches.
That URL, four more shapes like it, and the six that must still match - including a credential-bearing origin such as `https://user:pass@github.com/Abhijeet34/automation.git`, where the colon inside the userinfo must not be mistaken for the host:port separator - are pinned in `test-global-hooks.sh`; reintroducing the whole-string strip fails exactly that one case and nothing else.

Keyed on `origin` rather than on a physical path because a clone chooses its own path, and a path list would need a new line for every clone - which is the per-repo setup this hook exists to remove.
Keyed on the owner rather than a list of names because a new repository of his should be gated the day it is created, and the failure mode of a name missing from a list is silence.
`case` anchors the pattern at both ends, so `github.com.evil.example/Abhijeet34/x` does not match.

`origin` is also the right key for the pipeline: no-mistakes pushes every gated change through a local bare remote under `~/.no-mistakes/repos/`, so keying on the push target would leave the pipeline push - the one that most needs scanning - unscanned.

`~/.git-hooks/pre-push-allow` extends the list, one glob per line, `#` comments allowed.
It can only ever cause more repositories to be scanned, never fewer.

### Coverage on this machine

Measured 2026-08-17 across the 37 non-pool git working trees under `$HOME`:

| Outcome | Count | Which |
|---|---|---|
| Scanned | 33 | working trees of `automation`, `blurt`, `firstmate`, `gnhf`, `lavish-axi`, `no-mistakes`, `papertrace`, and one repository since dropped from the fleet |
| Left alone | 3 | no `origin` at all: three local-only working trees |
| Left alone | 1 | a clone of `gitlab.gnome.org/GNOME/meld` |

`~/.claude/tools/gnhf` is the one fleet clone that had no gate at all before this: it has no `.githooks` directory and resolves `~/.git-hooks`, so it was pushing unscanned.
The four fleet repositories with no clone on this machine today - `chrome-devtools-axi`, `gh-axi`, `quota-axi`, `tasks-axi` - are gated the moment they are cloned, which is the point.

### Known, and not fixed here: `core.hooksPath` swallows the other five hooks

`core.hooksPath` replaces the hooks directory outright, so a repository pointed at its own `.githooks` - which holds `pre-push` and nothing else - runs **no** `commit-msg`, `post-checkout`, `post-commit` or `post-merge` at all.
Measured 2026-08-17 with a global `commit-msg` that prints a marker: it fires before the repository sets `core.hooksPath=.githooks` and does not fire after.
`.ci/gitleaks/sync.sh` sets that config in every repository it installs into, so this is live in the 32 working trees that carry it.

What it actually costs here, measured the same day: nothing for git-lfs, because none of the eight fleet clones has a single LFS-tracked file; and the `warp.dev` co-author stripper in `commit-msg`.

This predates the machine-wide hook and is not fixed by it.
It is now *fixable*, because the scan no longer needs the repository-local `core.hooksPath` on a machine where these hooks are deployed - but unsetting it would silently ungate any machine that has not run `install.sh deploy`, so making that trade is a fleet decision, not a side effect of this change.

## The no-watermarking gate

`watermark-scan.py` refuses a push carrying **detective provenance**: a signal whose function is to let a machine classify the artefact as machine-made.
The rule is the captain's, in `~/OPINIONS.md` under "Provenance is for readers, never for machine detection", and its scope is every type of file and data, code included.
The test is purpose, not medium.

**Attributive provenance is never flagged** - a cited source, a measured claim, an authorship line, a `Decided by:` record, a commit trailer, an EXIF `Software` naming the editor a human drove.
`Software: Preview.app` is a tool record for a reader; `softwareAgent: gpt-image` is a machine-detection signal, and only the second is a finding.

What it detects, in two witnesses that back each other up:

| Where | What |
|---|---|
| text | invisible and steganographic codepoints: zero-width marks, bidi overlays and isolates, word joiner and invisible operators, soft hyphen, variation selectors, Mongolian and Hangul blanks, reserved-ignorables, noncharacters, and the Unicode Tags block U+E0000-E007F |
| binaries, raw bytes | C2PA/JUMBF manifest labels, `c2pa.watermarked*`, `c2pa.soft-binding`, the IPTC algorithmic `digitalSourceType` values, and a bare `SynthID` marker |
| binaries, exiftool | JUMBF groups, `digitalSourceType`, EXIF `Software`, XMP `CreatorTool`/`XMPToolkit`, `softwareAgent`, PDF `Producer`/`Creator` and the PDF XMP packet, and PNG `tEXt`/`iTXt`/`zTXt` generator chunks |
| binaries, exiftool warnings | a warning naming a structural parse failure - `Error reading xref table`, `Truncated`, `Corrupted` - because a container the inspector could not read was never established clean. `[minor]` warnings, which are ordinary tag-level noise, are not findings |
| containers | zip-family members (docx, xlsx, pptx, odt, epub, jar), PDF stream payloads raw and inflated, base64 `data:` URIs in text, and whole images carried inside another binary such as an ICO's payload - breadth-first, one level per pass, to a depth cap |
| names and messages | every path in the pushed ranges - filenames, directory names and submodule gitlink names - and every commit message, through the same codepoint engine |
| any text-like blob | a blob no decoder read in full. One non-UTF-8 byte used to route the whole file to the byte-marker scan, which knows no codepoints, and the file then reported `clean` |

Detection has three tiers, and which one a finding lands in decides what `clean` may claim about it:

| Tier | Meaning | What `clean` does |
|---|---|---|
| blocking, clearable | the mark is in the metadata or the bytes | strips it, then re-reads to prove it |
| blocking, UNCLEARABLE | the mark is in the PIXELS - a `c2pa.watermarked` assertion, or a generator known to mark undeclared (the Google SynthID family: Imagen, Veo, Gemini image output, Lyria, Nano Banana) | strips every removable signal and re-verifies it, then reports `UNVERIFIED`, names the generator, and lists every remedy |
| advisory | a producing tool, attributive by default | nothing. It is printed as `NOTED` and left for a human |

A GENERIC AI-generated marker from a generator that is on no known-pixel-watermarking list stays **clearable**, which is what this gate has always done.
Whether it should is a captain decision, registered as `provenance-gate-audit-v7-decision-generic-ai-marker-posture`; `GENERIC_AI_MARKER_UNCLEARABLE` in the scanner is the whole of the change when the answer arrives, and nothing else in the file encodes that posture.

### A tool tag is examined and NOTED, never refused on its own

A container recording the tool that produced it is attributive by default.
`Producer: appifact kit` names a publishing tool the way Word or Acrobat does, and refusing on the bare tag would break every PDF in the tree.
But skipping it is worse, and skipping it is what this gate used to do: PDF `Producer` was in no field list, so a PDF carrying one was handed to exiftool and then reported `clean` without the tag ever being read.
That is the same empty green the gate exists to prevent.

So a generator field whose value names no known generative pipeline is `binary.tool-tag`: printed as a `NOTED` line naming the field and its value, counted in the summary, exit status untouched, and left for `watermark-allow.conf` or a human to judge.
`clean` never strips one - attributive provenance is kept in full.
A value that DOES name a generative pipeline is `binary.generator-tool` and refuses, in a PDF exactly as in a PNG; the classification is one taxonomy across every container.
`clean` therefore means "examined and nothing found", where it used to mean "examined for everything except this".

The byte scan runs on binary blobs only, deliberately.
Source code legitimately names every one of those strings - `watermark-scan.py` itself does - and a detector that flags its own rules is the same defect as a filename-safety detector flagged for containing the RLO codepoint it exists to catch.

### Opposite default to the secret scan, and why

The gitleaks gate is opt-IN: it asks whether a push leaks *our* secrets, which is meaningless in a third-party clone.
This gate is opt-OUT: it asks whether *we* are shipping a machine-detectable origin mark, and every push from this machine is us shipping - including from a repository that does not exist yet, which no allowlist can name in advance.
Sharing one switch would have made every new project born ungated, which is the failure this file's own "machine-wide is the only level at which a clone is covered the moment it exists" argument exists to prevent.

`watermark-optout.conf` is that opt-out list, one origin-identity glob per line, and **an entry with no written reason is refused rather than honoured** - the value of an opt-out list is that nobody can widen it silently.
It is seeded only with upstreams this machine reads rather than authors: GNOME, zsh-users, zdharma-continuum, RustSec, sineto.
A repository of ours never belongs there.

### Two exemptions, and the difference between them

**Structural**, in the scanner, for contexts that are legitimate wherever they occur - because a path list cannot cover a file nobody has written yet:

- a variation selector after an emoji base or a keycap digit is emoji presentation, not a channel. The base test is `unicodedata.category` in `So`/`Sk`, plus a short explicit list of Emoji=Yes characters Unicode does not categorise as symbols. That list is load-bearing: U+2139 INFORMATION SOURCE, which no-mistakes writes as an emoji, has category `Ll`, the same as the letter `a`, whose variation selector must still be a finding.
- ZWJ inside an emoji sequence, and ZWJ/ZWNJ between Arabic-script or Indic letters, are spelling. The same codepoints in English prose are still findings.

**`watermark-allow.conf`**, for a decision, and every entry must name its reason after `#` or the scanner refuses to load it:

- a `text` entry lists the exact codepoints it excuses, so widening a file's character set is a new finding rather than a silent inheritance.
- a `blob` entry is keyed on the object's **content**, so a regenerated or edited asset is refused again under its new sha - which is what stops a replacement inheriting the exception it was meant to end.

Two entries today.
automation's `80-archives-files.zsh` holds bidi and zero-width codepoints in the character class its own filename-safety check matches against.
papertrace's evasion suite and canonicaliser hold the attack corpus a Unicode canonicaliser is defined by, bounded to the invisible set it normalises - the Tags block, the noncharacters and the reserved-ignorables are deliberately not excused there.

### The banner, and why replacing it was the only remedy

`firstmate/assets/banner.png` was the third entry, and it is the worked case for an UNCLEARABLE finding.
Blob `f81282ea33287428b787fe2a2d1d01bb7185970c`, inherited from upstream, carried a C2PA manifest signed by OpenAI OpCo, LLC through the SSL.com C2PA CA chain: `softwareAgent: gpt-image v2.0`, `claim_generator_info: OpenAI Media Service API`, `digitalSourceType: trainedAlgorithmicMedia`, and a **`c2pa.watermarked.unbound`** assertion - a watermark carried in the pixels rather than in the metadata.

Stripping the manifest would have made the file **read** clean while the mark remained, which converts a visible finding into a false pass, so the only honest remedy was to replace the image - a call about the project's front page, and the captain's.
He answered it on 2026-09-01: `assets/banner.png` is blob `9b87c88da0b480c94f23f8804f7e60f2dd7fb842` now, 533,574 bytes, and `watermark-scan.py inspect` reports it clean, so the entry was removed rather than left excusing a file that passes on its own.
`test-watermark-scan.py` pins that dead sha out of the shipped allowlist, because a content-keyed exemption outlives its content silently - the sha stops matching anything and nothing says so.

## inspect, clean, verify

The gate detects; the remedy is a separate verb the author runs, and the two share one engine.

```bash
watermark-scan.py inspect <path>...            # every finding, tagged clearable or UNCLEARABLE
watermark-scan.py clean [--out DIR] <path>...  # remove what is removable, then re-read to prove it
watermark-scan.py verify <path>...             # re-read and assert the artefact is clean
```

A push hook deliberately does **not** clean.
Mutating what you are pushing means rewriting commits behind you, which is worse than the finding; the refusal names `clean` instead, and you commit and push again.

`clean` never writes in place without leaving the original alongside as `<path>.orig`, and refuses rather than overwriting an `.orig` that already exists.
`--out DIR` writes the cleaned copy elsewhere and leaves the input untouched.
It honours `watermark-allow.conf`, matched on the repository-relative path and on content rather than on origin - so `clean` over this repository leaves the filename detector's character class byte-identical instead of deleting the codepoints it exists to match.

The verdict comes from **re-reading the artefact**, never from the exit status of the tool asked to change it.
This is structural rather than a convention: `clean_verdict` is the only place a clean verdict is produced, it takes the re-detection's findings as an argument, and no code path reaches `cleaned` without passing through it.
The strip tool's exit status is not one of its arguments and must not become one.

Four outcomes, and there is no fifth:

| Verdict | Meaning |
|---|---|
| `CLEANED and VERIFIED` | stripped, re-read with the full engine, nothing found |
| `NOT CLEAN` | a signal the gate CAN see is still there after the strip |
| `UNVERIFIED` | every removable signal is gone and re-verified, and a class nothing local can measure remains. Neither "clean" nor "marked" |
| `UNSCANNABLE` | nothing established the file either way - it did not decode, or the container did not parse |

That last row is the whole reason the encoding refusal exists one layer up, and it is why an unclearable class surfaced only by the RE-READ now blocks.
It did not before: the unclearable set was taken from the pre-strip scan while unclearable findings were filtered out of the residual, so that input reached neither test and printed `CLEANED and VERIFIED`.
No file on this machine is known to produce it, which is the point - a code path that can emit a clean verdict without a successful re-detection is a defect before anything reaches it.

A SECOND witness runs alongside the re-read, because a container that rewrites metadata by appending rather than by replacing defeats the first: a PDF incremental update leaves the old object in the file, where exiftool reports the tag gone and a byte scan does not.
Measured 2026-08-31 on a PDF whose `Producer` was `Midjourney v6`: `exiftool -all= -overwrite_original` warns "PDF edits are reversible", then reports `Producer` gone while the string is still in the bytes.
`clean` runs that witness for every generator value it removed and reports `NOT CLEAN` naming the surviving value.

### Attributive provenance survives the strip

A bare `exiftool -all=` is a correct strip and a wrong outcome: it wiped `Artist`, `Copyright` and `XMP-dc:Rights` off an asset alongside the Midjourney tag, which is the standing rule in `~/OPINIONS.md` inverted.
One exact-match table, `ATTRIBUTIVE_FIELDS`, is read at both ends - detection never flags one of those fields carrying a human value, and `clean` copies exactly those back after the strip with exiftool's own `-tagsFromFile @`.

The test is the VALUE, never the field's name.
`Artist: Midjourney` is detective wearing an attributive field's name: it is a finding, it is stripped, and it is not restored.
It was detected by nothing at all before this - `Artist` was in no field list.

### Which formats `clean` can write

| Format | Writer | Fidelity witness, measured |
|---|---|---|
| md and other text | codepoint removal in place | byte-exact minus the flagged codepoints |
| png, jpg, gif, webp, avif, heic, mp4, mov | `exiftool -all=` plus the attributive copy-back | pixel and frame signatures identical |
| pdf | exiftool, then a `qpdf` rebuild so the superseded object is gone | content stream intact, `Author` kept |
| tiff | ImageMagick `-strip`, then the attributive copy-back | pixel signature identical |
| mp3, wav, mkv, webm, flac, ogg, opus, aiff, avi | `ffmpeg -map_metadata -1 -c copy`, which re-encodes nothing | PCM and raw-video hashes identical, `Artist` and `Copyright` kept |
| svg | XML-level removal of `<metadata>`, XMP packets and RDF blocks | the drawing intact |
| zip-family (docx, odt, …) | repack with every media member cleaned by the writers above | every member present, document body unchanged |

exiftool cannot write mp3, wav, mkv or svg at all, and cannot delete `IFD0` from a TIFF - it answers `[minor] Can't delete IFD0 from TIFF` and leaves the value in the bytes whether it writes in place or to a new file.
`mp4`, `m4a` and `mov` deliberately stay on exiftool: it writes those today with frame signatures identical, and swapping a working writer for another buys nothing.
A writer whose tool is absent **refuses by name** and never falls through to one that cannot reach the value, because a `clean` that removed nothing must not look like one that removed everything.

An allowlisted finding is still a finding, so neither verb calls such a path clean.
`inspect` closes with `N allowed, 0 blocking` and `verify` with `VERIFIED - N allowed, 0 blocking`, above the `ALLOWED` lines naming each excused finding and its reason; the word `clean` is reserved for a path where nothing was found at all.
A noted tool tag reads the same way, as `N noted, 0 blocking`, and the two counts combine.
Exit status is untouched by this - an excused finding never blocked a push and still does not.

### Clearable and unclearable, which is the whole of it

The line is drawn by whether removal is **verifiable on a re-read**.
Invisible codepoints and container metadata are either in the bytes afterwards or they are not; measured 2026-08-31 on the firstmate banner, `exiftool -all= -overwrite_original` takes the whole signed manifest out - zero JUMBF tags and zero `c2pa` byte markers afterwards, 3,011,594 bytes down to 2,982,495.

An unbound watermark is not like that.
It lives in the pixels, so removing the assertion that declares it removes the declaration and not the mark.
`clean` therefore strips what it can, re-reads to prove it, and reports **`UNVERIFIED`** - not `clean`, and not `marked` either, because nothing here read the pixels in either direction.

**The remedy is every remedy, not one.**
This used to end at "the asset must be REPLACED", and that was wrong in a way worth naming so it is not rebuilt: it assumes a replacement EXISTS.
When the artefact is precisely the thing wanted - a generated image or video that IS the work - "replace it" is a refusal wearing a remedy's clothes, and it takes a decision belonging to the author and makes it inside a tool.
Corrected on the captain's instruction, 2026-09-02.

So the output states what was found and names the generator, what was removed and re-verified, what could not be established, and then every path that exists: re-synthesise the pixels through a generator that does not mark using this asset as input; recreate as vector where it is a logo, icon, banner or diagram; re-encode or transform, flagged honestly as best-effort and unverifiable; replace it where a replacement exists; or use it as it is, which is the author's call and stays visible rather than being refused on their behalf.
No re-synthesis pipeline is implemented here and none is planned in this change - no local image model exists on this machine - so remedy 1 is named as a step you run elsewhere.

The rule that does not bend is the other half: a best-effort transform earns `UNVERIFIED`, never `clean`.
Shipping an unverified asset with your eyes open is legitimate; being told it is clean when nothing established that is not.

There are two ways into that class, and the second is the one a declaration-only test misses.
A `c2pa.watermarked.*` assertion DECLARES the mark, and that is the banner case below.
A generator on the known-pixel-watermarking list declares nothing a strip can remove - Google's SynthID family embeds in the pixels and its metadata footprint is IPTC `digitalSourceType` plus credit text - so keying only on the declaration let `clean` print `CLEANED and VERIFIED` over a file a vendor detector would still flag.
Its notice is worded for that evidence rather than borrowing the manifest one, because there is no manifest here, and it names the generator: the remedy for this class is to stop using the tool that produced the asset, which is what `~/OPINIONS.md` already says about a generator that marks what it produces.

Three ceilings, stated rather than left as silent gaps, because "strip all and any" has a boundary and claiming past it is worse than not scanning at all.

A statistical or token-sampling text watermark - SynthID text among them - leaves no codepoint and no metadata, so nothing here detects it and nothing here claims to remove it.
There is no local detector for one and this gate will not pretend otherwise; paraphrase-to-humanize is deliberately not implemented, because it cannot be verified and it degrades the writing.
The remedy is the tool-choice half of `~/OPINIONS.md`: do not use a service known to sample-watermark its output.

A pixel-domain mark cannot be removed here at all, only refused.
Where the generator is identifiable the refusal names it, which is the only actionable remedy that class has.
Where it is not - an undeclared mark from a generator on no list - nothing here sees it, and that is the honest floor of the detection half.

And `verify` answers "is anything detectable left in this file", never "was this file ever marked" - a copy of the banner whose manifest has been stripped verifies clean and is not.
`clean`'s own report, or `inspect` on the original, is what answers that.

### Cost, measured 2026-08-31 and re-measured 2026-09-02

| Shape | Time |
|---|---|
| ordinary incremental push, 5 commits (`automation`, 20 blobs) | 0.27s; 0.265s after every change in this pass |
| ordinary incremental push, 5 commits (`meld`, forced in scope) | 0.15-0.25s; 0.240s after |
| ordinary incremental push, 5 commits (`firstmate`) | 0.341s before, 0.426s after |
| opted-out repository, fast path | 0.07-0.10s |
| worst case: first push of a whole history to a remote holding none of it - `meld`, 5,842 commits, 21MB clone | 26.8s and 29.8s over two runs; 27.5s and 27.4s after container recursion; 33.8s after the base64 closures |
| the same shape on our own repositories, full history | `automation` 2,746 blobs 4.6s, `blurt` 1,652 blobs 4.6s, `firstmate` 4,379 blobs 14.1s, `papertrace` 364 blobs 0.9s |

The worst case is the same class as the gitleaks gate's 27.6s on `automation`, and it is not what an ordinary push pays.
Container recursion, the largest addition, cost 2.4% of it: the walk only descends a blob that actually is a container, and the diff pass that gives every path per blob is 0.18s over meld's whole history.
The base64 closures cost the rest, and that number is a deliberate trade rather than an oversight: scanning every text blob for base64 fragments is 7.0s over meld's 289MB of text, and the pre-filter that would reclaim it - only look at a blob with a 128-character line - is measured at 0.54s but skips a base64 payload wrapped at PEM's 64 columns, which is a real shape.
False negatives outrank cost in the captain's own ranking, and the number that decides whether anyone reaches for `--no-verify` is the ORDINARY push, which pays 60 to 90 milliseconds more.
The false-positive delta over the same corpus was measured against the pre-change scanner rather than argued: `automation` 8 finding lines to 8, `firstmate` 16 to 16, `meld` 123 to 165 - and all 42 of meld's new ones are the encoding refusal firing on latin-1 `po/ChangeLog` entries, in a clone already on the opt-out list.
That measurement also surfaced 48 findings in meld's `po/*.po` gettext catalogues - Hebrew and Persian bidi marks, a Slovenian RLE, German BOMs - which are legitimate right-to-left typesetting and one concrete reason a third-party upstream belongs in the opt-out.
None of our four repositories carries a translation catalogue today; the first that does will need either a structural RTL exemption or an allowlist entry, decided on that repository's evidence rather than pre-built here.

### The CI backstop

`.github/workflows/shared-watermark-scan.yml` scans the tree in CI, beside `shared-secret-scan.yml` and for the same reason: the hook is the gate, and this is what sees what the hook never did.
Four things bypass the hook, all measured or structural - `git push --no-verify`, a machine with no deployed hook, any commit made on GitHub itself (web edit, API, Actions), and the filed fail-open in the repo-local chain when the global hook is absent.

Its scope is the **tree**, not history, which is the one place it differs from the secret scan beside it.
A credential committed and later deleted is still published, so that scan must read every commit; a watermark deleted at HEAD is genuinely gone from the artefact anyone downloads.

It refuses rather than passing whenever it cannot establish a verdict: a caller not carrying the scanner, a missing allowlist, a checkout git lists no file in, or a scanner that exits 0 having printed nothing.
`.ci/test-watermark-scan-workflow.sh` runs the extracted step body under `bash -e`, the way the runner runs it, and that is what caught a `grep -c` matching nothing aborting the whole step on the assignment - so the case the count exists to catch refused with no message at all.

## This is an accident-catcher, not a control

`git push --no-verify` bypasses every hook, and so does deleting the file.
The gate stops an agent or a human committing a credential by mistake.
It stops nobody who means to.
The CI backstop above narrows that to what never reaches CI either, but it does not close it: a marked asset can still be published from a fork, a release artefact, or anywhere outside these repositories.

Read it as the thing that catches the mistake, and never as the thing that makes a leak impossible.

## Origin-side enforcement is NOT delivered, and cannot be bought today

Every gate described here is local.
Nothing on the GitHub side rejects a push carrying a secret, and re-measured on 2026-08-17 that is not a budget decision - it is not purchasable for these repositories as they are.

- `repos/Abhijeet34/automation` reports `security_and_analysis: null` and `visibility: private`; `GET /repos/Abhijeet34/automation/secret-scanning/alerts` answers `404 Secret scanning is disabled on this repository.`
- Secret scanning and push protection are free on public repositories.
  On private ones GitHub's own documentation scopes them to repositories **owned by an organization** with GitHub Secret Protection on Team or Enterprise Cloud: "Secret scanning alerts for users can be enabled on any free public repository that you own."
  All 12 private repositories here are owned by the `Abhijeet34` **user** account, so there is no add-on to buy that would cover them while they stay private and personally owned.
- Pre-receive hooks are GitHub Enterprise Server only, confirmed rather than assumed: `GET /admin/pre-receive-hooks` against `api.github.com` answers `404`.

The two routes that would deliver genuine origin-side enforcement are therefore:

1. **Make the repositories public.** Secret scanning and push protection both become free and both run server-side.
2. **Move them into an organization on GitHub Team or Enterprise Cloud and buy GitHub Secret Protection.** Note that a *free* organization is not enough for this any more than it was for branch protection - see the mirror-owner entry in the root `AGENTS.md`.

Both are captain decisions and both connect to the repo-visibility question.
Until one of them happens, CI is the only server-side backstop: `.github/workflows/shared-secret-scan.yml` re-scans full history after the push, which catches what was already published rather than preventing it.
CI itself is running again - eight `CI` runs on `Abhijeet34/automation` in the eight hours before this was written, all `success` - so that backstop is live, but it is a detector and not a gate.

## Latency

Measured 2026-08-17, five pushes each after a warm-up, throwaway repositories with a local bare remote:

| Shape | Per push | Delta |
|---|---|---|
| Repository not on the allowlist | 0.31-0.33s | unchanged |
| Allowlisted, scanned by this hook | 0.44-0.46s | **+0.13s** |
| Allowlisted, `core.hooksPath=.githooks` already ran the scan | 0.44-0.46s | +0.13s, scanned once |
| Same, with the repository hook drifted so the skip does not apply | 0.57-0.80s | +0.26s, scanned twice |

The last row is what the byte comparison in the dispatcher avoids.
When a repository routes git's hooks elsewhere, git did not invoke this file - the repository's own hook chained here - and if that hook is byte-identical to the deployed scanner, the scan has already run on this same ref list.
The skip is inferred from `git config --local core.hooksPath` and a `cmp`, so a push cannot claim it: a repository can only skip the scan by having actually run it, and a drifted copy is scanned again by the canonical one.

**The worst case is a first push of a branch to a destination that holds none of its history.**
The whole branch is then genuinely being published there, and all of it is scanned.
For `automation` that is 818 commits and 24.3MB in 27.6s, measured 2026-08-17.

What a new branch excludes comes from the destination's own advertisement - one `git ls-remote` of the URL git passes as `$2`, then only the advertised shas that resolve locally.
`$2` rather than the remote name in `$1`, because a `remote.<name>.pushurl` splits the two and `$1` names the fetch URL: asking it excluded everything a mirror held and pushed a secret-carrying commit to an empty destination unscanned.
Until 2026-09-02 it came from remote-tracking refs instead, which a push by URL does not have at all, so that 27.6s shape was paid on **every** first delivery push of every PR branch rather than only on a genuinely fresh destination, and a fork's first pipeline push was refused over inherited upstream commits its destination was already serving.
A sha we do not have is not excluded, and an `ls-remote` that fails falls back to the old, wider range: both errors scan more, never less.
The ask costs one round trip on a new-branch push and nothing at all on any other - 1.32-1.45s to github.com from here, three runs on 2026-09-02, against the 27.6s full-history scan it replaces on every first delivery push.
Pushes to the pipeline's staging bare under `~/.no-mistakes/repos/`, which starts empty and so advertises nothing, additionally exclude everything any remote already holds - what has to be caught there is work no remote has ever seen, and the gate's own delivery push re-scans the rest against the real destination.

## What a refusal looks like mid-pipeline

no-mistakes pushes with plain `git push <remote> HEAD:<ref>` and no `--no-verify` (`internal/git/git.go`, `internal/pipeline/steps/common_exec.go`), so a refusal fails the `push` step and git's stderr - the hook's own message - comes back with it:

```text
gitleaks-pre-push: secrets found in the commits being pushed. Push refused.
gitleaks-pre-push: rotate the credential first - rewriting history does not un-leak it.
```

That is not a transient failure and re-running the step will not clear it.
Rotate the credential first, then remove it from the range.
Do not reach for `--no-verify`: it would push the credential to the gate remote, which is a real publish.

## Commands

```bash
system-maintenance/git-hooks/install.sh deploy   # repo -> ~/.git-hooks, and set global core.hooksPath
system-maintenance/git-hooks/install.sh check    # report drift, exit 1 if any
system-maintenance/git-hooks/test-global-hooks.sh              # ~30s, offline
GLOBAL_HOOKS_E2E=1 system-maintenance/git-hooks/test-global-hooks.sh   # adds one real networked clone
```

`deploy` is one way on purpose.
`~/.git-hooks` holds scripts git executes on every commit, checkout and push, so it is a code-execution surface with the same standing as a directory on `$PATH`; a sync-back would let anything that can write it launder itself into the repository as "the current config".
`check` prints the exact `cp` for either direction instead, so adopting a live change stays a deliberate act that lands in a diff.

```bash
system-maintenance/git-hooks/test-watermark-scan.py            # ~11s, offline; needs exiftool, ffmpeg, qpdf, magick
.ci/test-watermark-scan-workflow.sh                            # ~6s, offline; the CI backstop's step body under bash -e
```

Those four binaries are `need`ed rather than skipped by `.ci/check.sh secrets`, and CI installs all four.
A suite that skipped a writer would report a pass over a writer nothing measured, which is this repository's dominant defect in its own words.

`test-global-hooks.sh` runs entirely against a throwaway `HOME` and proves that scoping - it digests the real `~/.git-hooks` before and after the deploy and aborts if it changed - before it runs anything destructive.
The opt-in networked case clones `Abhijeet34/papertrace` and is deliberately not auto-skipped on failure: an auto-skip would report a pass on a machine where the network or the credentials are gone, which is the state where the answer matters.

## Related

- `.githooks/pre-push` - the canonical secret scanner, deployed here as `gitleaks-pre-push`. It fails closed and does not trust gitleaks' exit code alone; its header carries the measurement.
- `watermark-scan.py`, `watermark-allow.conf`, `watermark-optout.conf` - the no-watermarking gate and its two rule files, all three deployed by `install.sh`.
- `test-watermark-scan.py` - the gate's own suite: a fixture per rule, each re-graded once neutralised, the three legitimate contexts, both rule files' contracts, the `clean`/`verify` verbs, and the refusal when exiftool is gone. `test-global-hooks.sh` pins the dispatch around it.
- `.ci/gitleaks/sync.sh` - copies that hook and `.gitleaks.toml` out to the sibling repositories, and sets `core.hooksPath` there.
- `.ci/gitleaks/test-pre-push.sh` - pins the scanner's own refusal behaviour, where `test-global-hooks.sh` pins the machine-wide dispatch around it.
- `.github/workflows/shared-secret-scan.yml` - the CI backstop, and the only server-side check available on this plan.
