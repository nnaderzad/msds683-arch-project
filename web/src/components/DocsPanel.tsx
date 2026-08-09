import { isValidElement, useEffect, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchRepoDoc, fetchRepoDocs } from "../api/client";
import type { RepoDoc, RepoDocSummary } from "../types";

// Mermaid is imported dynamically inside the diagram component so the (large)
// library only loads when a doc actually contains a diagram, and initialized once.
let mermaidInitialized = false;
let mermaidSeq = 0;

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function MermaidDiagram({ chart }: { chart: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    setSvg(null);
    setFailed(false);

    import("mermaid")
      .then(async ({ default: mermaid }) => {
        if (!mermaidInitialized) {
          mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
          mermaidInitialized = true;
        }
        mermaidSeq += 1;
        const { svg: rendered } = await mermaid.render(`repo-doc-diagram-${mermaidSeq}`, chart);
        if (!cancelled) {
          setSvg(rendered);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [chart]);

  // An unparsable diagram still shows its source instead of hiding content.
  if (failed) {
    return <pre>{chart}</pre>;
  }

  if (!svg) {
    return <p className="docs-diagram-loading">Rendering diagram…</p>;
  }

  return <div className="docs-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function textContent(node: ReactNode): string {
  if (typeof node === "string") {
    return node;
  }
  if (Array.isArray(node)) {
    return node.map(textContent).join("");
  }
  return "";
}

// Map a relative markdown link (e.g. "data-model.md", "docs/data-model.md#gold")
// to the /repo-docs name it corresponds to, or null when it cannot be served.
export function docNameForHref(href: string | undefined, docs: RepoDocSummary[]): string | null {
  if (!href || href.startsWith("#") || href.startsWith("//") || /^[a-z][a-z0-9+.-]*:/i.test(href)) {
    return null;
  }
  const path = href.split("#")[0].split("?")[0];
  if (!/\.md$/i.test(path)) {
    return null;
  }
  const base = path.split("/").pop()!.replace(/\.md$/i, "");
  const match = docs.find((doc) => doc.name.replace(/\.md$/i, "") === base);
  return match ? match.name : null;
}

function isExternalHref(href: string | undefined): boolean {
  return !!href && (/^https?:/i.test(href) || href.startsWith("//") || /^mailto:/i.test(href));
}

function DocsPanel() {
  const [docs, setDocs] = useState<RepoDocSummary[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [doc, setDoc] = useState<RepoDoc | null>(null);
  const [docState, setDocState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  useEffect(() => {
    const controller = new AbortController();

    fetchRepoDocs(controller.signal)
      .then((list) => {
        setDocs(list);
        setListState("ready");
        setSelectedName((current) => current ?? list[0]?.name ?? null);
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setListState("error");
      });

    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedName) {
      return;
    }

    const controller = new AbortController();

    setDocState("loading");
    fetchRepoDoc(selectedName, controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) {
          return;
        }
        setDoc(loaded);
        setDocState("ready");
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setDoc(null);
        setDocState("error");
      });

    return () => controller.abort();
  }, [selectedName]);

  const markdownComponents: Components = {
    // Internal .md links switch to that doc when we serve it; other relative links
    // have nothing to resolve to, so they degrade to plain text. External links open
    // in a new tab.
    a: ({ href, children }) => {
      const target = docNameForHref(href, docs);
      if (target) {
        return (
          <button type="button" className="docs-link" onClick={() => setSelectedName(target)}>
            {children}
          </button>
        );
      }
      if (isExternalHref(href)) {
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        );
      }
      return <span>{children}</span>;
    },
    // ```mermaid fences render as diagrams; other fences stay scrollable code blocks.
    pre: ({ children }) => {
      const child = Array.isArray(children) ? children[0] : children;
      if (isValidElement(child)) {
        const { className, children: code } = child.props as {
          className?: string;
          children?: ReactNode;
        };
        if (className?.includes("language-mermaid")) {
          return <MermaidDiagram chart={textContent(code).trim()} />;
        }
      }
      return <pre>{children}</pre>;
    },
  };

  return (
    <section className="docs-panel" aria-label="How it works">
      <div className="combined-heading">
        <div>
          <h3>How it works</h3>
          <p>
            The project&apos;s committed documentation, rendered straight from the repository the
            pipeline runs from.
          </p>
        </div>
      </div>

      {listState === "loading" && (
        <section className="status-panel" aria-live="polite">
          <strong>Loading documentation</strong>
          <p>Fetching the doc list from /repo-docs.</p>
        </section>
      )}

      {listState === "error" && (
        <section className="status-panel is-error" role="alert">
          <strong>Documentation unavailable</strong>
          <p>Could not load the doc list. Check the API and try again.</p>
        </section>
      )}

      {listState === "ready" && docs.length === 0 && (
        <p className="search-empty">No documentation is published yet.</p>
      )}

      {listState === "ready" && docs.length > 0 && (
        <div className="docs-layout">
          <nav className="docs-nav" aria-label="Documents">
            {docs.map((entry) => (
              <button
                key={entry.name}
                type="button"
                className={entry.name === selectedName ? "is-active" : ""}
                onClick={() => setSelectedName(entry.name)}
              >
                <strong>{entry.title}</strong>
                <span>{entry.description}</span>
              </button>
            ))}
          </nav>

          <article className="docs-prose" aria-label="Document">
            {docState === "loading" && (
              <section className="status-panel" aria-live="polite">
                <strong>Loading document</strong>
                <p>Fetching {selectedName} from /repo-doc.</p>
              </section>
            )}
            {docState === "error" && (
              <section className="status-panel is-error" role="alert">
                <strong>Document unavailable</strong>
                <p>Could not load this document — it may have moved. Pick another doc.</p>
              </section>
            )}
            {docState === "ready" && doc && (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {doc.markdown}
              </ReactMarkdown>
            )}
          </article>
        </div>
      )}
    </section>
  );
}

export default DocsPanel;
