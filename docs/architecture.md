# Architecture

shallow-triage has three layers. Each change should touch one layer and
keep what the other layers have stored, so earlier input and data keep
working after an update.

```text
input                         data                              output
-----                         ----                              ------
config (TOML)  ─┐
GitHub (gh)    ─┤
local git      ─┼─ collect ──> reports/<owner>/<name>.json ─ render ──> HTML site
findings       ─┤                                                 └──> JSON (candidates)
safe marks     ─┘
        ▲
        └── cache/<owner>/<name>.json  (issues fetched so far)
```

Findings and safe marks are the user's own records (from `bisect`,
`closable add` and `safe mark`), so they are inputs, kept apart from fetched
data: rebuilding the cache or the reports never loses them.

- `collect` reads the inputs and saves one report per repository.
- `render` reads saved reports only. It never calls GitHub or git, so
  changing a page means running `render` again, not fetching again.
- `site` runs both.

## Modules

| Layer | Module | Role |
| --- | --- | --- |
| Input | `config.py` | Parse the TOML config into `RepoConfig` |
| Input | `gh.py`, `issues.py` | Fetch issues through `gh`; score them |
| Input | `local.py` | Read fix branches and fixing commits with `git` |
| Input | `cache.py` | Keep fetched issues between runs |
| Input | `findings.py` | Verified closable findings recorded by the user or `bisect` |
| Input | `marks.py` | Issues the user marked safe, and code taken from them |
| Work | `workflow.py` | `fix`, `test` and `bisect` in a repository's workdir |
| Work | `runenv.py` | Run configured commands in the venv, without credentials |
| Data | `report.py` | `RepoReport` model; `build_report`, `save_report`, `load_report` |
| Data | `serialize.py` | Dataclasses to and from JSON, tolerant of version differences |
| Data | `store.py` | Owner-only atomic JSON files, default folders |
| Output | `page.py`, `templates/` | HTML pages from reports |
| Output | `jsonout.py` | JSON list of candidates from a report |
| Glue | `site.py`, `cli.py` | Run collect and render for several repositories |

Rules for the boundaries:

- **Outputs only format.** `page.py` and `jsonout.py` take a `RepoReport`
  and compute nothing: scores, ranking and the base are decided when the
  report is built. They never read the config, the cache, GitHub or git.
- **A report is self-contained.** It holds what the outputs need from the
  config (`base`, `test`), never the clone's location, so it can be shared.
- **Inputs don't know about outputs.** Nothing under input imports from
  `page.py` or the templates.

## Stored formats and how to change them

| File | Default location | Version constant |
| --- | --- | --- |
| Config | `~/.config/shallow-triage/triage.toml` | none: keys are only added |
| Findings | `~/.local/share/shallow-triage/findings/` | `findings.FINDINGS_VERSION` |
| Safe marks | `~/.local/share/shallow-triage/safe/` | `marks.MARKS_VERSION` |
| Issue cache | `~/.cache/shallow-triage/` | `cache.CACHE_VERSION`, `issues.FIELDS_VERSION` |
| Reports | `~/.local/share/shallow-triage/reports/` | `report.REPORT_VERSION` |

### Config

Add keys with a default in `RepoConfig`; never rename or remove a key. An
unknown key is an error, which catches typos, so a config written for a
newer version needs that version.

### Findings and safe marks

These hold the user's decisions, so they are never dropped silently: a file
that can't be read is an error, not a reset. Add fields with defaults, as
for reports. A safe mark is a hash of the issue's title and body; changing
what the hash covers invalidates every mark, so don't.

### Issue cache

- **Fetching another issue field:** change `_FIELDS` in `issues.py` and bump
  `FIELDS_VERSION`. Cached issues keep being used, with defaults for the new
  field in `Issue.from_graphql`, and are refreshed `--batch` at a time. The
  walk position and known-closed numbers are kept.
- **Changing the cache file's layout:** bump `CACHE_VERSION`. Old files are
  ignored and rebuilt, so avoid this.

### Reports

`serialize.from_data` fills a field missing from a file with its default,
and ignores keys it doesn't know. So:

- **Adding a field** to `RepoReport` or anything it contains: give it a
  default. Old reports still load; new reports still load in old versions.
- **Renaming, removing or changing a field's type:** don't. Add a new field
  and stop filling the old one instead. If that's impossible, bump
  `REPORT_VERSION`; old reports then fail to load with a clear error and
  must be collected again.
- **Changing how something is computed** (for example the score): change
  `issues.py` or `report.py`. Reports saved earlier keep their old values
  until the next `collect`.

### Outputs

Templates, styles and `page.py` can change freely; run `render` to apply
the change to saved reports. The JSON keys of `candidates` are a documented
interface (see `docs/triage-agent.md`): add keys, don't rename or remove
them.
