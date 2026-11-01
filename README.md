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

## Using local clones

To get fix branches and closable issues, describe each repository in a TOML
file and pass it with `--config`:

```toml
[[repo]]
name = "owner/project"               # required
path = "~/src/project"               # local clone
base = "upstream/main"               # default: upstream/main
test = "tox -e py -- {files}"        # default: python -m pytest {files}
branches = ["triage/issue-*"]        # default: ["triage/issue-*", "fix-*"]
since = 2024-01-01                   # ignore older evidence for "closable"
```

```sh
shallow-triage site --config triage.toml --fetch -o site/
```

- **Fix branches** are local branches matching `branches` that are not merged
  into `base`. The issue number comes from the branch name
  (`triage/issue-123`, `fix-123-short-name`) or else from `Fixes #123` in a
  commit message. Branches whose issue has closed are listed separately.
- **`{files}`** in `test` becomes the changed test files, or every changed
  file when the branch changes no test file (for doctest-based projects).
- **`--fetch`** runs `git fetch` on the remote of `base` before reading, so
  the comparison uses the latest upstream. It changes only remote-tracking
  refs. Without it, the clones are only read.
- **Closable** evidence is a commit on `base` whose message says `Fixes #N`,
  `Closes #N` or `Resolves #N`, or a PR merged into the repository that was
  linked to close the issue. Check that the issue no longer reproduces before
  closing it.

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

No token is stored. For a private repository the cache holds its private
issue content, so its files are readable by your user only. Repositories in
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

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run repo-review .
```

Page markup and styles are in `src/shallow_triage/templates/`.
