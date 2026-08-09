import { afterEach, expect, test, vi } from "vitest";
import { scrollToPageTop } from "./scroll";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("scrolls smoothly to the top by default", () => {
  const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

  scrollToPageTop();

  expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
});

test("scrolls instantly when the viewer prefers reduced motion", () => {
  const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true }),
  );

  scrollToPageTop();

  expect(window.matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
  expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
});
