import { formatPrice } from "./formatters";

test("formats prices as $X.XX with exactly two decimals", () => {
  expect(formatPrice(45)).toBe("$45.00");
  expect(formatPrice(77.55)).toBe("$77.55");
  expect(formatPrice(102.292026)).toBe("$102.29");
  expect(formatPrice(0)).toBe("$0.00");
});

test("adds thousands separators for prices at or above 1000", () => {
  expect(formatPrice(1234.5)).toBe("$1,234.50");
  expect(formatPrice(1000)).toBe("$1,000.00");
  expect(formatPrice(1234567.891)).toBe("$1,234,567.89");
});

test("renders a dash for missing prices", () => {
  expect(formatPrice(null)).toBe("—");
  expect(formatPrice(undefined)).toBe("—");
});
