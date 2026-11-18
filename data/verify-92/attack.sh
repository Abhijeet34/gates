#!/bin/bash
# usage: attack.sh <pre-push>   real git-lfs on PATH; logs whether git-lfs pre-push ran via a GIT_TRACE-free wrapper
set -u
HOOK=$1
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=$T/gitconfig GIT_CONFIG_SYSTEM=/dev/null HOME=$T/home
mkdir -p "$HOME" "$T/hooks" "$T/bin"
git config --global user.name t; git config --global user.email t@example.invalid; git config --global init.defaultBranch main
git config --global filter.lfs.clean 'git-lfs clean -- %f'; git config --global filter.lfs.smudge 'git-lfs smudge -- %f'
git config --global filter.lfs.process 'git-lfs filter-process'; git config --global filter.lfs.required true
cp "$HOOK" "$T/hooks/pre-push"; chmod +x "$T/hooks/pre-push"
for s in watermark-scan.py exposure-scan.py signature-check.py check-orphan-dirs gitleaks-pre-push; do
  printf '#!/bin/sh\ncat >/dev/null\nexit 0\n' > "$T/hooks/$s"; chmod +x "$T/hooks/$s"; done
touch "$T/hooks/watermark-allow.conf" "$T/hooks/watermark-optout.conf" "$T/hooks/pushrange.py" "$T/hooks/gitleaks.toml"
LOG=$T/lfs.log; REAL=$(command -v git-lfs)
printf '#!/bin/sh\n[ "$1" = pre-push ] && echo "RAN $*" >> %s\nexec %s "$@"\n' "$LOG" "$REAL" > "$T/bin/git-lfs"; chmod +x "$T/bin/git-lfs"
export PATH="$T/bin:$PATH"
git config --global core.hooksPath "$T/hooks"
mk() { rm -rf "$T/r" "$T/remote.git" "$T/wt" "$LOG"; git init -q --bare "$T/remote.git"; git init -q "$T/r"; git -C "$T/r" remote add origin https://example.invalid/x/y; git -C "$T/r" remote add dst "file://$T/remote.git"; }
C() { git -C "$T/r" "$@"; }
ran() { [ -s "$LOG" ] && echo yes || echo no; }
rep() { echo "$1: push_rc=$2 git-lfs_pre-push_ran=$(ran)${3:+ $3}"; }
remote_objs() { find "$T/remote.git/lfs/objects" -type f 2>/dev/null | wc -l; }

echo "--- E2E: real git lfs track, push to file remote"
mk; (cd "$T/r" && git lfs install --local -q 2>/dev/null; git lfs track '*.bin' >/dev/null); head -c 2048 /dev/urandom > "$T/r/big.bin"; C add .gitattributes big.bin; C commit -qm lfs
(cd "$T/r" && git push -q dst main 2>"$T/err"); rep "E2E real LFS file" $? "remote_lfs_objects=$(remote_objs)"
echo "--- E2E: plain repo, timed"
mk; echo a > "$T/r/a"; C add a; C commit -qm a
s=$(date +%s.%N); (cd "$T/r" && git push -q dst main 2>"$T/err"); rc=$?; e=$(date +%s.%N); rep "E2E plain" $rc "hook+push_s=$(echo "$e - $s" | bc)"

echo "--- A1 .gitattributes only in a nested dir (a/b/.gitattributes)"
mk; mkdir -p "$T/r/a/b"; printf '*.bin filter=lfs diff=lfs merge=lfs -text\n' > "$T/r/a/b/.gitattributes"; head -c 100 /dev/urandom > "$T/r/a/b/x.bin"; rm -rf "$T/r/.git/lfs"
C add -A; C commit -qm n; rm -rf "$T/r/.git/lfs"; (cd "$T/r" && git push -q dst main 2>"$T/err"); rep "A1 nested attrs, lfs/objects removed" $?
echo "--- A2 linked worktree, objects only in the common dir"
mk; echo a > "$T/r/a"; C add a; C commit -qm a; mkdir -p "$T/r/.git/lfs/objects"; C worktree add -q -b wtb "$T/wt"; echo w > "$T/wt/w"; git -C "$T/wt" add w; git -C "$T/wt" commit -qm w
(cd "$T/wt" && git push -q dst wtb 2>"$T/err"); rep "A2 worktree push" $?
echo "--- A3 attrs only on a non-tip commit: pointer files committed, then .gitattributes deleted"
mk; (cd "$T/r" && git lfs track '*.bin' >/dev/null); head -c 100 /dev/urandom > "$T/r/p.bin"; C add .gitattributes p.bin; C commit -qm p; C rm -q .gitattributes; C commit -qm drop; rm -rf "$T/r/.git/lfs"
(cd "$T/r" && git push -q dst main 2>"$T/err"); rep "A3 history-only LFS, no local objects" $? "remote_lfs_objects=$(remote_objs) (ceiling stated in hook comment)"
echo "--- A4 commented-out filter=lfs in .gitattributes"
mk; printf '# *.bin filter=lfs (disabled)\n' > "$T/r/.gitattributes"; C add .gitattributes; C commit -qm c; (cd "$T/r" && git push -q dst main 2>"$T/err"); rep "A4 comment only (false positive is the safe direction)" $?
echo "--- A5 delete-only push of a branch in a repo with LFS attrs on another branch"
mk; echo a > "$T/r/a"; C add a; C commit -qm a; (cd "$T/r" && git push -q dst main 2>"$T/err"); C checkout -q -b lfsb; printf '*.bin filter=lfs\n' > "$T/r/.gitattributes"; C add .gitattributes; C commit -qm l; (cd "$T/r" && git push -q dst lfsb 2>/dev/null); rm -f "$LOG"
(cd "$T/r" && git push -q dst :lfsb 2>"$T/err"); rep "A5 delete-only push" $?
echo "--- A6 annotated tag whose commit names the filter"
mk; printf '*.bin filter=lfs\n' > "$T/r/.gitattributes"; C add .gitattributes; C commit -qm t; C tag -a -m t v1; rm -rf "$T/r/.git/lfs"; (cd "$T/r" && git push -q dst v1 2>"$T/err"); rep "A6 annotated tag only" $?
echo "--- A7 pathspec-ish/odd file named '.gitattributes ' and a dir called .gitattributes"
mk; mkdir -p "$T/r/.gitattributes.d"; printf 'filter=lfs\n' > "$T/r/x.txt"; C add -A; C commit -qm o; (cd "$T/r" && git push -q dst main 2>"$T/err"); rep "A7 filter=lfs only in a non-attributes file" $?
echo "--- A8 garbage/unknown sha on stdin, hook run directly"
mk; echo a > "$T/r/a"; C add a; C commit -qm a; (cd "$T/r" && printf 'refs/heads/main %s refs/heads/main %s\n' deadbeefdeadbeefdeadbeefdeadbeefdeadbeef 0000000000000000000000000000000000000000 | "$T/hooks/pre-push" dst "file://$T/remote.git" 2>"$T/err"); rep "A8 unknown sha" $?
echo "--- A9 hook run outside any repository (rev-parse fails -> fail-safe runs lfs?)"
rm -f "$LOG"; (cd "$T" && printf '' | GIT_DIR=/nonexistent "$T/hooks/pre-push" x y 2>"$T/err"); rep "A9 no repository" $? "stderr=$(tr '\n' ' ' < "$T/err" | head -c 160)"
