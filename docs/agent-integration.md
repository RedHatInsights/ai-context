# Agent integration

## Contract for every harness

1. Make a reviewed checkout of the **whole repository** available through file
   tools, or provide an authenticated retrieval mechanism that preserves paths.
2. Load the baseline behavior at startup through the harness's supported instruction
   mechanism. Advertise skill names/descriptions without loading all skill bodies.
3. On a matching task, load the selected `SKILL.md` and its required reading.
4. Retrieve task-specific sources through authorized tools. Report failures honestly.
5. Record the playbook revision used for consequential work.

Downloading a repository is not the same as loading instructions. A link in
`AGENTS.md` does not guarantee a harness will fetch it. Test discovery and activation
in each harness. `catalog.json` is a local convention; it is not auto-discovered by
Agent Skills implementations. Standalone skill loading remains useful because
each skill explicitly loads the shared baseline and policies.

## Pi: try the local checkout without changing settings

Run from the application repository where you intend to work. Replace the path:

```sh
export AI_CONTEXT=/absolute/path/to/ai-context
pi --skill "$AI_CONTEXT/skills" \
   --append-system-prompt "$AI_CONTEXT/principles/engineering-behavior.md"
```

Inspect startup diagnostics and explicitly invoke a skill to test loading:

```text
/skill:release-pending-audit Run the release audit; ask me for the scope first.
```

Use `/reload` after editing skills. Flags and commands above follow the local Pi
1.5.2 documentation; check `pi --help` for your installed version.

## Pi: optional persistent package installation

This repository's `package.json` exposes `./skills` as a Pi package. From a
consumer application repository, after reviewing the content:

```sh
pi install /absolute/path/to/ai-context --local
```

This changes that application's `.pi/settings.json`; it is not needed for the
one-session approach. For shared use after publishing these changes, a Git source
can be installed instead. Pin a reviewed commit or release tag rather than moving
all agents silently with the default branch:

```text
pi install git:github.com/tahmidefaz/ai-context@<reviewed-commit-or-tag> --local
```

Use your actual Git remote if this repository moves. Private Git sources need
working Git authentication; do not embed credentials in the source string.

Package installation advertises skills but does **not** automatically load the
package root's `AGENTS.md` or baseline behavior in other repositories. Each skill
loads its required reading on activation. If baseline behavior must apply before
skill selection, retain the startup flag or configure consumer instructions below.
No extensions, operational scripts, dependencies, or installation hooks are bundled.

## Other harnesses and consumer bootstrap

Use the harness's supported skill-path configuration pointing at this checkout's
`skills/` directory. Preserve sibling directories. If a harness only copies
individual skill folders, its adapter must also make the referenced shared files
available at the same relative paths, or rewrite references to an accessible
pinned source. Do not copy just the three `SKILL.md` files.

For harnesses without skill support, put an adapted version of this instruction in
the consuming repository's supported agent-instruction file. Replace the path
with one the harness can actually read:

```text
Platform engineering playbooks are available at /absolute/path/to/ai-context.
Before platform work, read principles/engineering-behavior.md under that root.
Use catalog.json to select the relevant skill, then read its SKILL.md and required
reading. Resolve its relative links from the skill directory. Retrieve platform
facts through directory/README.md and the service/source registries; do not invent
missing contracts or operational commands. Missing required procedures or approval
block remote changes. Report unavailable files or tools rather than claiming access.
```

## Cache, updates, and trust

A local clone can cache everything without putting everything into model context.
Refresh at a deliberate boundary, not halfway through a consequential workflow.
Record `git -C "$AI_CONTEXT" rev-parse HEAD`; if the checkout is dirty, record that
and the relevant diff in a safe review artifact. A commit alone does not describe
uncommitted instructions. Pinned playbooks still require checking the target's
actual version and current approved operational policy.

Keep private material within its access boundary. Review source changes before
updating installed skills. Repository policy cannot enforce tool permissions;
configure scoped credentials and deployment gates independently.

## Smoke test

Start a fresh session in a consumer repository, invoke a skill explicitly, and ask
it to identify the selected skill, shared rules, source registry, and missing
service data. It should find the shared files without assuming they live in the
consumer repository. A successful install alone does not prove the agent follows
the workflow; verify observable behavior on a real task.
