# FrontDesk Chorus — Session Retrospective 2026-05-13

Four dashboard tweaks added to the Front Desk app — stale key flags, activity logs, amenity close-out tracking, and a passdown pre-generation checklist — plus passdown improvements and a Parking section removal.

---

## What We Set Out to Do

- Add four operational tweaks to improve shift handoff quality (stale keys, running logs, close-out flags, passdown gating)
- Push changes to GitHub and merge into main
- Review what was missing from the passdown generator and fix gaps

---

## What Worked Well

**Defining all four tweaks upfront before writing a line of code** — having the full spec in one message meant no back-and-forth mid-build. Each tweak was well-defined enough to implement directly without clarifying questions.

**Targeted edits to a single large HTML file** — rather than rewriting large sections blindly, reading the exact functions first and then replacing them precisely avoided collateral damage in a 400KB+ file.

**Backwards compatibility on log arrays** — the activity log for Maintenance and Concierge was built to fall back to the old `desc`/`notes` field when no `log[]` array exists. Existing data shows up correctly without a migration.

**Pre-flight checklist applying notes back to source records** — when a user saves a note or skips an item in the passdown checklist, the note is written back to the actual key/maintenance/concierge/amenity record, not just stored as passdown metadata. This keeps the main sections and passdown in sync.

---

## What Didn't Work (and Why)

### Direct push to main was blocked
**What we tried:** After the second commit, we pushed directly to `main`.

**Why it failed:** The repo has branch protection on `main` — direct pushes return a 403. Only merges via PR are allowed.

**Lesson:** Always push to a feature branch. The pattern that works here is `claude/<description>-<hash>` → PR → merge on GitHub.

---

## Bugs Encountered

| Bug | Root Cause | Resolution |
|-----|-----------|------------|
| Duplicate message sent by user | UI glitch on Claude Code web | No code impact — cosmetic only |
| Push to `main` returned 403 | Branch protection rules on the repo | Pushed to feature branch `claude/passdown-maintenance-parking-f3Y9C`, user merged via PR |

---

## Where We Got Stuck (and Why)

**Shift ID logic for stale key detection** — computing a stable, comparable "shift ID" (e.g. `2026-05-13-PM`) from an arbitrary timestamp required handling the overnight edge case where hours < 7 belong to the previous calendar day's shift label. This was subtle but resolved cleanly by adjusting the date string for overnight entries.

---

## What Stayed the Same (and Why)

- **Guest & Visitor section** — user confirmed they'll likely remove this entirely since RiseIO already covers it. No changes made so data isn't lost yet.
- **Urgent/overdue to-dos in passdown** — left out of the passdown generator for now; not enough signal that this is needed, and to-dos are already visible in their own section.
- **Concierge as the home for parking notes** — the free-form Parking section in the passdown builder was removed precisely because Concierge Log already handles this naturally. No new home needed.

---

## What We'd Do Differently

1. **Check branch push permissions at session start** — would have saved the failed push attempt by going straight to a feature branch.
2. **Ask about RiseIO integrations earlier** — knowing that Guest & Visitor is a duplicate of RiseIO functionality would have informed the passdown checklist design (we flagged guests in the pre-flight check for a section that may be removed).
3. **Preview earlier in the workflow** — the user couldn't preview via localhost since they're on Claude Code web. Next time, mention the GitHub raw → htmlpreview.github.io approach upfront rather than after the server fails.

---

## Next Steps

- [ ] Merge `claude/passdown-maintenance-parking-f3Y9C` into main (passdown maintenance + parking removal)
- [ ] Decide whether to remove the Guest & Visitor section or keep it dormant
- [ ] Review whether Lost & Found items should surface in the passdown generator
- [ ] Consider adding maintenance to the passdown pre-flight checklist's auto-note (currently only flags missing log entries, doesn't push note back to pdNotes)

---

## Related

- [[FrontDesk Chorus — Project Overview]]
- [[Passdown Generator Logic]]
- [[Shift Handoff Standards]]
