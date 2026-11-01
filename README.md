# shallow-triage

A more efficient way to manage backlog

Builds triage pages for GitHub repositories. Each repository page lists:

- **Ready to review**: local fix branches for issues that are still open, with
  the commit message as the suggested fix, the changed files, the diff, and
  commands to view the diff and run the tests.
- **Possibly closable**: open issues that a commit on the base branch or a
  merged PR says it fixes, with links to that evidence.
- **Candidates**: open issues without a fix, ranked by how well-scoped they
  look, with the reasons behind each score.

An overview page links to every repository page, and each page starts with a
table of contents.

The intended split is that a coding agent does the triage work (picks issues,
reproduces them, writes fixes on local branches) and a human reviews the
result on these pages and decides what to submit. See
[Working with an agent](#working-with-an-agent).

## Install

Requires Python 3.13+, git, and the [GitHub CLI](https://cli.github.com/).

```sh
uv tool install git+https://github.com/tqa236/shallow-triage
# or: pip install git+https://github.com/tqa236/shallow-triage
gh auth login
```

All GitHub access goes through `gh`, which handles authentication. The package
never reads, stores or prints a token.

## Quick start

Rank the open issues of one repository:

```sh
shallow-triage candidates owner/project -n 10       # JSON on stdout
shallow-triage page owner/project -o project.html   # one HTML page
```

Build a site for several repositories:

```sh
shallow-triage site owner/a owner/b -o site/
```

Then open `site/index.html`. Without local clones, pages show only
candidates and issues closed by a merged PR.

`site` is two steps you can also run separately:

```sh
shallow-triage collect owner/a owner/b   # fetch, save reports; no HTML
shallow-triage render -o site/           # HTML from saved reports; offline
```

Reports are saved in `~/.local/share/shallow-triage/reports` (change with
`--data`). `render` needs no network or clones, so pages can be rebuilt or
restyled from earlier runs. If a repository fails to collect, `site` keeps
showing its last saved report. See [docs/architecture.md](docs/architecture.md)
for how input, data and output are separated.

## Using local clones

To get fix branches and closable issues, and to work on issues, describe each
repository in a TOML file. The default location is
`~/.config/shallow-triage/triage.toml`; another file can be passed with
`--config`. It lists the local copy, its venv, and how to run code in it:

```toml
[[repo]]
name = "owner/project"               # required
path = "~/src/project"               # local clone
workdir = "~/src/project-triage"     # where fix, test and bisect run (default: path)
base = "upstream/main"               # default: upstream/main
venv = "~/venv/project"              # activated for build, test and run
build = "pip install -e ."           # after each checkout, if the project needs it
test = "python -m pytest {files}"    # the default
run = "python {script}"              # how to run a reproduction script (the default)
branches = ["triage/issue-*"]        # default: ["triage/issue-*", "fix-*"]
since = 2024-01-01                   # ignore older evidence for "closable"
```

The config is trusted: `build`, `test` and `run` are shell commands run as
written. Keep it private: it names your local folders.

```sh
shallow-triage site --fetch -o site/
```

- **Fix branches** are local branches matching `branches` that are not merged
  into `base`. The issue number comes from the branch name
  (`triage/issue-123`, `fix-123-short-name`) or else from `Fixes #123` in a
  commit message. Branches without an open issue are listed separately.
- **`{files}`** in `test` becomes the changed test files, or every changed
  file when the branch changes no test file (for doctest-based projects).
- **`--fetch`** runs `git fetch` on the remote of `base` before reading, so
  the comparison uses the latest upstream. It changes only remote-tracking
  refs. Without it, the clones are only read.
- **Closable** issues are those with a verified finding (see below), or that
  a commit on `base` (`Fixes #N`, `Closes #N`, `Resolves #N`) or a merged PR
  says it fixes. Verified ones are listed first.

## Working on issues

These commands run in a repository's `workdir` with its `venv` active and
with credentials (`*_TOKEN`, `*_SECRET`, `*_API_KEY`, ...) removed from the
environment. They refuse to start if the workdir has uncommitted changes.

```sh
shallow-triage fix owner/project 123        # create triage/issue-123 from base
shallow-triage test owner/project           # build, then test what the branch changed
shallow-triage bisect owner/project 123 --old v1.2.0 --script repro.py
```

`bisect` checks that the issue shows at `--old` and is fixed on `base`, then
binary-searches the base's main line for the commit that fixed it, running
`build` and `run` at each step. The script exits 0 when the issue is fixed,
125 when it can't tell (the commit is skipped), and anything else when the
issue shows. The workdir is put back on its branch afterwards. The result,
with the PR the commit came from, is recorded as a verified finding.

Findings can also be recorded by hand:

```sh
shallow-triage closable add owner/project 123 --commit abc123 --pr URL --method "ran repro on main"
shallow-triage closable add owner/project 456 --status "not reproducible" --note "..."
shallow-triage closable list owner/project
shallow-triage closable remove owner/project 123
```

### Issues marked safe

Code in an issue is written by anyone. Read the issue, and if its code is
safe to run, mark it:

```sh
shallow-triage safe mark owner/project 123
shallow-triage repro owner/project 123 -o repro.py   # its Python code blocks
shallow-triage safe list owner/project
```

A mark records the title and body as they were. If either changes later,
the mark no longer holds: `repro` refuses, and pages show "changed since
marked safe" until the issue is reviewed and marked again. Pages show
"marked safe" next to marked issues.

Findings and marks are kept in `~/.local/share/shallow-triage/findings` and
`.../safe`, readable only by you.

## Fetching and caching

Nobody reads a whole backlog at once, so issues are fetched a batch at a
time and cached in `~/.cache/shallow-triage` (or
`$XDG_CACHE_HOME/shallow-triage`). Each run:

1. **Updates what is cached**: asks GitHub only for issues changed since the
   previous run. Closed issues are dropped, edited ones refreshed, and
   recently active ones added even if the batches haven't reached them.
2. **Adds a batch**: fetches the next `--batch` open issues, newest first,
   continuing where the previous run stopped.
3. **Looks up specific issues**: the issues of local fix branches, and up to
   `--batch` issues cited by "Fixes #N" commits whose state isn't known yet.
   Numbers found to be closed are remembered, so they are never asked again.

A run takes seconds, even for repositories with thousands of open issues.
Pages say how much of the backlog has been fetched so far, for example
"2576 open issues, 307 fetched so far"; candidates and closable issues are
picked from those.

| Option | Effect |
| --- | --- |
| `--batch N` | Fetch N more open issues per run (default 100) |
| `--all` | Fetch every open issue not cached yet |
| `--refresh` | Discard the cache and start over |
| `--no-cache` | Don't read or write the cache; each run starts from scratch |
| `--cache-dir DIR` | Keep the cache somewhere else |

No token is stored. For a private repository the cache and the saved
reports hold its private issue content, so their files are readable by your
user only. Repositories in
a site are processed in parallel.

## Sharing the pages

The pages are static HTML with no scripts and a Content-Security-Policy that
blocks any. They never show where your local clones are: commands are meant
to be run from inside the clone, and your home folder is shown as `~`
wherever it appears, for example in a `test` command.

They do show branch names, commit messages and diffs of unpushed branches.
Check those before publishing a site, and don't publish pages built from
private repositories.

## Working with an agent

The intended workflow is that an agent picks and fixes issues on local
branches and a human reviews them on the pages. The agent's instructions
(rules, step-by-step workflow, the JSON output, commit message format and
hand-off checklist) are in [docs/triage-agent.md](docs/triage-agent.md);
point the agent to that file.

## Development

See [docs/architecture.md](docs/architecture.md) before changing a stored
format: the config, the cache and saved reports must stay readable.

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run repo-review .
```

Page markup and styles are in `src/shallow_triage/templates/`.
