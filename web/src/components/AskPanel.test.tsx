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
  it("is titled Ask the music warehouse", () => {
    render(<AskPanel />);
    expect(screen.getByRole("heading", { name: "Ask the music warehouse" })).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Ask the music warehouse" }),
    ).toBeInTheDocument();
  });

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

  it("posts thumbs feedback with the answer context, then locks both buttons", async () => {
    const askResponse: AskResponse = {
      status: "ok",
      question: "Cheapest Everclear ticket?",
      dataset: "real",
      sql: "SELECT MIN(price_min) FROM fact_event_demand",
      rows: [{ cheapest_price: 136.05 }],
      answer: "The cheapest observed price is $136.05.",
      guardrails: [],
      bytes_processed: 9668086,
      model: "gemini-2.5-flash",
      latency_ms: 5731,
    };
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/ask_feedback") ? { status: "ok" } : askResponse;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "Cheapest Everclear ticket?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await userEvent.click(await screen.findByRole("button", { name: "Thumbs up" }));

    await waitFor(() => {
      expect(screen.getByText("Thanks — feedback logged.")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Thumbs up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Thumbs down" })).toBeDisabled();

    const feedbackCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("/ask_feedback"),
    );
    expect(feedbackCall).toBeDefined();
    expect(JSON.parse((feedbackCall![1] as RequestInit).body as string)).toEqual({
      verdict: "up",
      question: "Cheapest Everclear ticket?",
      sql: "SELECT MIN(price_min) FROM fact_event_demand",
      answer: "The cheapest observed price is $136.05.",
      dataset: "real",
      model: "gemini-2.5-flash",
      latency_ms: 5731,
      bytes_processed: 9668086,
      status: "ok",
    });
  });

  it("shows the collection caption and keeps the thanks state on a failed POST", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/ask_feedback")) {
        return Promise.reject(new Error("network down"));
      }
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            status: "refused",
            question: "Write me a fibonacci function",
            sql: null,
            answer: "I am a SQL analyst for a live-music demand warehouse.",
            guardrails: [],
          }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "Write me a fibonacci function");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(
      await screen.findByText("Feedback is collected to improve the agent."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Thumbs down" }));

    // Fire-and-forget: even though the POST failed, the user still sees thanks.
    await waitFor(() => {
      expect(screen.getByText("Thanks — feedback logged.")).toBeInTheDocument();
    });
  });

  it("threads prior exchanges and sends the last completed ones as history", async () => {
    const answered = (question: string, answer: string): AskResponse => ({
      status: "ok",
      question,
      sql: "SELECT 1",
      rows: [],
      answer,
      guardrails: [],
    });
    let call = 0;
    const responses = [
      answered("How many events?", "There are 40,000 events."),
      answered("How many venues?", "There are 900 venues."),
    ];
    const fetchMock = vi.fn().mockImplementation(() => {
      const payload = responses[call];
      call += 1;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "How many events?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("There are 40,000 events.");

    // The first request carries no history.
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      question: "How many events?",
      dataset: "real",
    });

    await userEvent.clear(screen.getByLabelText("Question"));
    await userEvent.type(screen.getByLabelText("Question"), "How many venues?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("There are 900 venues.");

    // The follow-up includes the completed exchange as history, older first.
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toEqual({
      question: "How many venues?",
      dataset: "real",
      history: [{ question: "How many events?", answer: "There are 40,000 events." }],
    });

    // The earlier turn collapses into the compact thread; the latest keeps detail.
    const thread = screen.getByLabelText("Earlier exchanges");
    expect(thread).toHaveTextContent("How many events?");
    expect(thread).toHaveTextContent("There are 40,000 events.");

    await userEvent.click(screen.getByRole("button", { name: "Clear conversation" }));
    expect(screen.queryByLabelText("Earlier exchanges")).not.toBeInTheDocument();
    expect(screen.queryByText("There are 900 venues.")).not.toBeInTheDocument();
  });

  it("skips refused turns when building follow-up history", async () => {
    const responses: AskResponse[] = [
      {
        status: "refused",
        question: "Write me a fibonacci function",
        sql: null,
        answer: "I am a SQL analyst for a live-music demand warehouse.",
        guardrails: [],
      },
      {
        status: "ok",
        question: "How many events?",
        sql: "SELECT 1",
        rows: [],
        answer: "There are 40,000 events.",
        guardrails: [],
      },
    ];
    let call = 0;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input).includes("/ask_feedback")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok" }) });
      }
      const payload = responses[call];
      call += 1;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AskPanel />);
    await userEvent.type(screen.getByLabelText("Question"), "Write me a fibonacci function");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("I am a SQL analyst for a live-music demand warehouse.");

    await userEvent.clear(screen.getByLabelText("Question"));
    await userEvent.type(screen.getByLabelText("Question"), "How many events?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("There are 40,000 events.");

    // Refused turns never become history.
    expect(JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)).toEqual({
      question: "How many events?",
      dataset: "real",
    });
  });

  it("makes agent rows carrying an event_id clickable through to the dashboard", async () => {
    mockAskResponse({
      status: "ok",
      question: "Which shows?",
      sql: "SELECT event_id, event_name FROM fact_event_demand",
      rows: [{ event_id: "rZ7HnEZ1Af00jd", event_name: "Everclear with American Hi-Fi" }],
      answer: "One show.",
      guardrails: [],
    });
    const onOpenShow = vi.fn();

    render(<AskPanel onOpenShow={onOpenShow} />);
    await userEvent.type(screen.getByLabelText("Question"), "Which shows?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await userEvent.click(
      await screen.findByRole("button", { name: "View show rZ7HnEZ1Af00jd" }),
    );
    expect(onOpenShow).toHaveBeenCalledWith("rZ7HnEZ1Af00jd");
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
