# shallow-triage
A more efficient way to manage backlog

Builds triage pages for GitHub repositories. Each repository page lists:

- **Ready to review**: local fix branches for issues that are still open, with
  the commit message as the suggested fix, the changed files, the diff, and
  commands to view the diff and run the tests.
- **Possibly closable**: open issues that a commit on the base branch or a
  merged PR says it fixes, with links to that evidence.
- **Candidates**: open issues without a fix, ranked by how well-scoped they
  look.

GitHub access goes through the [`gh`](https://cli.github.com/) CLI, so log in
with `gh auth login` first. The package never reads or stores a token.

## Usage

```sh
shallow-triage candidates owner/project -n 10       # ranked issues as JSON
shallow-triage page owner/project -o project.html   # one page
shallow-triage site --config triage.toml -o site/   # index + one page per repo
```

Without a local clone, `site owner/a owner/b -o site/` shows only candidates.

## Config

```toml
[[repo]]
name = "owner/project"
path = "~/src/project"               # local clone
base = "upstream/main"               # default: upstream/main
test = "tox -e py -- {files}"        # default: python -m pytest {files}
branches = ["triage/issue-*"]        # default: ["triage/issue-*", "fix-*"]
since = 2024-01-01                   # ignore older evidence for "closable"
```

Fix branches are local branches matching `branches` that are not merged into
`base`. The issue number comes from the branch name (`triage/issue-123`,
`fix-123-short-name`) or else from `Fixes #123` in a commit message.
`{files}` in `test` becomes the changed test files, or every changed file when
the branch changes no test file (for doctest-based projects).

The tool only reads the clones. Run `git fetch upstream` first so `base` is
current.
