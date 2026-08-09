import { useEffect, useRef, useState } from "react";
import { fetchShow } from "../api/client";
import type { ShowDetail } from "../types";
import { formatDate, formatNumber, formatPrice } from "../utils/formatters";

// How long a row must stay hovered/focused before the stats card appears.
const HOVER_DELAY_MS = 300;

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

// The agent's schema context nudges event listings to include event_id, but its
// SQL is free-form — detect the column defensively and prefer an exact match.
export function findEventIdColumn(columns: string[]): string | null {
  const exact = columns.find((column) => column.toLowerCase() === "event_id");
  if (exact) {
    return exact;
  }
  return columns.find((column) => column.toLowerCase().endsWith("_event_id")) ?? null;
}

// A row links to a show only when its id value looks like a real Ticketmaster id:
// a non-empty string, no whitespace, at least 8 characters.
export function linkableEventId(
  row: Record<string, unknown>,
  column: string | null,
): string | null {
  if (!column) {
    return null;
  }
  const value = row[column];
  if (typeof value !== "string" || value.length < 8 || /\s/.test(value)) {
    return null;
  }
  return value;
}

function formatPriceRange(min: number | null, max: number | null): string {
  if (min == null && max == null) {
    return "—";
  }
  if (min == null || max == null || min === max) {
    return formatPrice(min ?? max);
  }
  return `${formatPrice(min)}–${formatPrice(max)}`;
}

type HoverCard = {
  eventId: string;
  top: number;
  left: number;
};

type AskResultsTableProps = {
  rows: Record<string, unknown>[];
  // When absent, every row renders plain (no link affordance, no hover card).
  onOpenShow?: (eventId: string) => void;
};

export function AskResultsTable({ rows, onOpenShow }: AskResultsTableProps) {
  const [hoverCard, setHoverCard] = useState<HoverCard | null>(null);
  // Per-session cache: null records a failed fetch (e.g. 404) so the card renders
  // nothing for that id and we never refetch it. A ref keeps it across hovers;
  // cacheVersion re-renders when a fetch lands.
  const cacheRef = useRef(new Map<string, ShowDetail | null>());
  const [, setCacheVersion] = useState(0);
  const timerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);

  const hideCard = () => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setHoverCard(null);
  };

  // Any scroll dismisses the card (its absolute position would drift otherwise).
  useEffect(() => {
    if (!hoverCard) {
      return;
    }
    const hide = () => setHoverCard(null);
    window.addEventListener("scroll", hide, true);
    return () => window.removeEventListener("scroll", hide, true);
  }, [hoverCard]);

  useEffect(
    () => () => {
      if (timerRef.current != null) {
        window.clearTimeout(timerRef.current);
      }
      abortRef.current?.abort();
    },
    [],
  );

  const showCardFor = (eventId: string, rowEl: HTMLElement) => {
    const shellRect = shellRef.current?.getBoundingClientRect();
    const rowRect = rowEl.getBoundingClientRect();
    setHoverCard({
      eventId,
      top: shellRect ? rowRect.bottom - shellRect.top : 0,
      left: shellRect ? Math.max(0, rowRect.left - shellRect.left + 24) : 0,
    });

    if (cacheRef.current.has(eventId)) {
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    fetchShow(eventId, controller.signal)
      .then((detail) => {
        cacheRef.current.set(eventId, detail);
        setCacheVersion((version) => version + 1);
      })
      .catch((error: unknown) => {
        if (isAbortError(error) || controller.signal.aborted) {
          return;
        }
        // 404s and other failures show no card and are not retried this session.
        cacheRef.current.set(eventId, null);
        setCacheVersion((version) => version + 1);
      });
  };

  const scheduleCard = (eventId: string, rowEl: HTMLElement) => {
    hideCard();
    timerRef.current = window.setTimeout(() => showCardFor(eventId, rowEl), HOVER_DELAY_MS);
  };

  if (rows.length === 0) {
    return null;
  }
  const columns = Object.keys(rows[0]);
  const visible = rows.slice(0, 20);
  const eventIdColumn = onOpenShow ? findEventIdColumn(columns) : null;
  const anyLinkable =
    eventIdColumn != null && visible.some((row) => linkableEventId(row, eventIdColumn) != null);
  const cardShow = hoverCard ? cacheRef.current.get(hoverCard.eventId) : undefined;

  return (
    <div className="ask-table-shell" ref={shellRef}>
      <div className="ask-table-wrap">
        <table className="ask-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
              {anyLinkable && <th aria-hidden="true" />}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => {
              const eventId = onOpenShow ? linkableEventId(row, eventIdColumn) : null;
              const cells = columns.map((column) => (
                <td key={column}>{row[column] == null ? "—" : String(row[column])}</td>
              ));

              if (eventId == null || !onOpenShow) {
                return (
                  <tr key={index}>
                    {cells}
                    {anyLinkable && <td />}
                  </tr>
                );
              }

              return (
                // Same pattern as the search results: the row itself is the button.
                <tr
                  key={index}
                  className="ask-row-linkable"
                  role="button"
                  tabIndex={0}
                  aria-label={`View show ${eventId}`}
                  onClick={() => onOpenShow(eventId)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenShow(eventId);
                    }
                  }}
                  onMouseEnter={(event) => scheduleCard(eventId, event.currentTarget)}
                  onMouseLeave={hideCard}
                  onFocus={(event) => scheduleCard(eventId, event.currentTarget)}
                  onBlur={hideCard}
                >
                  {cells}
                  <td className="ask-view-hint">view →</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length > visible.length && (
          <p className="ask-table-note">
            Showing first {visible.length} of {rows.length} rows.
          </p>
        )}
      </div>

      {/* Linkability is not self-evident (older answers and aggregates have no
          event_id) — say out loud whether these rows click through. */}
      {onOpenShow && anyLinkable && (
        <p className="ask-table-note">Click a row to open that show in the dashboard.</p>
      )}
      {onOpenShow && !anyLinkable && (
        <p className="ask-table-note">
          These results aren&apos;t linked to shows — ask for a list of shows to get clickable
          rows.
        </p>
      )}

      {hoverCard && cardShow && (
        <div
          className="ask-show-card"
          role="status"
          style={{ top: hoverCard.top, left: hoverCard.left }}
        >
          <strong>
            {cardShow.artist_name ? `${cardShow.artist_name} — ` : ""}
            {cardShow.event_name}
          </strong>
          <p>
            {cardShow.venue_name} · {cardShow.city}, {cardShow.state_code}
          </p>
          <p>{formatDate(cardShow.show_date)}</p>
          <dl>
            <dt>Observed price</dt>
            <dd>{formatPriceRange(cardShow.price_min, cardShow.price_max)}</dd>
            <dt>Forecast</dt>
            <dd>{formatPrice(cardShow.forecast_price)}</dd>
            <dt>Local interest</dt>
            <dd>{formatNumber(cardShow.local_interest)}</dd>
            <dt>YouTube subscribers</dt>
            <dd>{formatNumber(cardShow.yt_subscribers)}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
