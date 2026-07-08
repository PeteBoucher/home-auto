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