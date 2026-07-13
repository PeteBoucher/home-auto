# Project guidelines for Claude

@.claude/guardrails/dos.md
@.claude/guardrails/donts.md
@.claude/memory/project.md

## General

- Run `pytest` before committing.
- **Deployment is automatic** — push to `main` triggers the GitHub Actions pipeline (self-hosted runner on the Pi). Tests must pass before the service restarts.
- The Pi is at `homeauto.local`, user `pete`. For emergency manual deploy: `ssh pete@homeauto.local "cd /opt/home-auto && git fetch origin main && git reset --hard origin/main && sudo systemctl restart home-auto"`
