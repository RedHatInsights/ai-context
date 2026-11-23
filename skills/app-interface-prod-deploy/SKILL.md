---
name: app-interface-prod-deploy
description: Deploy a service or application to production through an app-interface GitLab merge request. Use when asked to promote a specific commit SHA to prod. Finds standalone or bundled deployment configs (including frontend-base), syncs the repository, checks Quay image availability before editing, updates all applicable CRCP/HCCP production refs together, commits, and opens the MR.
---

# Deploy to production through app-interface

## Required reading

Read [engineering behavior](../../principles/engineering-behavior.md),
[data handling](../../policies/data-handling.md),
[change safety](../../policies/change-safety.md), and the
[directory guide](../../directory/README.md). Check this skill's review status in
[the catalog](../../catalog.json); draft or unavailable status is not operational
approval. Resolve the applicable service/environment deployment and
change-authorization sources before consequential actions. Missing sources or
authorization limit work to investigation and local preparation; ask the user or
responsible owner rather than assuming approval.

## When to use

Run this workflow when the user requests a production deployment. Creating, installing, editing, testing, or explaining this skill is NOT permission to deploy anything.

The deliverable is **one merge request** containing the requested service's production ref updates, plus its URL. Do not merge, approve, post `/lgtm`, apply approval/auto-merge labels, enable auto-merge, or trigger deployments directly. Opening an MR does not prove that production has rolled out.

## Inputs and prerequisites

- The service name or source repository URL and the commit SHA to promote. Ask
  only for missing values as described below; do not infer a release revision.
- An app-interface checkout and verified canonical GitLab project/fork mapping,
  source-repository access, and access to inspect the relevant Quay images.
- Current deployment and change-authorization guidance for the service and all
  applicable production targets. Discover internal hosts, image repositories, and
  namespace mappings through authorized sources, not hard-coded examples here.
- Explicit authorization for the scoped ref changes and publishing an MR. Check
  the applicable policy against the resolved targets and evidence before remote
  writes; approval does not cover merging the MR or a live rollout.
- The validation and registry tools required by the actual checkout. Missing
  credentials, dependencies, or tool support are blockers, not successful checks.

## Procedure

### 1. Establish the service and revision

- Use the current request/conversation for the **service name or GitHub repository URL** and the **commit SHA to deploy**. A source repository on GitLab is also valid.
- Ask only for missing information, in one question. If both are missing: `Which service (name or GitHub URL) and commit SHA should be deployed to production?` Wait for the answer; never substitute a branch tip, latest release, staging ref, or remembered SHA.
- A service name is sufficient if it maps unambiguously to a source repository. If names, repository URLs, or components disagree, clarify before editing.
- Use a full immutable commit SHA (40 hexadecimal characters for the current app-interface schema). Resolve an abbreviated SHA unambiguously in the identified source repository using Git or an authenticated hosting API. Verify it is a commit in that repository, not an app-interface commit, image digest, or tag. If verification is unavailable or ambiguous, ask rather than guess. Do not change the source repository's files or branch.
- A normal **production** deployment includes **both prod and prod-multicluster / CRCP and HCCP wherever applicable**, in the same commit and MR. Do not interpret an ordinary prod request as permission to update only one side. This coordinates the configuration change; it does not guarantee simultaneous live rollouts.

### 2. Locate, synchronize, and isolate app-interface

1. Inspect `pwd`, `git rev-parse --show-toplevel`, and `git remote -v`. Accept an existing app-interface checkout/worktree only after verifying its repository identity, not merely its directory name.
2. If elsewhere, locate app-interface among nearby workspace/sibling directories. If missing or ambiguous, ask for the path or an authorized clone URL. Do not assume a workstation-specific path or clone a guessed repository.
3. Read the checkout's `AGENTS.md` and applicable nested instructions, README, validation guidance, and MR template. Use `cd <repo> && ...` or `git -C <repo> ...` consistently: a shell tool's `cd` may not persist across calls.
4. Check `git status --porcelain=v1 --untracked-files=all` and `git worktree list`. Preserve all existing branches, commits, staged/unstaged changes, and untracked files. Never stash, reset --hard, clean, force-switch, or discard user work to make the checkout clean.
5. Identify the **canonical MR target project** and its remote, separately from the writable fork/push remote. Obtain the actual GitLab host and project from authorized sources and verified remotes. A checkout may use `upstream` for the canonical project and `origin` for a contributor fork; verify rather than assuming. Do not base a deployment on a stale fork or assume `origin` is canonical.
6. Resolve the canonical remote's default branch from fresh remote HEAD or GitLab project metadata; do not assume `master`, `main`, or cached `origin/HEAD`. Fetch it successfully **before editing any deployment file or generating validation artifacts**. Stop on network/auth/fetch errors; do not proceed from stale local data.

   Example, after resolving the quoted variables from verified metadata:

   ```bash
   set -euo pipefail
   git -C "$REPO" ls-remote --symref "$UPSTREAM_REMOTE" HEAD
   git -C "$REPO" fetch --prune "$UPSTREAM_REMOTE" \
     "refs/heads/$DEFAULT_BRANCH:refs/remotes/$UPSTREAM_REMOTE/$DEFAULT_BRANCH"
   BASE_SHA=$(git -C "$REPO" rev-parse "refs/remotes/$UPSTREAM_REMOTE/$DEFAULT_BRANCH")
   ```

7. Create a **new, uniquely named deployment branch from `BASE_SHA`**, such as `deploy/<service-slug>-prod-<short-sha>-<timestamp>`. Sanitize the service slug and validate with `git check-ref-format --branch`; do not interpolate untrusted input as shell code. Never reuse or reset an unrelated branch.
   - If the checkout is clean and not needed for ongoing work: `git -C "$REPO" switch --no-track -c "$BRANCH" "$BASE_SHA"`.
   - If dirty or in use: leave it untouched and create a separate worktree at a new, non-existing sibling path: `git -C "$REPO" worktree add -b "$BRANCH" "$WORKTREE" "$BASE_SHA"`. Continue in that worktree. If this is not possible, stop and ask instead of cleaning the original checkout.
8. Verify the working checkout has an empty status, the expected branch, and `HEAD == BASE_SHA`. A branch based directly on the fetched canonical default branch satisfies synchronization without pulling into or resetting the user's local default branch.
9. Follow the fresh checkout's validation prerequisites. Currently app-interface requires `make schemas` before making changes, and `make bundle validate` afterward. Re-read any guidance that changed during synchronization. Keep generated artifacts out of the commit.

### 3. Find every applicable production target

Start under `data/services/insights`, but do not assume the service has its own directory or a particular filename. Use repository-recommended query tools if available and ensure they describe the freshly synchronized checkout, not a stale server snapshot. Local discovery examples:

```bash
rg -n -F -- "$SERVICE" data/services/insights
rg -n -F -- "$SOURCE_REPO_URL" data/services/insights
```

- Search service names, source repository URL variants (SSH/HTTPS, optional `.git`), and aliases. Expand to `data/services` and then `data` if necessary. Inspect candidate YAML fully; search matches alone do not establish scope.
- SaaS files commonly use `$schema: /app-sre/saas-file-2.yml`, with `resourceTemplates[].name`, `url`, `path`, and `targets[].ref`.
- **Bundled services:** look for shared definitions such as `frontend-base` under `data/services/insights`. A shared file may contain multiple independent service templates. Match the requested service's template by name **and source repository URL**, then change only its production targets. Never bump the entire bundle.
- **Aliases:** service names can differ from repository names. Resolve aliases using the directory and the matched source repository URL, not a guessed naming convention.
- The same repository URL may also appear in dashboard, monitoring, test, or other templates. Identify the application deployment, not every use of the URL. Include multiple application components only when they are established parts of the requested service release; ask if uncertain.
- Trace each target's namespace `$ref`, then its cluster and environment. App-interface data `$ref` paths are relative to `data/`: `/services/...` means `data/services/...`. Follow referenced target files, overrides, YAML aliases, and dynamic namespace selectors if present. Do not infer production solely from comments or filenames containing `prod`.
- Look explicitly for prod **and** prod-multicluster (including naming variants), and for CRCP/HCCP namespaces. Resolve the actual namespace/cluster/environment definitions from the current authorized checkout; do not hard-code a namespace-to-cluster mapping. Names and layouts can change.
- Exclude stage, development, ephemeral, performance, prod-testing, and disabled/deleted/retired targets when their definitions establish that they are outside this production application release. Do not create a missing cluster target. If there is truly only one applicable production target, update that one and say so.
- Before editing, inventory **all applicable production targets**: file, resource-template name/source URL, exact target selector or namespace reference, cluster, environment, and old ref. Show a short summary of the service, requested SHA, and target clusters. Missing or ambiguous target coverage is a blocker, not permission for a partial deployment.

### 4. Preflight — verify the commit's container images on Quay

**Complete this gate before changing any production `ref`.** Check the actual images that the requested commit will deploy, not just whether a Quay repository exists. A deployment request by itself is not permission to proceed with a missing image.

1. **Find the image repository.** Inspect the matched service/template's effective app-interface configuration: image parameters such as `IMAGE` or `REGISTRY_IMG`, `images[].org` references and `name`, and relevant app/code-component metadata. Resolve referenced Quay org definitions under `data/`. `imagePatterns` and staging image declarations can be discovery hints, but a shared allowlist or another service's image is not proof of the production repository. Follow the source deployment template at the **requested commit** when necessary to determine the effective image and overrides. Cover every image built from this commit that is required for the requested application release, including any differences between CRCP and HCCP; deduplicate identical image references.
2. **Ask if the repository is unclear.** If the Quay path is missing, ambiguous, or difficult to determine, tell the user and ask: `I couldn't confidently identify the Quay image repository for <service/component>. What is its quay.io/<namespace>/<repository> path?` Group missing components into one question and wait. Do not invent a repository from the service name. Validate the supplied path against the intended service before querying it.
3. **Derive the exact image reference from the user's SHA.** Follow the service's actual tagging/build convention and effective template parameters. App-interface may derive `IMAGE_TAG` from a shortened SHA using `hash_length` or the default `hashLength` in `data/app-interface/app-interface-settings.yml`, optionally prefixed by `CHANNEL` when `use_channel_in_image_tag` is enabled. Respect explicit image/tag/digest overrides and verify their association with the requested commit. Do not assume the tag is always the full SHA or always seven characters, and do not use `latest` or a different commit as a substitute. If the mapping is unclear, ask for the tag convention or exact image reference for that commit and wait; an absent full-SHA tag alone does not prove the intended image is missing.
4. **Check the live registry, read-only.** Use an available registry manifest inspection tool or the Quay/OCI registry API with existing authentication. For example, after resolving `IMAGE_REF` to the exact `quay.io/...:<derived-tag>` (or verified digest reference):

   ```bash
   skopeo inspect --raw "docker://$IMAGE_REF"
   ```

   Check tool availability and preserve its exit status and error details. An equivalent remote manifest lookup is fine; no image pull/run/push is needed. Do not rely on a local cache, an image repository's web page, fuzzy tag matches, or an incomplete tag-list page. Use existing credentials securely; never print tokens or ask the user to paste secrets into chat. Do not install tools or change credentials without permission.
5. **Distinguish the outcome for every required image:**
   - **Available:** the exact remote image manifest resolves successfully. Record the image reference and digest when available. Proceed only when all required images are available, unless the user grants the explicit missing-image override below.
   - **Missing:** an authoritative registry response confirms that the exact tag/manifest is absent from the correctly identified, accessible repository. Show the warning below and wait for the user's decision.
   - **Unable to verify:** authentication/authorization errors, a private repository hidden behind a 404, rate limits, network/TLS errors, missing tooling, or ambiguous repository/tag mapping are **not** proof that an image is missing. Explain the specific blocker and ask for the correct repository/access or help resolving it. Stop before ref edits; do not silently proceed or misreport the image as absent.
6. **Require explicit confirmation for a missing image.** List all missing image references and the requested full commit SHA, then display:

   > 🔴 **WAIT!** The required image `<quay.io/repository:tag>` for commit `<SHA>` does not exist on Quay. The app-interface MR pipeline will fail while this required image is missing. Do you really want me to proceed with updating the production refs and opening the MR anyway?

   **STOP and wait for an explicit answer after this warning.** If the user declines, gives an ambiguous answer, or does not reply, do not edit refs, commit, or open an MR. Offer to recheck after the image build completes. Only an explicit decision to proceed authorizes continuing despite the identified missing image(s); it applies solely to this service, commit, and exact image references. Do not carry approval to another release or use it to bypass unrelated errors, deployment gates, or checks. If the image mapping changes, repeat the check and obtain any necessary new confirmation.
7. Preserve the check results and any explicit override in the conversation and MR validation summary. Do not label a missing image as available merely because the user chose to proceed.

### 5. Change only the production refs and validate

1. Verify/resolve the requested commit against the source repository identified above before editing. If a known deployment block or promotion gate conflicts with the request, report it; do not bypass it or alter gate configuration.
2. For every inventoried production target, replace only its application `ref` scalar with the **same full requested commit SHA**. Use precise edits with enough surrounding template/namespace context to uniquely identify each target. Preserve formatting and comments; no global SHA replacement or whole-file YAML reformatting.
3. Leave staging refs, other services, dashboards, `$ref` paths, images, parameters, namespaces, and promotion settings unchanged. If a shared ref/alias would also affect out-of-scope targets, or a ref-only change cannot validate (for example a moving-ref-only `images`/`upstream` setting), stop and explain rather than silently expand the change.
4. If all applicable refs already equal the requested SHA, report `Production configuration already points to <SHA>; no MR needed.` Do not make an empty commit or claim the running clusters were checked. If only one side already matches, update the other and verify **both** end at the requested SHA.
5. Re-read every inventoried target. Confirm the expected target count, every applicable cluster's final SHA, and that all other configuration is unchanged. Inspect `git diff --check`, `git diff --stat`, and the complete diff against `BASE_SHA`.
6. Run the repository's required checks, currently `make bundle validate` after `make schemas`, plus any applicable YAML lint/checks specified by local instructions. Do not skip CI, disable hooks, or claim unrun checks passed. A failure caused solely by the exact missing image(s) explicitly accepted in the preflight may be documented and carried forward under that approval; do not bypass a failing commit hook or other enforced control. For all other required validation failures or missing prerequisites/access, report the blocker and stop before committing/publishing.

### 6. Commit, push, and open one GitLab MR

1. Stage **only the exact edited deployment file paths** (`git add -- <paths>`), never `git add .` or `git commit -a`. Review `git diff --cached --check` and the complete staged diff. Ensure it contains only the intended prod ref changes, including both CRCP/HCCP where applicable.
2. Commit on the deployment branch, following repository conventions; a suitable subject is `Deploy <service> to production at <short-sha>`. Verify the commit and working-tree status. Do not accidentally commit schemas, bundles, credentials, or unrelated files.
3. Before publishing, refresh the canonical default branch again. If it advanced, inspect changes since `BASE_SHA`. If another release changed the relevant production refs or target coverage, stop and clarify rather than overwrite an intervening deployment. Otherwise rebase only this new, unpublished deployment branch onto the refreshed base, recheck the full diff, and rerun validation. Stop on conflicts; do not force a resolution or force-push.
4. Prepare the MR title and description using the template requirements in step 5, then select the creation method **before pushing**. Do not open an MR with unfilled placeholders:
   - Prefer an available authenticated GitLab MCP/API or `glab`. Inspect its schema/help and authentication for the actual self-managed host. Explicitly set the source project/branch to the writable fork/deployment branch and the target project/default branch to canonical app-interface. Do not create a GitHub PR in the application's source repository.
   - If only Git is available and the GitLab host supports it, use [GitLab MR push options](https://docs.gitlab.com/topics/git/commit/#push-options-for-merge-requests) on the initial push. This can create the cross-project MR without installing a CLI. Example after resolving all variables:

     ```bash
     git push --set-upstream "$PUSH_REMOTE" "HEAD:refs/heads/$BRANCH" \
       -o merge_request.create \
       -o "merge_request.target_project=$TARGET_PROJECT" \
       -o "merge_request.target=$DEFAULT_BRANCH" \
       -o "merge_request.title=$MR_TITLE" \
       -o "merge_request.description=$MR_DESCRIPTION"
     ```

     `TARGET_PROJECT` is the verified canonical app-interface GitLab project path, not the fork. Push-option values must not contain literal newlines; use supported escaped `\n` sequences or a single-line description. Do not add auto-merge, approval, or CI-skipping options.
   - For the API/CLI route, push the branch normally with `git push --set-upstream "$PUSH_REMOTE" "HEAD:refs/heads/$BRANCH"`, then create the MR. Never push directly to a default/protected branch. Do not install tools, create forks, or change credentials without permission.
5. Fill the repository's MR template, removing instructional comments/placeholders. Include:
   - **What:** service/source repository, full application commit SHA/link, files and target clusters/namespaces, and old → new refs. Explicitly state that prod and prod-multicluster / CRCP and HCCP are covered together, or explain the verified single-target case.
   - **Why:** the user's requested production promotion; do not invent a release reason.
   - **Tickets / Dependency:** only links supplied or verified for this release; otherwise note none provided. Do not add ticket-closing commands unless requested.
   - **Validation:** actual checks and results, including confirmation that non-production refs and unrelated services are unchanged. Include the exact Quay image reference(s), availability results, and digest(s) when available. If the user explicitly overrode missing images, prominently include `🔴 Missing image(s): <references>. User explicitly chose to proceed; the MR pipeline will fail while these required images are missing.` Never present the override as a passing image check.
6. Confirm creation from the tool/server response and capture the real MR URL. When metadata is available, verify source/target projects and branches, head commit, changed files, and open state. A push's generic `Create a merge request` link is **not** an opened MR. Check for an existing MR for this source branch before retrying an ambiguous/failed request to avoid duplicates.
7. If push or MR creation fails, report exactly what succeeded (local commit, pushed branch, or created MR), preserve the branch/worktree, and ask for the missing access/action. Never fabricate an MR URL or claim that a successful push alone created it.

## Stop and escalate

- Stop remote writes when applicable deployment/change-authorization sources,
  approvals, repository identity, or production target coverage are missing or
  contradictory. Ask for the specific missing evidence or owner decision.
- Preserve existing user work. If a clean branch/worktree cannot be established,
  synchronization fails, or a concurrent release changes the relevant refs, stop
  rather than reset, overwrite, or force-push.
- Unknown image repository/tag mapping or registry access errors block ref edits.
  Confirmed missing images require the explicit post-warning decision described
  in the preflight; all other gates still apply.
- Stop on unrelated validation failures, merge conflicts, or insufficient access.
  Report partial outcomes accurately and preserve the branch/worktree for recovery.
- Never treat this skill, an opened MR, or successful static checks as authorization
  to merge, proof of live rollout, or operational certification of this draft.

## Expected output

Keep the final response short. On success:

```text
MR: <actual merge-request URL>
Service: <service> · Commit: <full application SHA>
Production targets: <CRCP and HCCP, or verified applicable targets>
Images: <verified image references, or 🔴 missing images explicitly overridden by user>
Validation: <checks/results>
Awaiting review/merge; live rollout has not been verified.
```

Retain the 🔴 missing-image warning if an override was used; do not imply image validation passed. Mention the worktree path if a separate one was created. Leave the deployment branch available for review; do not clean up the user's original checkout or remove a worktree unexpectedly.
