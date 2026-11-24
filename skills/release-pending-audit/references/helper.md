# Git evidence helper

Paths are relative to this skill. Python 3.9+ and Git 2.29+; the static YAML resolver in
`prepare_audit.py` also needs [PyYAML](../requirements.txt). Use an approved environment; **do not
install dependencies or change credentials without permission**. The manual path below needs only
Python and Git. Neither helper calls Jira, host APIs or running clusters.

## Common path: one work manifest, two commands

Create a fresh 0700 directory under the configured state directory (`umask 077` and `mktemp -d`);
use private files, never a service repo. `$run` below is that directory. Start the action ledger
with no approval. Save the playbook revision and effective release policy/comment, Jira scope
(query/IDs, board filter, sprint or explicit whole-column choice), complete-comment review,
PR metadata/acceptance evidence, and repo requests in `work.json`.
Record identifiers/versions sufficient for a later freshness comparison, not tokens or raw comments.

```json
{
  "app_interface_repository": "app-interface",
  "repositories": [
    {"id": "app-interface", "path": "/workspace/app-interface", "remote": "upstream",
     "expected_identity": "gitlab.example.com/platform/app-interface"},
    {"id": "service", "path": "/workspace/service", "remote": "origin",
     "expected_identity": "github.com/example/service", "default_branch": "main"}
  ],
  "tickets": [{
    "key": "PROJECT-123", "url": "https://jira.example.com/browse/PROJECT-123",
    "scope_complete": false, "blockers": [],
    "requirements": [{
      "repository": "service", "url": "https://github.com/example/service/pull/12",
      "merged": true, "landed_commits": ["FULL_INTEGRATED_COMMIT"], "targets_complete": false
    }]
  }]
}
```

Use each canonical repo once, including app-interface. IDs are manifest-local. `expected_identity`
is the independently verified host/full project path, not the checkout basename. If needed,
`python3 scripts/git_audit.py inventory /path/to/workspace` lists immediate checkouts/remotes
without fetching. Supply `default_branch` only from fresh host metadata; otherwise the helper asks
the remote HEAD. Omit unavailable checkouts and record ticket blockers. Do not erase their work
requirements to obtain a verdict.

```sh
python3 scripts/prepare_audit.py "$run/work.json" --output "$run/snapshots.json"
# Review the saved map and complete the evidence fields described below, THEN:
python3 scripts/git_audit.py check "$run/work.json" --snapshots "$run/snapshots.json" --output "$run/results.json"
```

1. **Prepare** pins the app-interface default commit, resolves candidates per canonical service
   repo at that revision, then syncs service default branches with full history, with up to four
   independent stores in parallel. Duplicate repo/remote requests share one snapshot; linked
   worktrees sharing a Git store serialize. Only missing pinned target SHAs need an additional
   fetch (one grouped attempt per repo). For verified nonstandard/manual refs, `required_commits`
   on the repo request may supply additional full SHAs. Never fetch every remote branch to
   compensate for unknown scope. An unavailable object remains a blocker for checks that need it.
2. **Review** `snapshots.json`: `repositories` has identity, default branch/SHA and fetch time or
   `error`; `target_maps` is keyed by canonical identity. The resolver searches the pinned `data/`
   tree for SaaS v2 files containing the repo name, prioritizes `data/services/insights`, and matches
   the exact normalized source URL. Each `file#template` has the source template path, static prod
   targets (full ref, source selector, namespace/cluster/environment), exclusions and blockers.
   It follows static target and namespace references, using environment `servicePhase`, not filenames.
   It does **not** decide business scope, resolve every schema/selector/override, or prove absence.
   Check for other applicable deployments, aliases and rollback evidence. `prepare` success means
   the snapshots were obtained, **not** that the mappings are complete.
3. In **the same work manifest**, set `app_interface_sha` to the exact reviewed snapshot SHA.
   Set `scope_complete` only after all comments, required work and acceptance scope are accounted
   for; set `targets_complete` per merged requirement only after verifying full applicable coverage.
   The default is to include **every** candidate template's production targets. To exclude e.g. an
   unrelated dashboard, put `"exclude_templates": {"data/path/file.yml#dashboard": "<reviewed reason>"}`
   on that requirement. Unrecognized exclusions, unresolved in-scope templates, no targets, false
   flags or ticket blockers cannot produce Released. Never discard a blocker without evidence.
4. **Check** is offline. It verifies the reviewed config pin, repository identities/full history,
   actual commit types, landed ancestry on the default branch and in **each** prod ref, and standard
   `This reverts commit <sha>` messages. It memoizes checks across tickets. Manual/nonstandard
   rollback evidence must still be reviewed. Results include each PR/landed SHA/target/prod SHA;
   stdout with `--output` is only a summary. `results.json` is the report evidence, not closure approval.

## Manual mapping / policy exceptions

If PyYAML is unavailable, or a deployment uses another schema, overlays, dynamic selectors, mutable
refs or an alias the static resolver cannot map, inspect the pinned Git objects or equivalent
revision-bound authorized source. With no resolver, `git_audit.py sync "$run/work.json" --output
"$run/snapshots.json"` pins all available repos in one call. Do not use working-tree YAML or stale
maps. Only after **independently resolving every applicable target** supply `mapping_evidence` and
explicit `targets` on the requirement instead of a generated mapping, e.g.:

```json
{
  "mapping_evidence": "<why these are all effective prod targets, with pinned sources>",
  "targets": [
    {"name": "service/cluster-a/namespace", "sha": "FULL_PROD_COMMIT_A", "source": "data/path/file.yml#selector-a.ref"},
    {"name": "service/cluster-b/namespace", "sha": "FULL_PROD_COMMIT_B", "source": "data/path/file.yml#selector-b.ref"}
  ]
}
```

For a **configured merge-equals-release exception**, set `release_policy` on the requirement to the
exact key from `config.json` instead of supplying `targets`. `merge_equals_release_repositories`
requires a verified canonical repo on the allowlist. `merge_equals_release_when_undeployed` requires
both `deployment_absence_evidence` (verified beyond the candidate search) and `package_evidence`
(pinned repo evidence that this is an npm package repo or Nx monorepo); an unresolved or nonempty
deployment map conflicts with this exception. The helper uses the **current verified default SHA**
as the target, including after refresh. A merge, a `package.json`, or absence of a local checkout
alone is not a release. Completeness, ancestry and revert checks still apply; report the policy reason.

## Freshness, failures and tests

Keep each review/approval tied to its own files. On changed evidence, write refreshed snapshots and
results separately; re-review changed coverage before setting any flags. Do not overwrite the
approved report or infer approval from a previous run. For a verified remote, `git ls-remote --symref
-- <remote> HEAD` in that repo compares both the current default branch name and tip with its snapshot
without fetching objects. This does not replace the Jira/PR freshness checks. Verify current Jira state and write outcomes
as described in the skill. Successful `--output` writes private JSON atomically. Exit status 0 means
report/command completed, 1 means partial sync (inspect per-repo errors), 2 means invalid input or
fatal error. No implicit retry. `prepare_audit.py --timeout` or `git_audit.py --timeout` (before its
subcommand) overrides the per-Git-command timeout, default 180 seconds.

Local behavioral tests use temporary checkouts/remotes only:

```sh
python3 -B -m unittest discover -s tests -v
```
