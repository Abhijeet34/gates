// SPDX-License-Identifier: MIT
// Copyright (c) 2019 base64url contributors. Copied unmodified from base64url 3.0.1.
"use strict";

function encode(text) {
  return Buffer.from(text).toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

module.exports = { encode };
