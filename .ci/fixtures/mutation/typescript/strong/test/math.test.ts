import { expect, test } from "vitest";
import { double, isEven } from "../src/math";

test("doubles", () => {
  expect(double(3)).toBe(6);
});

test("even", () => {
  expect(isEven(2)).toBe(true);
  expect(isEven(3)).toBe(false);
});
