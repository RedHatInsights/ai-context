# Service and source directory

This is a routing index, not a copy of architecture documentation. JSON is used
so ordinary agent tools and the dependency-free validator can read it.

## Lookup procedure

1. Find an exact service ID or alias in [services.json](services.json). If an alias
   is ambiguous, resolve it with the requester rather than selecting arbitrarily.
2. Confirm the environment, owner, repository, and version being investigated.
3. Read that service's `source_ids` by topic. Each value is a list of IDs in
   [sources.json](sources.json). Follow source scope to choose the applicable one.
4. Fetch the selected source using approved authenticated tools. Check actual
   contents and version/environment applicability; a registry status is not proof
   that a page is current. Follow its approved references as necessary.
5. Report inaccessible, missing, or contradictory required sources. Do not replace
   them with generic web advice or another service's deployment commands.

For cross-cutting discovery, search source IDs and `use_when` descriptions directly.
Do not assume an unmapped source applies to a service. For a local source path,
resolve it from the repository root. Markdown links elsewhere resolve from their
containing file. HTTPS sources may need different access tooling per harness.

## Service records

Start with [the service template](../templates/service.json). Replace `replace-me`
and fill the record before appending it to the `services` array. The template is
not an actual service and is not loaded as registry data.

- `id`: stable lowercase slug; `aliases`: names engineers use to refer to it.
- `owner`: team/contact routing identifier, or `null` if unknown.
- `repository`: canonical HTTPS repository URL, or `null` if unknown.
- `environments`: known environment names; an empty list means unknown, not all.
- `source_ids`: topic → list of source IDs. Supported initial topics are
  `architecture`, `frontend-integration`, `observability`, `deployment`, and
  `change-authorization`. Add other topics as needed with explicit documentation.
- `related_services`: IDs of registered dependencies/consumers worth inspecting;
  these are navigation hints, not a complete runtime topology.

## Source records

Each source has:

- `id`: stable slug (create service/environment-specific IDs when necessary).
- `title` and `use_when`: what question this source answers and when to read it.
- `location`: canonical HTTPS URL or repository-relative file path; `null` when missing.
- `scope`: applicable service/environment/version, or an explicit statement that
  applicability is not yet established.
- `status`: `missing`, `draft`, or `reviewed`.
- `owner` and `last_verified` (`YYYY-MM-DD`): required for `reviewed`; otherwise may be null.
- `access`: approved access method/prerequisites, without credentials.

The initial entries are **unconfigured source slots**, not assertions that these
specific documents already exist. Replace their metadata or add more scoped entries.
A `reviewed` source was checked by its owner; agents still check applicability at use time.

## Maintenance and access

Update routing when ownership, contracts, deployment mechanisms, or source locations
change. Review dates reflect actual content verification, not a formatting edit.
Do not store restricted URLs/metadata in a publicly readable repository. Check
GitHub visibility and source access with maintainers before onboarding internal links.
