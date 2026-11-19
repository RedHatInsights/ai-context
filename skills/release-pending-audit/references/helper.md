# Git helper

Requires Python 3.9+ and Git 2.29+. No Python dependencies, provider tokens, or Jira access. Paths below are relative to the skill directory. The helper does not switch branches, modify working-tree files, run repository hooks, call host APIs, or write to Jira.

## Commands

```bash
python3 scripts/git_audit.py inventory /path/to/workspace
python3 scripts/git_audit.py sync /path/to/run/repos.json --output /path/to/run/snapshots.json
python3 scripts/git_audit.py check /path/to/run/checks.json --snapshots /path/to/run/snapshots.json --output /path/to/run/results.json
python3 -B -m unittest discover -s tests -v
```

- `inventory` inspects the workspace itself and immediate child checkouts, including Git worktrees. It does not fetch. It returns canonical repository identities without credentials.
- `sync` is the ONLY helper command that fetches. It groups duplicate repo/remote requests, verifies remote identity, resolves the default branch (unless supplied from fresh host metadata), fetches all branch history once, and unshallows if needed. It reads immutable default-branch objects; the user's current checkout is untouched. Remote-tracking refs change as expected from a fetch.
- `check` is offline. It requires full commit SHAs and successful snapshots. It caches commit/ancestry/revert queries across all tickets and emits tri-state results. Missing inputs/errors fail closed.
- `--output` saves full JSON privately (mode 0600). For `check`, stdout contains only ticket summaries; detailed evidence stays in the file. With no output path, the complete JSON is printed.
- Exit codes: 0 = command/report completed (a report may contain unreleased/uncertain tickets); 1 = one or more sync failures, inspect the JSON; 2 = invalid input or fatal error. Individual failures do not erase other results.
- Git commands have a timeout (default 180 seconds; `--timeout` before the subcommand overrides it). No implicit retries.

## Inventory output

Match PR/MR target repository URLs to `identity`, preserving the host and full GitLab subgroup path. HTTPS and SSH forms normalize to the same identity; GitHub path case is normalized. Custom SSH aliases/ports may need explicit confirmation rather than guessed equivalence.

```json
{
  "repositories": [
    {
      "path": "/workspace/service",
      "remotes": [{"name": "origin", "identity": "github.com/example/service"}]
    }
  ]
}
```

## Sync input

IDs are local manifest identifiers, not Jira project keys. Give `app-interface` its own ID. `expected_identity` must match inventory AND independently verified repository metadata. `default_branch` is optional; omit it to resolve the remote's advertised HEAD instead of trusting a local `origin/HEAD`.

```json
{
  "repositories": [
    {
      "id": "app-interface",
      "path": "/workspace/app-interface",
      "remote": "origin",
      "expected_identity": "gitlab.example.com/platform/app-interface"
    },
    {
      "id": "service",
      "path": "/workspace/service",
      "remote": "upstream",
      "expected_identity": "github.com/example/service",
      "default_branch": "main"
    }
  ]
}
```

Normally sync app-interface first to inspect its config, then sync all service repositories together. Merge those outputs' `repositories` objects into one snapshot document for `check`; retain each entry's fields/timestamp unchanged. If app-interface also appears in the work list, reuse its first snapshot. Do not replace a failed refresh with a previous successful snapshot.

Each successful snapshot includes `path`, `remote`, `identity`, `default_branch`, `default_sha`, and `fetched_at`. A failure has `error` instead. There is no cross-run automatic reuse.

## Check input

The placeholder SHAs below must be replaced with actual FULL 40- or 64-digit commit IDs. `app_interface_sha` must equal the reviewed app-interface snapshot, including after a refresh. `scope_complete` means all necessary comments, PRs, and acceptance scope were reviewed. `targets_complete` means ALL applicable production deployments for that requirement were mapped. Missing/false completeness flags block a Released result. Pass unresolved issues in `blockers`.

```json
{
  "app_interface_repository": "app-interface",
  "app_interface_sha": "FULL_REVIEWED_APP_INTERFACE_COMMIT",
  "tickets": [
    {
      "key": "PROJECT-123",
      "url": "https://jira.example.com/browse/PROJECT-123",
      "scope_complete": true,
      "blockers": [],
      "requirements": [
        {
          "url": "https://github.com/example/service/pull/123",
          "repository": "service",
          "merged": true,
          "landed_commits": ["FULL_INTEGRATED_COMMIT"],
          "targets_complete": true,
          "targets": [
            {
              "name": "service/prod/crcp/namespace",
              "sha": "FULL_PRODUCTION_COMMIT",
              "source": "data/path/to/prod.yml#deployments[service].ref"
            },
            {
              "name": "service/prod-multicluster/hccp/namespace",
              "sha": "FULL_PRODUCTION_COMMIT",
              "source": "data/path/to/prod-multicluster.yml#deployments[service].ref"
            }
          ]
        }
      ]
    }
  ]
}
```

A requirement with `merged: false` is Not released; an absent/unknown merged state needs clarification. For merged work, every `landed_commits` entry must be on the fetched default branch, and every target must contain every landed commit. An empty requirements list is NOT automatically released. Missing repository snapshots, Git errors, shallow history, non-commit refs, or missing evidence sources need clarification.

Standard `This reverts commit <sha>` messages between a landed and production commit cause Needs clarification, including revert-of-revert situations that require human interpretation. Nonstandard/manual reverts cannot be ruled out by this helper; the agent must inspect other rollback evidence. A commit's inclusion is evidence about configured refs, not live rollout status or complete business acceptance.

## Safe verification

Tests create temporary repositories and local bare remotes only. They do not touch the workspace, contact hosting services, or call MCP. To inspect helper argument syntax without doing any work:

```bash
python3 scripts/git_audit.py --help
```
