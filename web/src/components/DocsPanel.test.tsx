import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DocsPanel, { docNameForHref } from "./DocsPanel";

// The diagram library is heavyweight and DOM-hungry; the docs view only needs
// its render() contract, so stub it to a recognizable SVG.
vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn().mockResolvedValue({ svg: '<svg role="img" aria-label="diagram"></svg>' }),
  },
}));

const DOCS = [
  { name: "data-model", title: "Data model", description: "Schema deep-dive" },
  { name: "REPO_STATE", title: "Repo state", description: "Where things stand" },
];

const DATA_MODEL_MD = [
  "# Data model",
  "",
  "The silver constellation feeds the gold star.",
  "",
  "| table | grain |",
  "| --- | --- |",
  "| fact_event_demand | event day |",
  "",
  "```mermaid",
  "graph TD; bronze-->silver;",
  "```",
  "",
  "See [repo state](REPO_STATE.md) and [the collector](../cloud_functions/main.py).",
].join("\n");

function mockDocsApi(markdownByName: Record<string, string>) {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/repo-docs")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(DOCS) });
    }
    const match = url.match(/\/repo-doc\/([^/?]+)$/);
    if (match) {
      const name = decodeURIComponent(match[1]);
      const markdown = markdownByName[name];
      if (markdown == null) {
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: "Not Found",
          json: () => Promise.resolve({ detail: "not found" }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ name, title: name, markdown }),
      });
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({}),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DocsPanel", () => {
  it("renders the first fetched doc with GFM tables and mermaid diagrams", async () => {
    mockDocsApi({ "data-model": DATA_MODEL_MD, REPO_STATE: "# Repo state" });
    render(<DocsPanel />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Data model" }),
    ).toBeInTheDocument();
    // GFM table support: the cell renders as a real table cell.
    expect(screen.getByRole("cell", { name: "fact_event_demand" })).toBeInTheDocument();
    // The ```mermaid fence becomes an SVG diagram (stubbed renderer).
    expect(await screen.findByRole("img", { name: "diagram" })).toBeInTheDocument();
  });

  it("maps internal .md links to served docs and flattens unresolvable links", async () => {
    const fetchMock = mockDocsApi({ "data-model": DATA_MODEL_MD, REPO_STATE: "# Repo state" });
    render(<DocsPanel />);
    await screen.findByRole("heading", { level: 1, name: "Data model" });

    // A relative link to a file we do not serve degrades to plain text.
    expect(screen.queryByRole("link", { name: "the collector" })).not.toBeInTheDocument();
    expect(screen.getByText("the collector")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "repo state" }));

    expect(
      await screen.findByRole("heading", { level: 1, name: "Repo state" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).endsWith("/repo-doc/REPO_STATE")),
    ).toBe(true);
  });

  it("shows a friendly message when a doc cannot be loaded", async () => {
    mockDocsApi({ REPO_STATE: "# Repo state" });
    render(<DocsPanel />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/could not load this document/i);
    });
    // The picker stays usable so the viewer can move to a doc that loads.
    await userEvent.click(screen.getByRole("button", { name: /repo state/i }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Repo state" }),
    ).toBeInTheDocument();
  });

  it("resolves relative markdown hrefs against the served doc list", () => {
    expect(docNameForHref("REPO_STATE.md", DOCS)).toBe("REPO_STATE");
    expect(docNameForHref("./data-model.md", DOCS)).toBe("data-model");
    expect(docNameForHref("docs/data-model.md#gold", DOCS)).toBe("data-model");
    expect(docNameForHref("missing.md", DOCS)).toBeNull();
    expect(docNameForHref("../cloud_functions/main.py", DOCS)).toBeNull();
    expect(docNameForHref("https://example.com/data-model.md", DOCS)).toBeNull();
    expect(docNameForHref("#anchor", DOCS)).toBeNull();
  });
});
