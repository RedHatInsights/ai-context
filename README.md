# ai-context

A platform engineering playbook for AI agents working on **console.redhat.com**.
Teach agents how to investigate, find authoritative information, reason across
application boundaries, and prepare safe changes—not just how to describe the platform.

**Status: starter playbook, not an approved operational runbook.** The initial
skills are generic, unverified procedures. Service ownership, documentation URLs,
and deployment authorization must be supplied and reviewed by platform engineers.
Missing information is explicit; no platform topology or deployment commands are assumed.

## Start here

| You want to… | Read |
| --- | --- |
| Use this from Pi or another harness | [Agent integration](docs/agent-integration.md) |
| Route an engineering task | [Skill catalog](catalog.json) |
| Audit whether sprint tickets are released to production | [Release pending audit](skills/release-pending-audit/SKILL.md) |
| Find services, owners, docs, and tools | [Directory guide](directory/README.md) |
| Populate and maintain this repository | [Contributing](CONTRIBUTING.md) |

## How agents should use this repository

1. Load [engineering behavior](principles/engineering-behavior.md).
2. Select a skill using its description or `catalog.json`; read that skill fully.
3. Read its required policies, then resolve the target service and environment.
4. Retrieve only the relevant sources from the directory. A URL is not evidence
   until its contents have actually been read and checked for applicability.
5. Investigate or act within authorization; verify and report evidence and gaps.

Skills reference shared files outside their own directories. **Distribute the
whole repository**, not an isolated `SKILL.md`. The catalog is a repository
convention, not a built-in standard that every agent automatically understands.

## Repository layout

- `skills/`: task procedures in the portable Agent Skills format.
- `principles/`: baseline engineering behavior.
- `policies/`: conservative change and data-handling boundaries.
- `directory/`: service registry and authoritative source registry.
- `platform/`: architecture entry point; link canonical docs rather than duplicating them.
- `debugging/`, `deployment/`: entry points for future platform-specific runbooks.
- `docs/`: integration instructions.
- `templates/`: reusable skill and service templates.
- `scripts/`: offline repository validation; no operational automation.

## First team onboarding

- [ ] Assign real owners and review the baseline rules and policies.
- [ ] Populate `directory/sources.json` with accessible, authoritative URLs.
- [ ] Add two or three connected applications to `directory/services.json`.
- [ ] Review skills against actual tools, contracts, and operational practices.
- [ ] Test link access using the same credentials/tools the agents will use.
- [ ] Mark a skill `reviewed` in `catalog.json` only after owner review.
- [ ] Configure credential scopes, CI/CD approvals, and GitHub branch protection.

A reviewed skill does **not** grant permission to deploy. Until a service's
approved deployment and change-authorization sources are configured and
applicable, deployment preparation must remain in preparation mode.

## Validate locally

Python 3.10+; no third-party dependencies or network access required:

```sh
python3 scripts/validate.py
python3 -m unittest discover -s tests
```

Validation checks structure, required metadata, registry references, skill
coverage, and inline relative Markdown file links. It does not verify remote URLs,
Markdown anchors, operational correctness, authorization, or agent behavior.
Missing sources and draft skills are reported as onboarding warnings.

## Design boundaries

Git is the source of truth. Load small procedures and fetch facts on demand.
Do not store secrets, customer data, raw incident logs, or duplicated architecture
manuals here. Repository instructions are guidance, not a security boundary:
access controls and deployment approvals must be enforced by the tools themselves.
