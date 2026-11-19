# Contributing

## Onboard a platform area

1. Check repository visibility before adding internal metadata or links.
2. Populate source entries following [the directory guide](directory/README.md).
3. Add real service records using [the template](templates/service.json), including
   ownership, known environments, and topic-specific source mappings.
4. Update or add a skill with verified decision points. Keep implementation facts
   in canonical sources; do not duplicate architecture documents.
5. Test retrieval with an agent's actual tools/access.
6. Have the owning team review it. Only then set catalog/source status to `reviewed`,
   assign a real `owner`, and record `last_verified` as `YYYY-MM-DD`.

## Add or modify a skill

Copy [the skill template](templates/SKILL.md.template) to
`skills/<lowercase-hyphenated-name>/SKILL.md`. Use matching folder and frontmatter
names. This repository intentionally uses only single-line, unquoted `name` and
`description` frontmatter; avoid colons followed by spaces in these scalar values.
Describe when to select the skill, not just what it is called.

- Link the shared baseline, policies, and directory from the skill itself.
- Keep required context small. Use conditional references for deeper facts.
- Separate read-only diagnosis, local changes, and remote mutation.
- Include evidence-based branching, verification, stop conditions, and output.
- Register the skill in [catalog.json](catalog.json), initially as `draft` with
  null owner/date if unassigned.
- Run validation and tests; manually review source accuracy and access.

Catalog and source dates are verification dates, not "last touched" dates. When
behavior/contracts change, re-review affected material; mark it `draft` when its
applicability is uncertain. Link superseded material to its replacement instead
of leaving conflicting instructions active.

## Review checklist

- Is the procedure grounded in current platform behavior rather than generic guesses?
- Are source applicability, owners, and retrieval methods explicit?
- Can agents distinguish missing information from a safe default?
- Are permissions enforced in the tool/CI layer rather than only described here?
- Are failure/recovery and verification paths covered?
- Are secrets, sensitive logs, and customer data absent?

Run locally (Python 3.10+):

```sh
python3 scripts/validate.py
python3 -m unittest discover -s tests
```

The GitHub Actions workflow performs offline structural checks, not behavioral or
operational certification. Remote URL accessibility must be tested separately
using approved credentials; CI intentionally has no internal-system credentials.

## Repository governance to configure on GitHub

Assign actual owning teams before adding `CODEOWNERS`; this starter does not guess
team names. Configure required reviews, branch protection, and a required
`Validate playbooks` check. Require operational owners to review deployment policy
changes. Those repository settings are not created by committing these files.
