#!/usr/bin/env bash
# Judge the open advisories against the caller's deferral list.
set -uo pipefail

refuse() { echo "::error::$1" >&2; exit 1; }

case "$SEVERITY_FLOOR" in
  critical | high | medium | low) ;;
  *) refuse "severity_floor is \"$SEVERITY_FLOOR\"; it must be one of critical, high, medium, low." ;;
esac
case "$FAIL_ON_DEVELOPMENT" in
  true | false) ;;
  *) refuse "fail_on_development is \"$FAIL_ON_DEVELOPMENT\"; it must be true or false." ;;
esac
[ -r "$ALERTS_JSON" ] || refuse "the alert inventory at $ALERTS_JSON is missing, so nothing was judged."

# Only a pull request has changes of its own; every other event is
# judged on the default branch's alerts alone, exactly as before.
compare=$(mktemp)
if [ "$EVENT_NAME" != pull_request ]; then
  printf '{"changes":[]}\n' >"$compare"
elif ! jq -e 'type == "object" and ((.error | type) == "string" or (.changes | type) == "array")' \
    "$COMPARE_JSON" >/dev/null 2>&1; then
  jq -n --arg f "$COMPARE_JSON" '{error: "the compare step left no readable record at \($f)"}' >"$compare"
else
  cp "$COMPARE_JSON" "$compare"
fi

# --- the deferral list ---------------------------------------------
#
# Read from the repository rather than from GitHub's dismiss button,
# which records the decision where no diff shows it and no reviewer
# meets it. Real YAML, parsed with yq into JSON and validated with jq,
# so a malformed file is a named refusal rather than a silently empty
# list. yq missing is itself a refusal: an unreadable deferral list
# that defaults to empty would fail LOUDLY here, but one that defaults
# to empty on a repository with no failing alerts would pass while
# having read nothing.
deferrals=$(mktemp)
# GitHub's escaping for a workflow-command property value.
DEFERRALS_FILE_PROP=${DEFERRALS_PATH//%/%25}
DEFERRALS_FILE_PROP=${DEFERRALS_FILE_PROP//$'\r'/%0D}
DEFERRALS_FILE_PROP=${DEFERRALS_FILE_PROP//$'\n'/%0A}
DEFERRALS_FILE_PROP=${DEFERRALS_FILE_PROP//:/%3A}
DEFERRALS_FILE_PROP=${DEFERRALS_FILE_PROP//,/%2C}
if [ -e "$DEFERRALS_PATH" ]; then
  command -v yq >/dev/null 2>&1 \
    || refuse "$DEFERRALS_PATH exists but yq is not installed, so the deferral list could not be read."
  yq -o=json '.' "$DEFERRALS_PATH" >"$deferrals" 2>"$deferrals.err" \
    || refuse "$DEFERRALS_PATH is not valid YAML: $(tr '\n' ' ' <"$deferrals.err")"
else
  printf '{"deferrals":[]}\n' >"$deferrals"
fi

# Every rule the file has to satisfy, as one list of named errors.
# All four keys are required and an unknown key is an error, for the
# reason .ci/shared-workflows.md gives for the generated-file
# exceptions: `until` stated separately from `reason` is what stops a
# standing exemption reading as a one-off, and it is the one field no
# check can grade - the parser asserts it is answered, a reviewer
# asserts the answer is true. No expiry DATE, deliberately and for the
# same reason recorded there: a date is the runner's clock rather than
# a property of the decision, it permits everything until it passes,
# and it is renewed by editing one line.
errors=$(jq -r '
  def required: ["ghsa", "package", "reason", "until"];
  def nonempty($v): ($v | type) == "string" and (($v | gsub("\\s"; "")) | length) > 0;
  ( if type != "object" then ["the file must be a mapping with a `deferrals:` key"]
    else
      ( (keys_unsorted - ["deferrals"]) | map("unknown top-level key `\(.)`") )
      + ( if has("deferrals") | not then ["no `deferrals:` key"]
          elif (.deferrals | type) != "array" then ["`deferrals:` must be a list"]
          else
            ( [ .deferrals | to_entries[]
                | .key as $i | .value as $e
                | if ($e | type) != "object" then "deferral \($i + 1): must be a mapping"
                  else
                    ( ($e | keys_unsorted) - required | map("deferral \($i + 1): unknown key `\(.)`") )
                    + ( required - ($e | keys_unsorted) | map("deferral \($i + 1): missing required key `\(.)`") )
                    + ( required | map(select($e[.] != null and (nonempty($e[.]) | not))
                                       | "deferral \($i + 1): `\(.)` is empty") )
                    + ( if ($e.ghsa | type) == "string"
                          and (($e.ghsa | test("^GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$")) | not)
                        then ["deferral \($i + 1): `ghsa` is \"\($e.ghsa)\", which is not a GHSA id"] else [] end )
                    | .[]
                  end ] )
            + ( [ .deferrals | map(select(type == "object") | .ghsa) | group_by(.)[]
                  | select(length > 1) | "`\(.[0])` is listed \(length) times" ] )
          end )
    end ) | .[]' "$deferrals" 2>&1) \
  || refuse "$DEFERRALS_PATH could not be validated: $errors"
[ -z "$errors" ] || {
  printf '%s\n' "$errors" | while IFS= read -r e; do echo "::error file=$DEFERRALS_FILE_PROP::$e" >&2; done
  refuse "$DEFERRALS_PATH is malformed; every deferral needs ghsa, package, reason and until."
}

# --- the verdict ----------------------------------------------------
verdict=$(jq -n \
  --slurpfile alerts "$ALERTS_JSON" \
  --slurpfile deferrals "$deferrals" \
  --slurpfile compare "$compare" \
  --arg floor "$SEVERITY_FLOOR" \
  --argjson faildev "$FAIL_ON_DEVELOPMENT" '
  def rank: {"low": 1, "medium": 2, "high": 3, "critical": 4}[.] // 0;
  # PEP 503 folding, so a pip alert for Foo_Bar meets the change to foo-bar.
  def norm: ascii_downcase | gsub("[-_.]+"; "-");
  # Whether the pull request makes a $type change, at this alert own
  # manifest, to a version of its package that carries its advisory.
  def touches_at($chg; $x; $type; $m):
    any($chg[]; .change_type == $type and .manifest == $m
                and (.name | norm) == ($x.pkg | norm)
                and any(.vulnerabilities[]; .advisory_ghsa_id == $x.ghsa));
  def touches($chg; $x; $type): touches_at($chg; $x; $type; $x.manifest);
  # The GitHub compare lists an npm change under the lockfile alone, never under
  # package.json, so a package.json alert is judged on the lockfiles in its
  # own directory. Any other manifest type keeps the same-manifest rule.
  def siblings($x):
    if ($x.manifest | test("(^|/)package\\.json$"))
    then ($x.manifest | sub("package\\.json$"; "")) as $dir
         | ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"] | map($dir + .)
    else [] end;
  def fixed($chg; $x):
    (touches($chg; $x; "removed") and (touches($chg; $x; "added") | not))
    or ( siblings($x) as $s
         | any($s[]; . as $m | touches_at($chg; $x; "removed"; $m))
           and (any($s[]; . as $m | touches_at($chg; $x; "added"; $m)) | not) );
  ($compare[0].error // null) as $cerr
  | ($compare[0].changes // []) as $chg
  | ($alerts[0] // []) as $a
  | ($deferrals[0].deferrals // []) as $d
  | ($a | map({
      ghsa: .security_advisory.ghsa_id,
      sev: .security_advisory.severity,
      # A null scope is NOT development. GitHub omits the label when
      # it cannot tell, and treating unknown as exempt is the scope
      # label being trusted one layer further down.
      scope: (.dependency.scope // "unknown"),
      pkg: .dependency.package.name,
      manifest: (.dependency.manifest_path // "?"),
      url: (.html_url // "")
    }) | map(. + {fixed: fixed($chg; .)})) as $open
  | ($open | map(select((.sev | rank) >= ($floor | rank))
                        | select($faildev or .scope != "development"))) as $atfloor
  | ($d | map(.ghsa)) as $dghsa
  | ($atfloor | map(select(.ghsa as $g | ($dghsa | index($g)) | not))) as $undeferred
  | {
      open: $open,
      counts: ($open | group_by(.sev) | map({sev: .[0].sev, n: length})
               | sort_by(.sev | rank) | reverse),
      deferred: ($atfloor | map(select(.ghsa as $g | $dghsa | index($g)))),
      fixed: ($open | map(select(.fixed))),
      failing: ($undeferred | map(select(.fixed | not))),
      compare_error: $cerr,
      # A deferral naming a GHSA with no open alert is dead text, not a
      # false verdict: it is keyed on the exact advisory, so it can
      # never widen to cover a new one. Reported, never fatal - a
      # repository that FIXED the advisory must not go red for it.
      stale: ($d | map(select(.ghsa as $g | ($open | map(.ghsa) | index($g)) | not))),
      # Mis-keyed, though, is a real error: the deferral names an open
      # advisory but the wrong package, so the reviewer approved a
      # sentence about something else.
      miskeyed: ($d | map(. as $e
                  | select(($open | map(.ghsa) | index($e.ghsa)) != null)
                  | select(($open | map(select(.ghsa == $e.ghsa) | .pkg) | index($e.package)) == null)
                  | {ghsa: $e.ghsa, claimed: $e.package,
                     actual: ($open | map(select(.ghsa == $e.ghsa) | .pkg) | unique | join(", "))})),
      reasons: ($d | map({key: .ghsa, value: {reason, until}}) | from_entries)
    }') \
  || refuse "the verdict could not be computed from $ALERTS_JSON."
[ -n "$verdict" ] || refuse "the verdict could not be computed from $ALERTS_JSON."

# The path is passed as data, never spliced into jq program text: a
# `"` in it broke the program, and a `::` or newline in a workflow
# command property is read as the command's own syntax.
get() { printf '%s' "$verdict" | jq -r --arg file "$DEFERRALS_FILE_PROP" "$1"; }

# The standing count, in the log and in the run summary, so low and
# medium are visible as a trend rather than invisible until one of
# them becomes high. Printed on every run, including the green ones.
{
  echo "### Dependency advisories"
  echo
  echo "Floor \`$SEVERITY_FLOOR\`; development-scoped alerts $([ "$FAIL_ON_DEVELOPMENT" = true ] && echo 'can fail' || echo 'are exempt')."
  echo
  echo "| severity | open |"
  echo "| --- | --- |"
  get '.counts[] | "| \(.sev) | \(.n) |"'
  [ "$(get '.open | length')" = 0 ] && echo "| _none_ | 0 |"
  echo
  echo "| severity | scope | package | GHSA | manifest |"
  echo "| --- | --- | --- | --- | --- |"
  get '.open | sort_by(.sev) | reverse | .[] | "| \(.sev) | \(.scope) | \(.pkg) | \(.ghsa) | \(.manifest) |"'
  if [ "$(get '.fixed | length')" != 0 ]; then
    echo
    echo "Fixed in this pull request, so not failing: it removes the vulnerable version at the alert's manifest (for a package.json, at a lockfile beside it) and adds none still affected."
    echo
    get '.fixed[] | "- \(.sev) \(.pkg) \(.ghsa) (\(.manifest))"'
  fi
} | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo

get '.miskeyed[] | "::error file=\($file)::deferral for \(.ghsa) names package `\(.claimed)`, but the open alert for it is `\(.actual)`"' >&2
[ "$(get '.miskeyed | length')" = 0 ] \
  || refuse "$DEFERRALS_PATH mis-names the package of an advisory it defers."

get '.stale[] | "::warning file=\($file)::deferral for \(.ghsa) (\(.package)) matches no open alert; the decision it records has outlived its reason and the entry can go"' >&2

deferred_n=$(get '.deferred | length')
if [ "$deferred_n" != 0 ]; then
  echo "$deferred_n advisory(ies) at or above \`$SEVERITY_FLOOR\` are deferred by $DEFERRALS_PATH:"
  get '.deferred[] as $x | .reasons[$x.ghsa] as $r
       | "  \($x.sev) \($x.pkg) \($x.ghsa)\n    reason: \($r.reason | gsub("\\s+"; " "))\n    until:  \($r.until | gsub("\\s+"; " "))"'
fi

failing_n=$(get '.failing | length')
compare_error=$(get '.compare_error // empty')
if [ "$failing_n" = 0 ]; then
  [ -z "$compare_error" ] \
    || echo "::warning::the dependency compare for this pull request could not be read, and no alert needed it: $compare_error"
  echo "no open advisory at or above \`$SEVERITY_FLOOR\` is undeferred; $(get '.open | length') open alert(s) inventoried"
  exit 0
fi

get '.failing[] | "::error::\(.sev) advisory in \(.pkg) (\(.scope) scope, \(.manifest)): \(.ghsa) \(.url)"' >&2
[ -z "$compare_error" ] \
  || refuse "$failing_n open advisory(ies) at or above \`$SEVERITY_FLOOR\` are open on the default branch, and whether this pull request fixes them COULD NOT BE ESTABLISHED: the dependency compare failed with $compare_error"
refuse "$failing_n open advisory(ies) at or above \`$SEVERITY_FLOOR\` are neither fixed nor recorded in $DEFERRALS_PATH. Upgrade the dependency, or add the GHSA id there with the reason it stands and the condition that ends it."
