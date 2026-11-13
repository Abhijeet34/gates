#!/usr/bin/env bash
# Regression tests for run/install-gitleaks.sh, the install every secret-scan
# job runs. Run under `bash -e`, the way the runner runs it.
#
# The script is run as a copy whose release URL points at a local server and
# whose pinned digest is the fixture tarball's; every other line, the curl
# options included, is the shipped script's. A stub sudo installs into the
# work directory.
#
#   1. one connection reset (curl exit 56, as on 2026-11-13) is retried and the
#      install succeeds,
#   2. a server that resets every connection still fails the step, named,
#   3. a tarball that does not match the pinned digest is refused, not installed.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

workdir=$(mktemp -d) || exit 2
server_pid=
trap '[ -z "$server_pid" ] || kill "$server_pid" 2>/dev/null; rm -rf "$workdir"' EXIT
failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

# The fixture release: a tarball holding an executable named gitleaks.
mkdir "$workdir/src" "$workdir/bin" "$workdir/dest"
printf '#!/bin/sh\necho fixture-gitleaks\n' >"$workdir/src/gitleaks"
chmod +x "$workdir/src/gitleaks"
tar -czf "$workdir/release.tar.gz" -C "$workdir/src" gitleaks
good_sha=$(sha256sum "$workdir/release.tar.gz" | cut -d' ' -f1)

# sudo install ... /usr/local/bin/gitleaks -> install into $DEST instead.
cat >"$workdir/bin/sudo" <<'STUB'
#!/usr/bin/env bash
args=()
for a in "$@"; do
  [ "$a" = /usr/local/bin/gitleaks ] && a="$DEST/gitleaks"
  args+=("$a")
done
exec "${args[@]}"
STUB
chmod +x "$workdir/bin/sudo"

# A server that answers the first RESETS connections with a TCP reset after
# reading the request, then serves the tarball.
cat >"$workdir/server.py" <<'PY'
import socket, struct, sys
body = open(sys.argv[1], "rb").read()
resets = int(sys.argv[2])
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(8)
with open(sys.argv[3], "w") as f:
    f.write(str(srv.getsockname()[1]))
n = 0
while True:
    conn, _ = srv.accept()
    n += 1
    with open(sys.argv[4], "w") as f:
        f.write(str(n))
    conn.recv(65536)
    if n <= resets:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()
        continue
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/gzip\r\n"
                 b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
    conn.close()
PY

start_server() { # start_server <resets>
  if [ -n "$server_pid" ]; then kill "$server_pid" 2>/dev/null; wait "$server_pid" 2>/dev/null || true; fi
  rm -f "$workdir/port" "$workdir/count"
  python3 -I "$workdir/server.py" "$workdir/release.tar.gz" "$1" "$workdir/port" "$workdir/count" &
  server_pid=$!
  for _ in $(seq 50); do [ -s "$workdir/port" ] && break; sleep 0.1; done
  port=$(cat "$workdir/port")
}

# The shipped script with its release URL and digest repointed. Each rewrite
# must land, or the test would be exercising the real download.
make_script() { # make_script <sha256>
  local out="$workdir/install-gitleaks.sh"
  sed -e "s|^url=\"https://github.com/gitleaks/gitleaks/releases/download/.*\"\$|url=\"http://127.0.0.1:${port}/gitleaks.tar.gz\"|" \
      -e "s|^GITLEAKS_SHA256=\"[0-9a-f]*\"\$|GITLEAKS_SHA256=\"$1\"|" \
      run/install-gitleaks.sh >"$out"
  if ! grep -q "^url=\"http://127.0.0.1:${port}/" "$out" || ! grep -q "^GITLEAKS_SHA256=\"$1\"\$" "$out"; then
    echo "the URL or digest line of run/install-gitleaks.sh moved; update this test" >&2
    exit 2
  fi
}

run_install() {
  rm -f "$workdir/dest/gitleaks"
  rc=0
  out=$(PATH="$workdir/bin:$PATH" no_proxy=127.0.0.1 NO_PROXY=127.0.0.1 DEST="$workdir/dest" bash -e "$workdir/install-gitleaks.sh" 2>&1) || rc=$?
  count=$(cat "$workdir/count" 2>/dev/null || echo 0)
}

# 1. A single reset is retried.
start_server 1
make_script "$good_sha"
run_install
if [ "$rc" -eq 0 ] && [ "$("$workdir/dest/gitleaks" 2>/dev/null)" = fixture-gitleaks ] && [ "$count" -eq 2 ]; then
  pass "one connection reset is retried and the install succeeds"
else
  fail "one connection reset is retried and the install succeeds (rc=$rc, connections=$count): $out"
fi

# 2. A server that never recovers still fails the step, after the retries.
start_server 1000
make_script "$good_sha"
run_install
if [ "$rc" -ne 0 ] && [ ! -e "$workdir/dest/gitleaks" ] && [ "$count" -eq 5 ] && grep -qi 'reset' <<<"$out"; then
  pass "a reset on every attempt fails the step after four retries"
else
  fail "a reset on every attempt fails the step after four retries (rc=$rc, connections=$count): $out"
fi

# 3. The digest still decides: a mismatching tarball is refused.
start_server 0
make_script "$(printf '0%.0s' $(seq 64))"
run_install
if [ "$rc" -ne 0 ] && [ ! -e "$workdir/dest/gitleaks" ] && grep -q 'FAILED' <<<"$out"; then
  pass "a tarball not matching the pinned digest is refused"
else
  fail "a tarball not matching the pinned digest is refused (rc=$rc): $out"
fi

exit "$failed"
