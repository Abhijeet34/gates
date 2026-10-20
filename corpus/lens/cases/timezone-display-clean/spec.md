# Spec: Show event times in the viewer's time zone

## Goal
Event times show in UTC and attendees miss sessions; show them in the viewer's own time zone.

## Scope
In scope: the event page's time rendering and a time-zone label beside each time.
Out of scope: changing how times are stored and the calendar export.

## Design
- Times stay stored in UTC; the page converts with the browser's `Intl.DateTimeFormat` in the viewer's resolved zone.
- Each time shows the zone's short name from `Intl.DateTimeFormat` with `timeZoneName: 'short'`, such as 'CET', so a screenshot stays unambiguous.
- If the zone cannot be resolved, times show in UTC labelled 'UTC'.

## Rollout
1. Ship to everyone; the change is display only.
2. Alert when the page's client errors rise above their weekly average.
3. Rollback: revert the deploy.

## Acceptance
- When a viewer in Berlin opens an event at 12:00 UTC on 15 January, the system shall show 13:00 CET. Verify: `npx playwright test e2e/event-time.spec.ts --grep berlin-winter`.
- When a viewer in Berlin opens an event at 12:00 UTC on 15 July, the system shall show 14:00 CEST. Verify: `npx playwright test e2e/event-time.spec.ts --grep berlin-summer`.
- When the zone cannot be resolved, the system shall show the UTC time labelled UTC. Verify: `npx playwright test e2e/event-time.spec.ts --grep unresolved`.
