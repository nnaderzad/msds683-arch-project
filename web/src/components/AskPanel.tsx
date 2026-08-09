import { useState } from "react";
import { askQuestion } from "../api/client";
import type { AskResponse } from "../types";

// Demo insurance: one easy lookup, one aggregate, and one guardrail probe so the
// live audience sees a real answer, a real number, and a refusal in three clicks.
const EXAMPLE_QUESTIONS = [
  "What is the cheapest ticket price ever observed for Everclear?",
  "Which upcoming Dance/Electronic shows in the Bay Area in the next month have a predicted price under $100? List event, venue, date, and predicted price.",
  "Which artist has the highest average Google Trends interest across all metros?",
];

const STATUS_COPY: Record<AskResponse["status"], string> = {
  ok: "Answered",
  refused: "Refused (out of scope or unsafe semantics)",
  blocked: "Blocked by SQL guardrails",
  rate_limited: "Rate limited",
  error: "Error",
};

function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null) {
    return null;
  }
  if (bytes >= 1024 ** 3) {
    return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  }
  if (bytes >= 1024 ** 2) {
    return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  }
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

function ResultsTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) {
    return null;
  }
  const columns = Object.keys(rows[0]);
  const visible = rows.slice(0, 20);
  return (
    <div className="ask-table-wrap">
      <table className="ask-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{row[column] == null ? "—" : String(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > visible.length && (
        <p className="ask-table-note">Showing first {visible.length} of {rows.length} rows.</p>
      )}
    </div>
  );
}

const SYNTH_EXAMPLE = "Which sold-out shows have the highest resale markup?";

type AskPanelProps = {
  // Embedded-below-the-dashboard mode: fewer example chips, tighter spacing.
  compact?: boolean;
};

export function AskPanel({ compact = false }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "done" | "failed">("idle");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [useSynth, setUseSynth] = useState(false);

  const submit = (text: string, dataset?: "real" | "synth") => {
    const trimmed = text.trim();
    if (trimmed.length < 3 || phase === "loading") {
      return;
    }
    setPhase("loading");
    setResponse(null);
    askQuestion(trimmed, dataset ?? (useSynth ? "synth" : "real"))
      .then((result) => {
        setResponse(result);
        setPhase("done");
      })
      .catch(() => {
        setPhase("failed");
      });
  };

  const exampleQuestions = compact ? EXAMPLE_QUESTIONS.slice(0, 2) : EXAMPLE_QUESTIONS;

  return (
    <section className={compact ? "ask-panel is-compact" : "ask-panel"} aria-label="Ask the music warehouse">
      <div className="combined-heading">
        <div>
          <h3>Ask the music warehouse</h3>
          <p>
            Natural-language questions become guardrailed BigQuery SQL over the gold star
            schema (Gemini on Vertex AI). The generated SQL is always shown.
          </p>
        </div>
      </div>

      <form
        className="ask-form"
        onSubmit={(event) => {
          event.preventDefault();
          submit(question);
        }}
      >
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="e.g. When is the next show at The Independent in San Francisco?"
          aria-label="Question"
          rows={2}
          maxLength={500}
        />
        <button type="submit" disabled={phase === "loading" || question.trim().length < 3}>
          {phase === "loading" ? "Working…" : "Ask"}
        </button>
      </form>

      <div className="ask-examples">
        {exampleQuestions.map((example) => (
          <button
            key={example}
            type="button"
            className="ask-chip"
            disabled={phase === "loading"}
            onClick={() => {
              setQuestion(example);
              submit(example);
            }}
          >
            {example.length > 60 ? `${example.slice(0, 57)}…` : example}
          </button>
        ))}
        {useSynth && (
          <button
            type="button"
            className="ask-chip is-synth"
            disabled={phase === "loading"}
            onClick={() => {
              setQuestion(SYNTH_EXAMPLE);
              submit(SYNTH_EXAMPLE, "synth");
            }}
          >
            {SYNTH_EXAMPLE}
          </button>
        )}
        <label className="ask-synth-toggle" title="Simulated sellouts & resale prices — clearly labeled synthetic; real event/venue names">
          <input
            type="checkbox"
            checked={useSynth}
            onChange={(event) => setUseSynth(event.target.checked)}
          />
          Synthetic sandbox (sellouts &amp; resale)
        </label>
      </div>

      {phase === "loading" && (
        <section className="status-panel" aria-live="polite">
          <strong>Asking the agent</strong>
          <p>Generating SQL, running guardrails, querying BigQuery…</p>
        </section>
      )}

      {phase === "failed" && (
        <section className="status-panel is-error" role="alert">
          <strong>Request failed</strong>
          <p>Could not reach the /ask endpoint. Check the API and try again.</p>
        </section>
      )}

      {phase === "done" && response && (
        <div className="ask-result">
          <div className="ask-badges" aria-label="Guardrail verdicts">
            <span className={`ask-badge is-${response.status}`}>
              {STATUS_COPY[response.status] ?? response.status}
            </span>
            {response.synthetic && (
              <span className="ask-badge is-synthetic">SYNTHETIC DATA</span>
            )}
            {(response.guardrails ?? []).map((verdict) => (
              <span
                key={verdict.name}
                className={`ask-badge ${verdict.passed ? "is-pass" : "is-fail"}`}
                title={verdict.detail}
              >
                {verdict.name}
              </span>
            ))}
          </div>

          {response.sql && (
            <pre className="ask-sql" aria-label="Generated SQL">
              {response.sql}
            </pre>
          )}

          {response.answer && <p className="ask-answer">{response.answer}</p>}

          {response.status === "ok" && <ResultsTable rows={response.rows ?? []} />}

          <p className="ask-meta">
            {[
              formatBytes(response.bytes_processed),
              response.model,
              response.latency_ms != null ? `${(response.latency_ms / 1000).toFixed(1)} s` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
      )}
    </section>
  );
}
