# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues for
`zimondeng/ai-eval-center`. Use the `gh` CLI for issue operations.

## Conventions

- Create an issue: `gh issue create --title "..." --body "..."`.
  For multiline bodies, write the text to a file and use `--body-file`.
- Read an issue: `gh issue view <number> --json number,title,body,labels,comments`.
- List issues: `gh issue list --state open`, adding `--label`, `--state`,
  or `--json` filters as needed.
- Link child issues and blockers using GitHub's native sub-issues and
  issue dependencies when available. Otherwise, add `Part of #<parent>`
  or `Blocked by: #<number>` to the child issue body. A ticket is
  unblocked when every blocker is closed.
- Comment: `gh issue comment <number> --body "..."`.
- Apply or remove a label: `gh issue edit <number> --add-label "..."`
  or `--remove-label "..."`.
- Close an issue: post an explanation, then run `gh issue close <number>`.

Before creating an issue, verify the target repository with
`gh repo view --json nameWithOwner`. If it is not the user's repository,
confirm the target with the user.

## Pull requests as a triage surface

**PRs as a request surface: no.** Change this to `yes` if the repository
treats external pull requests as feature requests.

When enabled, use the same triage labels and states for external PRs,
using the corresponding `gh pr` commands. Issues and PRs share a number
space; resolve ambiguous numbers before acting.

## Skill operations

- "Publish to the issue tracker": create a GitHub issue.
- "Fetch the relevant ticket": read the GitHub issue.
- `/wayfinder`: use one issue labelled `wayfinder:map` for the map and
  link child tickets as sub-issues. If sub-issues are unavailable, use
  a task list on the map and `Part of #<map>` in each child. Use
  `wayfinder:<type>` labels for child ticket types. Select unassigned
  open children without open blockers, claim with
  `gh issue edit <number> --add-assignee @me`, and record the resolution
  on the child and a pointer in the map.
