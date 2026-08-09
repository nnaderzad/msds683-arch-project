// Bring the dashboard back into view after an off-screen action (e.g. picking a
// show from an ask-answer row rendered below the chart). Smooth by preference,
// instant for viewers who ask for reduced motion. matchMedia is feature-checked
// because jsdom (tests) does not implement it.
export function scrollToPageTop(): void {
  const reduceMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
}
