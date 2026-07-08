# Resident Advisor data-access request (draft)

RA's terms prohibit automated access without written authorization, so we ask
first. Their GraphQL endpoint is technically open, but we don't build on it
without a yes (docs/collection_efficiency_review.md, finding 16). Contact:
RA has no public API program — use the general contact form (ra.co/contact)
and/or press@ra.co, mentioning the SF office.

**Status: GRANTED (2026-07-04)** — Tomas emailed; RA approved **a single
automated request per day**. That limit is enforced in code by
`ra_api/collect_ra.py` (bronze-partition guard); never raise the frequency
without asking RA again.

---

Subject: Academic request — low-volume automated access to Bay Area event listings

Hi Resident Advisor team,

I'm a graduate student at the University of San Francisco (MS in Data Science)
building a non-commercial class project: a demand-forecasting model for Bay Area
electronic-music events, combining public event listings with search-interest
trends.

RA has by far the best coverage of the Bay Area club scene, and I'd like to ask
for written permission for a **single automated request per day** retrieving
upcoming San Francisco Bay Area event listings (event name, date, venue, lineup,
and attending count) for the duration of the semester. The data would be used
only inside the project, never redistributed or published, and every event
reference in our demo links back to its ra.co page.

I'm aware your Terms of Use prohibit automated access without authorization,
which is why I'm asking rather than scraping. Happy to sign anything you need,
share the project write-up, or adjust scope/volume to whatever you're
comfortable with.

Thank you for considering it!

[name, USF program, contact]
