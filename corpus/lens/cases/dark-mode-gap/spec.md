# Spec: Add a dark theme to the web app

## Goal
Users working at night ask for a dark theme; add one that follows the system setting with a manual override.

## Scope
In scope: dark values for the existing colour tokens, a theme switch in settings and the `prefers-color-scheme` default.

## Design
- Every colour already comes from the token set, so dark mode is a second value per token, not per component.
- The switch is a radio group labelled 'Theme' offering System, Light and Dark, stored in the existing `user_settings` table; users with no setting get System.
- Each dark token pair keeps text contrast at 4.5:1 or more.

## Rollout
1. Ship to staff, then everyone after a week with no contrast reports.
2. Track the share of users choosing Dark on the settings dashboard.
3. Rollback: hide the switch and force Light by the `theme_switch` flag.
4. Remove the `theme_switch` flag two weeks after it reaches everyone.

## Acceptance
- When the system setting is dark and the user chose System, the system shall render with dark tokens. Verify: `npx playwright test e2e/theme.spec.ts --grep system-dark`.
- When a user chooses Light while the system is dark, the system shall render with light tokens and keep that choice. Verify: `npx playwright test e2e/theme.spec.ts --grep override`.
- When a dark token pair renders text, the system shall keep contrast at 4.5:1. Verify: `npm run check:contrast -- --theme dark` exits 0.
