# Reporting 1.9.6: setting validation

The owner operating route validates settings before proposing, storing or
displaying them. Refusals identify the field without echoing its value.
The operating configuration and historical observation schemas share these
allowlist shapes; project/repository/scope binding checks still apply.

| Field | Permitted shape |
| --- | --- |
| mode | off, teams, slack or both |
| bindings | At most two exact binding objects; one per platform, one common scope; every selected platform required |
| project | Lowercase UUID or slug |
| repository | Positive decimal repository ID (1–20 digits) or adapter slug; owner settings must exactly match reviewed repository identity |
| scope_revision | Lowercase 64-character SHA-256 or slug |
| platform | teams or slack |
| destination | Slack C/G channel ID, lowercase UUID team plus Teams 19: channel/thread pair, or adapter slug |
| revision | 1–10 decimal digits, optionally prefixed binding- |
| secret_ref | env:NAME, vault:PATH or secret:PATH; never a literal credential |

A slug has one to four lowercase alphanumeric segments separated by hyphens;
the first starts with a letter, and each has at most 16 characters. Environment
names start with an ASCII letter or underscore and contain only ASCII letters,
digits and underscores, at most 128 characters. Vault/secret paths have one to
four slash-separated segments, each starting with an ASCII letter and containing
only ASCII letters, digits, underscores and hyphens, at most 32 characters each.
All binding strings retain the 256-character ceiling. Actual transport admission
also requires the platform's channel shape; adapter aliases do not enable sending.

Budget fields remain exact booleans or bounded positive integers; enforcement
requires accounting. Calendar fields remain supported IANA timezones, continuous
work mode, exact booleans, unique ISO weekdays 1–7 and strict HH:MM times, with
opening before cutoff. Malformed values cannot reach the shared proposal/audit.

Unsafe retained proposals refuse confirmation before display or authentication.
Unsafe retained confirmation priors refuse new proposals. Existing store bytes
are preserved; this introduces no migration.

Reporting-owned delivery exhaustion is terminal for the exact candidate across
windows, calendar edits and restart. Ordinary review-only exhaustion retains
calendar-following recovery. A changed candidate can qualify independently.
