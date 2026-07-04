# Ticketmaster API access request (draft)

Send from the developer-portal account that owns our API key, to
**devportalinquiry@ticketmaster.com** (48h SLA per the developer FAQ). Records
what we asked for and when; update with the outcome.

**Status: DRAFT — not yet sent** (owner: Tomas)

---

Subject: Inventory Status API access + Discovery quota request — event-demand dashboard

Hi Ticketmaster developer support,

I run a small event-demand analytics dashboard focused on live-music events
(Discovery API key under this account). The app shows users upcoming shows with
demand context — price ranges, on-sale status, and interest trends — with events
linking back to their Ticketmaster pages for purchase, following the branding
guidelines.

Two requests:

1. **Inventory Status API access.** Our users benefit most from accurate
   availability and price-range context (primary and resale). The batched
   event-ID design of the Inventory Status API fits our daily refresh much better
   than polling Discovery event-by-event, and would materially reduce our call
   volume against your infrastructure.

2. **Discovery API daily-quota increase.** We currently operate within the
   default 5,000 calls/day using a twice-daily nationwide refresh. Additional
   headroom would let us refresh high-interest markets more often during onsales
   without risking the cap.

App details: [dashboard URL — fill in the Cloud Run demo URL], data refreshed
twice daily, all event links point to ticketmaster.com, no resale/purchase
functionality of our own, no redistribution of raw data.

Happy to provide anything else you need — thanks!

[name / account email]
