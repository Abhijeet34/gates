# Spec: Invite teammates by email

## Goal
Accounts are single-user, so teams share one login; let an owner invite teammates by email.

## Scope
In scope: an invite form for owners, an emailed one-time link and a member list.
Out of scope: roles beyond owner and member, single sign-on and billing per seat.

## Design
- Invites are rows in a new `invites` table with a hashed token that expires after seven days and is spent on first use.
- Only the account owner can invite or remove members, and only the account's members can see the member list, both checked server side.
- Inviting an email that is already a member or has a pending invite shows that instead of sending.
- The member list shows each member's email and when they joined, 'No teammates yet' when empty, and an error with a retry when it cannot load.

## Rollout
1. Ship behind the `team_invites` flag for 20 beta accounts, then everyone after two weeks.
2. Alert when invite emails fail to send more than 5% of the time.
3. Rollback: turn off `team_invites`; members already added keep access, and owners can still remove them.
4. Remove the `team_invites` flag two weeks after it reaches everyone.

## Acceptance
- When an owner invites an email, the system shall send a one-time link. Verify: `pytest tests/teams/test_invite.py::test_link_sent`.
- When a link is used a second time or after seven days, the system shall refuse it. Verify: `pytest tests/teams/test_invite.py::test_spent_or_expired_link`.
- When a member who is not the owner invites or removes someone, the system shall answer 403. Verify: `pytest tests/teams/test_invite.py::test_member_refused`.
