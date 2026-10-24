"use strict";
const { render } = require("changelog-render");
const { heading } = require("@notes/format");

module.exports = (entries) => heading("Release notes") + render(entries);
