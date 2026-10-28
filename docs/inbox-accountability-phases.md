# Inbox Accountability — Phases

Grounded in current code: `category == "request"` already proxies "needs a
reply" (app/services/daily_brief.py:87). What's missing is knowing whether
the user *actually* replied, and how old the open item is — everything else
builds on top of those two facts.

## Phase 1 — Reply detection (read-only)

Add a method to GmailReadOnlyClient (app/email/gmail_client.py) that reads a
thread by `external_thread_id` (already stored, models.py:103) and reports
whether any message in it is `From: me` with a timestamp after the inbound
message being analyzed. Stays read-only — no new scopes, no send/modify.

Output: a boolean/timestamp the next phase can use. No UI changes yet.

## Phase 2 — Open-item tracking + nag level (backend)

Add a computed "open item" view: for every EmailAnalysis where
category == "request" and not replied (Phase 1) and review_status isn't
completed/dismissed, compute age = now - received_at, and derive nag_level
(quiet / noticeable / persistent) from one configurable threshold pair.

Extend GET /api/v1/daily-brief (or add /api/v1/open-items) to return this.
No new table needed yet — it's a query over existing EmailAnalysis +
ProcessedMessage + EmailReview, same pattern as build_daily_brief.

## Phase 3 — Nag ladder in the companion widget

Wire the computed nag_level into the existing orb (desktop-companion/src/App.tsx):
- quiet: current behavior (badge count, auto-hide after 30s)
- noticeable: amber ring, one native notification naming sender + age
- persistent: red ring, auto-hide suppressed until dismissed or replied

Mostly reuses what's already built (badge, color states, notification plugin)
— the new part is nag_level driving which behavior applies, instead of just
actionCount > 0.

## Phase 4 — Per-business configurable thresholds

Expose the nag thresholds (hours for noticeable/persistent) as a setting —
either in app/config.py (env-based, matches existing settings pattern) or a
lightweight per-mailbox config if multiple inboxes become real. Default to
something sane (e.g. 4h / 24h) so it works out of the box.

---
Not in scope for any phase above: owner routing, manager escalation,
auto-replying — all deliberately cut when we scoped this for a 1-2 person
business instead of a team.
