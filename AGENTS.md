## Branch layout

Project work (domain docs, plan, source) lives on `flow`; `main` stays a bare skeleton. Task branches base on `flow`, PRs target `flow` (`gh pr create --base flow`). `main` is off-limits: no commits, no pushes, no PRs against it.

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues, operated through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md`.
