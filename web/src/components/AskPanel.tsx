import { useState } from "react";
import { askQuestion, sendAskFeedback } from "../api/client";
import type { AskExchange, AskFeedbackVerdict, AskResponse } from "../types";
import { AskResultsTable } from "./AskResultsTable";

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

const SYNTH_EXAMPLE = "Which sold-out shows have the highest resale markup?";

type AskPanelProps = {
  // Embedded-below-the-dashboard mode: fewer example chips, tighter spacing.
  compact?: boolean;
  // Opens a show in the dashboard view; result rows carrying an event_id column
  // become clickable (with a hover stats card) when this is provided.
  onOpenShow?: (eventId: string) => void;
};

// Feedback is offered on every completed agent verdict, not just answered ones.
const FEEDBACK_STATUSES: AskResponse["status"][] = ["ok", "refused", "blocked"];

// One completed Q&A turn in the session transcript.
type AskTurn = {
  question: string;
  response: AskResponse;
};

// /ask follow-up context: the last 3 answered turns (status ok only), older first.
function historyFromTurns(turns: AskTurn[]): AskExchange[] {
  return turns
    .filter((turn) => turn.response.status === "ok" && turn.response.answer != null)
    .slice(-3)
    .map((turn) => ({ question: turn.question, answer: turn.response.answer as string }));
}

export function AskPanel({ compact = false, onOpenShow }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "done" | "failed">("idle");
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [useSynth, setUseSynth] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);

  // The latest turn keeps the full detail rendering; earlier turns collapse into
  // the compact conversation thread above the input.
  const latestTurn = turns.at(-1) ?? null;
  const olderTurns = turns.slice(0, -1);

  const submit = (text: string, dataset?: "real" | "synth") => {
    const trimmed = text.trim();
    if (trimmed.length < 3 || phase === "loading") {
      return;
    }
    setPhase("loading");
    // One vote per answer: a new question re-enables the feedback buttons.
    setFeedbackSent(false);
    askQuestion(trimmed, dataset ?? (useSynth ? "synth" : "real"), historyFromTurns(turns))
      .then((result) => {
        setTurns((current) => [...current, { question: trimmed, response: result }]);
        setPhase("done");
      })
      .catch(() => {
        setPhase("failed");
      });
  };

  const clearConversation = () => {
    setTurns([]);
    setPhase("idle");
    setFeedbackSent(false);
  };

  const giveFeedback = (verdict: AskFeedbackVerdict) => {
    if (!latestTurn || feedbackSent) {
      return;
    }
    const { question: askedQuestion, response } = latestTurn;
    // Fire-and-forget: show the thanks state immediately and never nag the user,
    // even if the POST fails or comes back rate_limited.
    setFeedbackSent(true);
    sendAskFeedback({
      verdict,
      question: askedQuestion,
      sql: response.sql ?? null,
      answer: response.answer ?? null,
      dataset: response.dataset,
      model: response.model,
      latency_ms: response.latency_ms,
      bytes_processed: response.bytes_processed ?? null,
      status: response.status,
    }).catch(() => {
      // Intentionally swallowed; the UI already acknowledged the vote.
    });
  };

  const exampleQuestions = compact ? EXAMPLE_QUESTIONS.slice(0, 2) : EXAMPLE_QUESTIONS;
  // The latest answer keeps the existing full-detail rendering (it stays visible
  // while a follow-up is loading; a new answer collapses it into the thread).
  const response = latestTurn?.response ?? null;

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
        {turns.length > 0 && (
          <button type="button" className="ask-clear" onClick={clearConversation}>
            Clear conversation
          </button>
        )}
      </div>

      {olderTurns.length > 0 && (
        <div className="ask-thread" aria-label="Earlier exchanges">
          {olderTurns.map((turn, index) => (
            <div key={index} className="ask-turn">
              <p className="ask-turn-question">{turn.question}</p>
              <p className="ask-turn-answer">
                {turn.response.answer ?? STATUS_COPY[turn.response.status] ?? turn.response.status}
              </p>
            </div>
          ))}
        </div>
      )}

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
        <span className="ask-synth-group">
          <label className="ask-synth-toggle">
            <input
              type="checkbox"
              checked={useSynth}
              onChange={(event) => setUseSynth(event.target.checked)}
            />
            Synthetic sandbox (sellouts &amp; resale)
          </label>
          <span className="ask-synth-caption">
            Simulated sellout &amp; resale data over real events — for questions real sources
            can&apos;t answer.
          </span>
        </span>
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

      {response && (
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

          {response.status === "ok" && (
            <AskResultsTable rows={response.rows ?? []} onOpenShow={onOpenShow} />
          )}

          {FEEDBACK_STATUSES.includes(response.status) && (
            <div className="ask-feedback">
              <button
                type="button"
                aria-label="Thumbs up"
                disabled={feedbackSent}
                onClick={() => giveFeedback("up")}
              >
                👍
              </button>
              <button
                type="button"
                aria-label="Thumbs down"
                disabled={feedbackSent}
                onClick={() => giveFeedback("down")}
              >
                👎
              </button>
              {feedbackSent ? (
                <span className="ask-feedback-note">Thanks — feedback logged.</span>
              ) : (
                <span className="ask-feedback-caption">
                  Feedback is collected to improve the agent.
                </span>
              )}
            </div>
          )}

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
