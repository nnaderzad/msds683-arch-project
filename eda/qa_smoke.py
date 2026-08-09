#!/usr/bin/env python3
"""Post-deploy QA smoke for the live event-demand service.

One deterministic, re-runnable pass over the deployed surface — the checks a
human would click through before a demo, encoded so they run identically every
time (the project's QA rule: deterministic checks over eyeballing). Exit code 0
only when every check passes; the table prints either way.

What it pins (each burned us or nearly did — see docs/REPO_STATE.md incidents):

  * service up + UI served (deploy-gap lesson: verify the running revision)
  * gold endpoints serve data incl. history_filled and non-null forecasts
  * the text-to-SQL click-path contract: a listing answer carries event_id and
    a sample of those ids resolve in /show (the "clickable rows" feature)
  * guardrails hold live: DROP blocked, off-domain refused
  * docs page + feedback endpoints answer

Run (any machine, no GCP credentials needed — it only talks to the public URL):

    python eda/qa_smoke.py                       # against the live service
    python eda/qa_smoke.py --url http://localhost:8080
    python eda/qa_smoke.py --skip-llm            # free run: no /ask calls
    python eda/qa_smoke.py --skip-write          # no feedback-row insert

Cost: the default run makes 4 /ask calls (~$0.02) and inserts one clearly
labeled feedback row. Wired into docs/demo-runbook.md T-24h checks.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://event-demand-api-mqd3drcneq-uw.a.run.app"
LISTING_QUESTION = "What are some EDM shows coming up in San Francisco?"
EASY_QUESTION = "How many events are in the warehouse?"
BLOCKED_QUESTION = "Drop the fact_event_demand table"
OFF_DOMAIN_QUESTION = "Write me a function that prints the Fibonacci sequence."


def _request(url: str, payload: dict | None = None, timeout: int = 90):
    """Return (status_code, parsed_json_or_None)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as err:
        return err.code, None
    except Exception:  # noqa: BLE001 - a dead service is a failed check, not a crash
        return 0, None


class Smoke:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.results.append((name, bool(passed), detail))

    # -- gold surface -------------------------------------------------------

    def core(self) -> None:
        status, body = _request(f"{self.base}/health")
        self.check("health", status == 200 and body == {"status": "ok"}, f"HTTP {status}")

        status, _ = _request(f"{self.base}/")
        self.check("ui_served", status == 200, f"HTTP {status}")

        status, genres = _request(f"{self.base}/genres")
        ok = status == 200 and isinstance(genres, list) and "Dance/Electronic" in genres
        self.check("genres", ok, f"{len(genres or [])} genres")

        status, rows = _request(
            f"{self.base}/search?dma=807&days_ahead=30&limit=5", timeout=120
        )
        ok = status == 200 and bool(rows) and all(r.get("event_id") for r in rows)
        self.check("search_bay_area", ok, f"{len(rows or [])} rows")
        self._sample_event = (rows or [{}])[0].get("event_id")

        if self._sample_event:
            status, show = _request(f"{self.base}/show/{self._sample_event}", timeout=120)
            keys_ok = status == 200 and show is not None and (
                {"history", "history_filled", "forecast"} <= set(show)
            )
            self.check(
                "show_detail",
                keys_ok,
                f"HTTP {status}; history={len((show or {}).get('history', []))} "
                f"filled={len((show or {}).get('history_filled', []))}",
            )
        else:
            self.check("show_detail", False, "no search row to probe")

        status, shows = _request(f"{self.base}/shows", timeout=120)
        forecasts = sum(1 for s in shows or [] if s.get("forecast_price") is not None)
        self.check(
            "forecasts_serving",
            status == 200 and forecasts > 0,
            f"{forecasts}/{len(shows or [])} shows carry a forecast",
        )

    # -- text-to-SQL surface ------------------------------------------------

    def agent(self, sample_ids: int = 5) -> None:
        status, r = _request(f"{self.base}/ask", {"question": EASY_QUESTION})
        self.check(
            "ask_easy",
            status == 200 and r is not None and r.get("status") == "ok"
            and all(g.get("passed") for g in r.get("guardrails", [])),
            f"status={r.get('status') if r else status}",
        )

        status, r = _request(f"{self.base}/ask", {"question": LISTING_QUESTION})
        rows = (r or {}).get("rows") or []
        has_ids = bool(rows) and "event_id" in rows[0]
        self.check(
            "ask_listing_has_event_id",
            status == 200 and (r or {}).get("status") == "ok" and has_ids,
            f"{len(rows)} rows; event_id column: {has_ids}",
        )

        # The click-path contract: listed ids must open in the dashboard.
        dead = []
        for row in rows[:sample_ids]:
            event_id = row.get("event_id")
            if not event_id:
                continue
            code, _ = _request(f"{self.base}/show/{event_id}", timeout=60)
            if code != 200:
                dead.append(f"{event_id}:{code}")
        self.check(
            "listing_ids_resolve",
            has_ids and not dead,
            f"{min(len(rows), sample_ids)} sampled, dead: {dead or 'none'}",
        )

        status, r = _request(f"{self.base}/ask", {"question": BLOCKED_QUESTION})
        self.check(
            "guardrail_drop",
            (r or {}).get("status") in ("blocked", "refused"),
            f"status={(r or {}).get('status')}",
        )

        status, r = _request(f"{self.base}/ask", {"question": OFF_DOMAIN_QUESTION})
        self.check(
            "guardrail_off_domain",
            (r or {}).get("status") == "refused",
            f"status={(r or {}).get('status')}",
        )

    # -- docs + feedback surface -------------------------------------------

    def docs(self) -> None:
        status, catalog = _request(f"{self.base}/repo-docs")
        self.check(
            "repo_docs_catalog",
            status == 200 and isinstance(catalog, list) and len(catalog) >= 6,
            f"{len(catalog or [])} docs",
        )
        status, doc = _request(f"{self.base}/repo-doc/data-model")
        self.check(
            "repo_doc_render",
            status == 200 and bool((doc or {}).get("markdown")),
            f"{len((doc or {}).get('markdown', ''))} chars",
        )

    def feedback(self) -> None:
        status, r = _request(
            f"{self.base}/ask_feedback",
            {"verdict": "up", "question": "qa_smoke check — ignore", "status": "ok"},
        )
        self.check(
            "feedback_write",
            status == 200 and (r or {}).get("status") == "ok",
            f"status={(r or {}).get('status')}",
        )

    # -- reporting ----------------------------------------------------------

    def report(self) -> int:
        width = max(len(name) for name, _, _ in self.results)
        failed = 0
        for name, passed, detail in self.results:
            mark = "PASS" if passed else "FAIL"
            failed += 0 if passed else 1
            print(f"  {mark}  {name.ljust(width)}  {detail}")
        total = len(self.results)
        print(f"[qa_smoke] {total - failed}/{total} checks passed against {self.base}")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--skip-llm", action="store_true", help="skip /ask checks (free run)")
    parser.add_argument("--skip-write", action="store_true", help="skip the feedback insert")
    args = parser.parse_args()

    smoke = Smoke(args.url)
    smoke.core()
    if not args.skip_llm:
        smoke.agent()
    smoke.docs()
    if not args.skip_write:
        smoke.feedback()
    sys.exit(smoke.report())


if __name__ == "__main__":
    main()
