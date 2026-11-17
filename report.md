# gates-tagging-for-bumps-t4: can gates tag itself?

## Not done, and why (read first)

I did **not** run the throwaway-tag experiment with gates' own built-in `GITHUB_TOKEN`. The question it was meant to answer no longer decides anything. The backlog item assumes that "gates publishes no releases and no tags". That stopped being true on 2026-10-16, a month after the 2026-09-17 measurement. gates now has eight releases, `v1` to `v8`. They were cut by the builder App `hw-bodhi[bot]` and by the owner, so the tagging line is already working without `GITHUB_TOKEN`.

Running the experiment anyway would have meant pushing a workflow that holds `contents: write`. Every gates workflow is held to `.ci/test-workflow-policy.sh`, and token changes fall under `run/permissions-diff.py`. The delivery contract also discards every file except this report, so the workflow would only have existed to create and delete a tag in a public repository whose tags Dependabot reads. I judged that the wrong trade for a question that is now moot. If someone still wants that specific fact (whether `GITHUB_TOKEN` may create a tag), the recipe is under "Recommendation" below.

## What I found

### 1. gates has tags and releases: the premise is stale

```
$ git ls-remote --tags origin        # 12 refs
$ git for-each-ref refs/tags --sort=version:refname ...
v1 commit (lightweight)  2026-10-16
v2 tag  tagger=hw-bodhi[bot] 2026-10-21
v3 tag  tagger=hw-bodhi[bot] 2026-10-22
v4 tag  tagger=hw-bodhi[bot] 2026-10-25
v5 tag  tagger=hw-bodhi[bot] 2026-10-26
v6 commit (lightweight)  2026-11-02
v7 commit (lightweight)  2026-11-02
v8 commit (lightweight)  2026-11-03
```

After `git fetch --unshallow`, every tag's commit is an ancestor of `origin/main`:

```
v1 9634dea8b931 on-main=yes  feat(bin): add gates bump with staged rollout and signed rollback (#20)
v2 cef2b36e0c0e on-main=yes  feat(corpus): add open-alternative lens row ... (#45)
v3 9dda2e358e6f on-main=yes  feat(workflows): add the shared daily fakes smoke (#47)
v4 2d686fa31bf6 on-main=yes  ci: shadow the gate runner beside gates' own checks (#53)
v5 99048291edfe on-main=yes  chore: ignore Python bytecode ... (#66)
v6 b58ba0a4a155 on-main=yes  docs: adopt the repository standard's community files ... (#89)
v7 bd934625b687 on-main=yes  fix(gitleaks): excuse decision names ... (#93)
v8 7c07f96851a2 on-main=yes  ci: give release commits a green checks run lock.sh can read (#96)
```

The GitHub releases API (`list_releases`) shows who published each release:

| Release | Author | Published (UTC) |
| --- | --- | --- |
| v1 to v5 | `hw-bodhi[bot]` (id 343227966) | 2026-10-16 to 2026-10-25 |
| v6 to v8 | `Abhijeet34` | 2026-11-02 to 2026-11-03 |

No workflow run has the `release` event (`list_workflow_runs event=release` returned 0). The tags were made from outside Actions, not by a gates workflow's `GITHUB_TOKEN`.

### 2. The release policy the backlog item calls "a decision" is already written down

`README.md:82-87` ("Pinning") was introduced by `4d42962` on 2026-10-16 ("ci: bootstrap shared workflows from a locked gates commit (#19)"). It says:

- `README.md:82`: the pin is a commit with a `# vN` comment, because Dependabot's `github-actions` updater "rewrites the comment from the `vN` tags this repository cuts". This is exactly the mechanism the experiment wanted.
- `README.md:83`: "A release is a tag on a commit of `main` whose `checks` run is green."
- `README.md:84`: the procedure is `gh workflow run ci.yml --ref main`, then `gh release create vN --target <that commit> --generate-notes`. `v8` itself (#96) is the change that gives release commits a `checks` run `run/lock.sh` can read.
- `README.md:87`: "tags are never moved", because nothing ancestry-checks the bootstrap pin.

That answers the open policy questions in the backlog item:

- **Scheme:** `vN`.
- **What gets tagged:** a `main` commit with green `checks`.
- **Cadence:** manual, roughly one release every few days so far.
- **What a tag means:** a vetted bootstrap commit for Dependabot to offer.

### 3. What is not shown here

- **Whether Dependabot now actually opens gates bumps.** That would be visible only in caller repositories, which are outside this session's scope (`Abhijeet34/gates` only). The 2026-09-17 decline ("Latest version is " empty) was measured with zero tags, so it says nothing about today.
- **Whether `GITHUB_TOKEN` may create a tag.** Untested, for the reason given at the top.

## Recommendation

1. **Close `gates-tagging-for-bumps-t4` as superseded.** Its hypothesis, "give gates tags and the updater has an ordering", was adopted on 2026-10-16 and has eight releases behind it. The tagging line is not dead, and it never needed `GITHUB_TOKEN`.
2. **Replace it with a verification scout in a caller repository.** Find a caller whose bootstrap `uses: Abhijeet34/gates/...@<sha> # vN` is behind `v8`, and check that caller's latest Dependabot `github-actions` update job. The pass condition is "Latest version is v8" (or a bump PR), not an empty version. That is the measurement that would actually settle the unattended-propagation question. Note the scope limit in `README.md:136-137`: Dependabot moves only the workflow pin, never `.github/gates.lock`, which stays with `bin/gates.py bump`. So even a working Dependabot buys the bootstrap pin only, as the backlog item already warned ("the unattended half only").
3. **Run the `GITHUB_TOKEN` tag probe only if someone wants an automated release job in gates.** Use a throwaway branch with a `workflow_dispatch`-only workflow, `permissions: contents: write`, and one step:

   ```
   gh api repos/$GITHUB_REPOSITORY/git/refs -f ref=refs/tags/probe-tag-$GITHUB_RUN_ID -f sha=$GITHUB_SHA
   gh api -X DELETE repos/$GITHUB_REPOSITORY/git/refs/tags/probe-tag-$GITHUB_RUN_ID
   ```

   Use a non-`v` name so Dependabot can never offer it. Delete the branch afterwards. Even if it is allowed, a tag created by `GITHUB_TOKEN` triggers no further workflows. It would also put a `contents: write` token into gates' workflow surface, which `run/permissions-diff.py` treats as a protected change. The current manual and App release path avoids both, so I do not recommend changing it.
