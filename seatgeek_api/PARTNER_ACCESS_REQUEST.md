# SeatGeek partner / API access request — draft to send

**Why:** the basic Platform API `client_id` (app `shakshuka`) returns `stats = {}`
for all events — pricing + `listing_count` are gated to partner/affiliate access.
We want those secondary-market fields for an academic project.

**Where to send (try in this order):**
1. The contact form / "request access" on **https://platform.seatgeek.com/** (the
   developer platform site).
2. Email **partnerships@seatgeek.com** (and/or **developers@seatgeek.com** if it
   bounces).
3. SeatGeek affiliate program (via Impact/Partnerize) — affiliates sometimes get
   richer API scopes.

---

## Draft message

> **Subject:** API access request — pricing/listing stats for a university data
> project (non-commercial)
>
> Hi SeatGeek team,
>
> I'm a graduate student at the University of San Francisco (MS in Data Science).
> For a course project on **event-demand modeling**, my team is building a data
> pipeline that combines public event data with local search-interest signals to
> study how concert demand develops over time.
>
> We're already using the SeatGeek Platform API (app **shakshuka**,
> client_id on request) and it's been great for event, venue, performer, and
> popularity data. We'd like to also use the **`stats`** fields on `/events` —
> `lowest_price` / `average_price` / `listing_count` / `visible_listing_count` —
> which currently return empty for our `client_id`.
>
> Our use is **non-commercial and academic**: low request volume (a once-daily
> snapshot of a few hundred upcoming shows), no redistribution of your data, and
> full attribution to SeatGeek in our writeup. We're happy to agree to any rate
> limits or terms required for partner/affiliate access.
>
> Could you let us know how to enable the pricing/listing `stats` for our app, or
> what partner/affiliate tier we'd need? Glad to share more about the project.
>
> Thank you!
> [Name] · [USF email] · MSDS 683, University of San Francisco

---

**Notes for us:**
- Keep the ask scoped + non-commercial — that's the version most likely to be granted.
- If granted, **no code change is needed**: `seatgeek_poc.py` already captures the
  `stats` columns; they just start populating. Then we add the daily collector +
  Terraform.
- If declined / no response in ~1–2 weeks, fall back to the alternative price
  sources tracked in the team backlog.
