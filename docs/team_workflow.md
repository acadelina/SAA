# Team workflow

Working agreement for the three-person Shifting Hulls team. The repository is public. Keep `main` working at all times.

## Branches

- `feature/<short-name>` for new behaviour, `docs/<short-name>` for documents, `fix/<short-name>` for bugs.
- One branch per change, created from an up-to-date `main`. Squash merge, then delete the branch.
- Never commit directly to `main`. Never force push a shared branch.

## Pull requests

- Small: one purpose, reviewable in one sitting. Split a PR that grows past its purpose.
- One reviewer, never the author. Link the issue in the PR description.
- Say what changed and how you checked it. Name the commands or tests you ran.
- Merge only after the reviewer approves. If CI exists, it must be green.

## Commit messages

- Subject: imperative, under 60 characters, no full stop. Example: `Add stub agent to daily cycle`.
- Blank line, then 1 to 4 plain-English lines on what changed and why.
- One logical change per commit. No filler such as "update" or "fix stuff".

## Plan changes

- Any change to the timeline, scope or "Done when" column in `docs/Milestone_2_Architecture_and_Plan.md` gets its own PR.
- In that PR, update the document and add a row to the plan change log in Section 8 with the date, what changed and why.
- Never change the plan inside a feature PR.

## Never commit

- API keys, tokens, passwords, or `.env` files.
- Raw Global Fishing Watch data, or derived records the licence does not allow us to publish.
- Large AIS files (ZIP or CSV) and database dumps.
- Model weights and generated binary artefacts.

If a secret reaches a commit, rotate it and tell the team. Removing it in a later commit is not enough.

Keep `.gitignore` current. It must cover at least:

```
.env
data/
*.zip
*.csv
models/
*.log
```

If a small fixture CSV is needed, add a narrow exception with a comment. Before every commit, run `git status` and `git diff --cached --stat`. If a data file or a secret appears, stop and remove it from the staged change.

## Issues and labels

Every PR links one issue that states the change and its "Done when" check. Labels:

| Group | Labels |
|---|---|
| Area | `area:simulator`, `area:pipeline`, `area:ai`, `area:eval`, `area:docs` |
| Type | `type:feature`, `type:bug`, `type:risk`, `type:data` |
| Priority | `priority:high`, `priority:medium`, `priority:low` |
| Status | `blocked` (waiting on another issue or a spec decision) |

## Ownership

Three areas, each with an owner and a cover. Use `owner: TBD` until the team agrees.

| Area | Owner | Cover |
|---|---|---|
| Simulator and data | TBD | TBD |
| Ingestion, storage and orchestration | TBD | TBD |
| AI models, evaluation and dashboard | TBD | TBD |

The cover reviews the owner's PRs and can run the component. No area should depend on one person being available.
