# Future Development Recommendations

This document records possible enhancements for future Sun Sync developers.
These recommendations are not part of the current Wi-Fi vendo and charging
system scope and do not imply that the features are implemented.

## Administrator and operator account management

### Current scope and behavior

The dashboard is an administrative interface for operating the vendo and
charging system. The current authentication flow supports administrator login
and password changes. The database can store multiple administrator records,
but there is no dashboard workflow for creating, editing, disabling, or
assigning roles to accounts.

For the current project scope, keep account management intentionally simple:
use a trusted administrator account and do not add a dashboard tab solely to
manage other operators. Do not share administrator credentials when separate
operators need individual accountability; treat that as a future feature
requirement instead.

### When to consider this feature

Revisit account management if the system will be operated by multiple people,
if different operators need different permissions, or if the owner needs to
review which operator performed a sensitive action. Do not add it only to
increase the number of dashboard tabs.

### Recommended future design

If the feature is approved, provide a dedicated **Administration** area for
authorized administrators with:

- **Named accounts:** each person uses an individual account; never create
  shared operator logins.
- **Least-privilege roles:** begin with a small set such as owner/admin and
  operator. Define permissions for viewing status, changing system settings,
  managing sessions, recording transactions, and creating/downloading/
  restoring backups. Keep account management and database recovery restricted
  to the owner/admin role.
- **Safe account lifecycle:** allow an authorized administrator to invite or
  create an account, resend an invite, disable an account, and revoke active
  sessions. Avoid exposing password hashes or existing passwords in the UI or
  API. Use a secure password-setup/reset flow rather than assigning a shared
  initial password.
- **Account protections:** require unique strong passwords, rate-limit login
  and recovery attempts, and consider multi-factor authentication if the
  dashboard becomes reachable beyond a trusted local network.
- **Last-owner recovery:** prevent disabling or deleting the final owner
  account without a tested recovery path. Do not make the system permanently
  dependent on one person's availability.
- **Audit trail:** record the actor, action, target account/resource, time, and
  outcome for account changes and other sensitive operations such as settings
  changes and database restores. Never log passwords, tokens, or secret values.
- **Re-authentication for high-impact changes:** require confirmation of the
  current password or a fresh authentication step before privilege changes,
  disabling an owner, changing security settings, or restoring the database.

### Implementation and validation checklist

Before implementation:

1. Confirm the expected number of operators and who may administer accounts.
2. Write a permission matrix for each dashboard/API action.
3. Decide whether multiple roles are actually needed; avoid speculative
   permissions or a general-purpose authorization framework without a
   requirement.
4. Define account invitation, password recovery, owner lockout recovery, and
   session-revocation behavior.

Before release:

1. Enforce authorization on backend routes; hiding dashboard controls is not
   access control.
2. Test that operators cannot call owner-only routes directly.
3. Test disabled-account login rejection and revocation of already-issued
   tokens/sessions.
4. Test that an owner cannot accidentally lock out the system's last owner.
5. Verify audit records identify the operator and do not contain credentials.
6. Test recovery from a lost or compromised administrator account.

## Related references

- Current authentication flow and administrator data model: see
  [SYSTEM_DOCUMENTATION.md](./SYSTEM_DOCUMENTATION.md), sections 4 and 7.
- Current local setup and security configuration: see [README.md](./README.md).
