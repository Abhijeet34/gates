"use strict";
const chokidar = require("chokidar");
const { z } = require("zod");
const { encode } = require("../vendor/base64url");
const relay = require("relay-sync-core");

const Note = z.object({ path: z.string(), body: z.string() });

function watch(dir, onNote) {
  return chokidar.watch(dir).on("change", (path) => onNote(Note.parse({ path, body: encode(path) })));
}

module.exports = { watch };
