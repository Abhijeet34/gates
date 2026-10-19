import { expect, test } from "vitest";
import { double } from "../src/math";

test("doubles", () => {
  expect(double(3)).toBe(6);
});
