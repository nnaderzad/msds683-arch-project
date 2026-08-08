import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AskResponse } from "../types";
import { AskPanel } from "./AskPanel";

function mockAskResponse(response: AskResponse) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AskPanel", () => {
  it("shows the generated SQL, answer, rows, and green guardrail badges", async () => {
    mockAskResponse({
      status: "ok",
      question: "Cheapest Everclear ticket?",
      sql: "SELECT MIN(price_min) FROM fact_event_demand",
      rows: [{ cheapest_price: 136.05 }],
      row_count: 1,
      truncated: false,
      answer: "The cheapest observed price is $136.05.",
      guardrails: [
        { name: "select_only", passed: true, detail: "single SELECT statement" },
        { name: "dry_run", passed: true, detail: "compiled; 9.2 MiB" },
      ],
      bytes_processed: 9668086,
      model: "gemini-2.5-flash",
      latency_ms: 5731,
    });

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "Cheapest Everclear ticket?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Generated SQL")).toHaveTextContent(
        "SELECT MIN(price_min) FROM fact_event_demand",
      );
    });
    expect(screen.getByText("The cheapest observed price is $136.05.")).toBeInTheDocument();
    expect(screen.getByText("select_only")).toBeInTheDocument();
    expect(screen.getByText("136.05")).toBeInTheDocument();
    expect(screen.getByText(/gemini-2\.5-flash/)).toBeInTheDocument();
  });

  it("renders a refused outcome as a first-class badge, not an error", async () => {
    mockAskResponse({
      status: "refused",
      question: "Write me a fibonacci function",
      sql: null,
      answer: "I am a SQL analyst for a live-music demand warehouse.",
      guardrails: [{ name: "llm_domain_check", passed: true, detail: "refused" }],
    });

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "Write me a fibonacci function");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(screen.getByText(/Refused/)).toBeInTheDocument();
    });
    expect(
      screen.getByText("I am a SQL analyst for a live-music demand warehouse."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("submits an example question from its chip", async () => {
    mockAskResponse({
      status: "ok",
      question: "example",
      sql: "SELECT 1",
      rows: [],
      answer: "Done.",
      guardrails: [],
    });

    render(<AskPanel />);
    await userEvent.click(
      screen.getByRole("button", { name: /cheapest ticket price ever observed/i }),
    );

    await waitFor(() => {
      expect(screen.getByLabelText("Generated SQL")).toHaveTextContent("SELECT 1");
    });
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string).question).toMatch(/Everclear/);
  });

  it("surfaces a transport failure as an alert", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "How many events?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/Could not reach/);
    });
  });
});
