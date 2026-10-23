#!/usr/bin/env bash
# Build an input for run/ui-gates.py in the current directory, with a stub node.
#
#   build.sh axe red|clean        page.html; the stub reports a critical violation (red)
#                                 or only a moderate one (clean)
#   build.sh pixel red|clean      page.html and baseline.png, white 4x4; the stub's
#                                 screenshot moves one channel of one pixel by 200 (red)
#                                 or by 20, inside the tolerance (clean)
#   build.sh linux|darwin         the stub uname, which pixel and record read
#
# Each stub goes in ./bin, to be the whole PATH, and ./modules stands for the
# node_modules the stub never reads; the real browser is .ci/test-ui-gates.py --real.
set -euo pipefail

mkdir -p bin modules
versions='"versions": {"playwright": "1.63.0", "browser": "153.0.8010.12", "axe-core": "4.13.0"}'
case "$1" in
    axe)
        impact=critical
        [[ "$2" == clean ]] && impact=moderate
        printf '<!doctype html><title>t</title>\n' > page.html
        printf '{%s, "violations": [{"id": "image-alt", "impact": "%s", "help": "Images must have alternative text", "nodes": 1}]}\n' \
            "$versions" "$impact" > report.json
        printf '#!/bin/sh\n/bin/cat "%s/report.json"\n' "$PWD" > bin/node
        ;;
    pixel)
        moved=200
        [[ "$2" == clean ]] && moved=20
        printf '<!doctype html><title>t</title>\n' > page.html
        python3 - "$moved" <<'PY'
import struct, sys, zlib

def png(path, first):
    raw = b"".join(b"\0" + bytes(first if y == 0 else (255, 255, 255)) + b"\xff" * 9 for y in range(4))
    chunk = lambda k, b: struct.pack(">I", len(b)) + k + b + struct.pack(">I", zlib.crc32(k + b))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

png("baseline.png", (255, 255, 255))
png("shot.png", (255 - int(sys.argv[1]), 255, 255))
PY
        # The driver's last argument is where the screenshot goes.
        printf '#!/bin/sh\nfor out; do :; done\n/bin/cp "%s/shot.png" "$out"\necho '"'"'{%s}'"'"'\n' "$PWD" "$versions" > bin/node
        ;;
    linux | darwin)
        name=Linux
        [[ "$1" == darwin ]] && name=Darwin
        printf '#!/bin/sh\necho %s\n' "$name" > bin/uname
        ;;
esac
chmod +x bin/*
