# Project guardrails for Claude

## Privacy

Never include real personal data in code, documentation, or commit messages. Use placeholders instead.

| Data type | Use instead |
|---|---|
| Personally Identifying Information | Dummy data |
| GPS coordinates | `<your latitude>` / `<your longitude>` |
| Home IP addresses | `192.168.x.x` |
| Email addresses | `you@example.com` |
| Device IDs / local keys | `<device_id>` / `<local_key>` |
| Passwords / tokens | `<your_token>` |

`.env` will be gitignored — sensitive values belong there, not in source files or docs.

## Tests

Never hardcode an absolute date/datetime (e.g. `datetime(2026, 9, 14, 13, 45, 19)`) as a cutoff, anchor, or comparison point in a test — including when copying a real date from an incident note or commit for realism. A fixed date checked against a rolling window (e.g. `hours=168`) passes only until "now" moves far enough past it, then fails for real, not flakily. Anchor time-window tests to `datetime.utcnow()` (or equivalent) plus/minus a `timedelta` instead, so they can't go stale.
