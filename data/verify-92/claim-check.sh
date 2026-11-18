#!/bin/bash
# Written from PR 92's claim alone: "run git lfs pre-push only in repositories that use LFS".
# usage: claim-check.sh <path-to-pre-push> [nolfs]   (nolfs = no git-lfs on PATH)
set -u
HOOK=$1; MODE=${2:-}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null HOME=$T/home
mkdir -p "$HOME" "$T/hooks" "$T/bin"
cp "$HOOK" "$T/hooks/pre-push"; chmod +x "$T/hooks/pre-push"
for s in watermark-scan.py exposure-scan.py signature-check.py check-orphan-dirs gitleaks-pre-push; do
  printf '#!/bin/sh\ncat >/dev/null\nexit 0\n' > "$T/hooks/$s"; chmod +x "$T/hooks/$s"; done
touch "$T/hooks/watermark-allow.conf" "$T/hooks/watermark-optout.conf" "$T/hooks/pushrange.py" "$T/hooks/gitleaks.toml"
LOG=$T/lfs.log
cat > "$T/bin/git-lfs" <<X
#!/bin/sh
echo "RAN \$*" >> "$LOG"; cat >> "$LOG"; exit 0
X
chmod +x "$T/bin/git-lfs"
if [ "$MODE" = nolfs ]; then
  # a PATH with git and core utils but no git-lfs
  mkdir -p "$T/sys"; for b in git sh cat mktemp rm sed cmp pgrep kill dirname pwd ls find grep head tr env mkdir sleep wc test printf awk; do p=$(command -v $b) && ln -sf "$p" "$T/sys/$b"; done
  P="$T/sys"
else P="$T/bin:$PATH"; fi
G() { git -c user.name=t -c user.email=t@example.invalid -c core.hooksPath="$T/hooks" -c init.defaultBranch=main "$@"; }
pass=0; fail=0
mk() { rm -rf "$T/r" "$T/remote.git" "$LOG"; G init -q --bare "$T/remote.git"; G init -q "$T/r"; G -C "$T/r" remote add origin https://example.invalid/x/y; G -C "$T/r" remote add dst "$T/remote.git"; }
push() { (cd "$T/r" && PATH="$P" git -c core.hooksPath="$T/hooks" push -q dst "$@" 2>"$T/err"); }
expect() { # name rc_expected lfs(yes|no) actual_rc
  local ran=no; [ -s "$LOG" ] && grep -q '^RAN' "$LOG" && ran=yes
  if [ "$4" = "$2" ] && [ "$ran" = "$3" ]; then echo "PASS $1 (rc=$4 lfs_ran=$ran)"; pass=$((pass+1));
  else echo "FAIL $1 (rc=$4 want $2, lfs_ran=$ran want $3)"; sed 's/^/   | /' "$T/err" | head -5; fail=$((fail+1)); fi; }
C() { G -C "$T/r" "$@"; }
# 1 no LFS at all -> git-lfs must not run
mk; echo a > "$T/r/a"; C add a; C commit -qm a; push main; rc=$?
if [ "$MODE" = nolfs ]; then expect "no-LFS repo pushes without git-lfs installed" 0 no $rc; else expect "no-LFS repo: git-lfs not run" 0 no $rc; fi
# 2 root .gitattributes filter=lfs -> must run
mk; printf '*.bin filter=lfs diff=lfs merge=lfs -text\n' > "$T/r/.gitattributes"; C add .gitattributes; C commit -qm a; push main; rc=$?
if [ "$MODE" = nolfs ]; then expect "LFS-attributes repo without git-lfs refuses" 1 no $rc; else expect "LFS attributes: git-lfs run" 0 yes $rc; fi
# 3 lfs/objects present in git dir -> must run
mk; echo a > "$T/r/a"; C add a; C commit -qm a; mkdir -p "$T/r/.git/lfs/objects/ab/cd"; touch "$T/r/.git/lfs/objects/ab/cd/abcd"; push main; rc=$?
[ "$MODE" = nolfs ] || expect "LFS objects: git-lfs run" 0 yes $rc
echo "summary: $pass pass, $fail fail"; [ $fail = 0 ]
