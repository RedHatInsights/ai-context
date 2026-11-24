#!/usr/bin/env python3
"""Prepare one reviewable snapshot/map from the work manifest. Never calls Jira."""

import argparse
import json
import sys

from git_audit import (AuditError, full_sha, git, load_json, repo_identity,
                       safe_error, save_json, sync_repositories, text)

try:
    import yaml
except ImportError:
    yaml = None  # inventory/sync/check and the manual mapping path need only Git.

SAAS = "/app-sre/saas-file-2.yml"


if yaml:
    class SafeLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
        def construct_mapping(self, node, deep=False):
            self.flatten_mapping(node)
            value = super().construct_mapping(node, deep=deep)
            if len(value) != len(node.value):
                raise AuditError("Duplicate or overridden YAML key; review the effective configuration")
            return value


def data_path(reference):
    if not isinstance(reference, dict) or set(reference) != {"$ref"}:
        raise AuditError("Expected a single static app-interface $ref")
    path = text(reference["$ref"], "$ref path")
    if (not path.startswith("/") or any(p in ("", ".", "..") for p in path[1:].split("/"))
            or any(c in path for c in "#:\\\x00")):
        raise AuditError("Unsupported data reference; resolve it at the pinned revision")
    return "data" + path


def flag(item, key):
    value = item.get(key, False)
    if not isinstance(value, bool):
        raise AuditError(f"Ambiguous {key} flag")
    return value


class TargetResolver:
    """Static SaaS v2 candidates, NOT a completeness oracle or a live rollout check."""

    def __init__(self, snapshot, timeout=180):
        self.path = snapshot["path"]
        self.sha = full_sha(snapshot["default_sha"])
        self.timeout = timeout
        self.files = {}

    def document(self, path, schema):
        if path not in self.files:
            blob = git(self.path, "cat-file", "blob", f"{self.sha}:{path}", timeout=self.timeout).stdout
            try:
                self.files[path] = yaml.load(blob, Loader=SafeLoader)
            except (yaml.YAMLError, AuditError, ValueError, RecursionError) as exc:
                # Parser exceptions may quote sensitive lines from unrelated fields.
                raise AuditError(f"{path}: unsupported or ambiguous YAML") from exc
        doc = self.files[path]
        if not isinstance(doc, dict) or doc.get("$schema") != schema:
            raise AuditError(f"Unexpected schema at {path}; expected {schema}")
        return doc

    def target(self, item, selector, index):
        source = f"{selector}.targets[{index}]"
        if not isinstance(item, dict):
            raise AuditError("Invalid target")
        if "$ref" in item:
            path = data_path(item)
            item = self.document(path, "/app-sre/saas-file-target-1.yml")
            source += f" -> {path}"
        if flag(item, "disable") or flag(item, "delete"):
            return "excluded", {"source": source, "reason": "disabled/deleted target"}
        if "namespaceSelector" in item or item.get("provider", "static") != "static":
            raise AuditError("Dynamic namespace selector; resolve its full target set")
        ns_path = data_path(item.get("namespace"))
        ns = self.document(ns_path, "/openshift/namespace-1.yml")
        if flag(ns, "delete"):
            return "excluded", {"source": source, "reason": f"namespace marked for deletion: {ns_path}"}
        env_path = data_path(ns.get("environment"))
        env = self.document(env_path, "/app-sre/environment-1.yml")
        phase = env.get("servicePhase")
        if phase in ("dev", "stage"):
            return "excluded", {"source": source, "reason": f"{env_path}: servicePhase={phase}"}
        if phase != "prod":
            raise AuditError(f"Unresolved environment at {env_path}")
        cluster_path = data_path(ns.get("cluster"))
        cluster = text(self.document(cluster_path, "/openshift/cluster-1.yml").get("name"), "cluster name")
        namespace = text(ns.get("name"), "namespace name")
        return "targets", {"name": f"{selector}/{cluster}/{namespace}", "source": source + ".ref",
                           "sha": full_sha(item.get("ref")), "namespace": ns_path,
                           "environment": env_path, "cluster": cluster_path}

    def resolve(self, identity):
        result = {"app_interface_sha": self.sha, "templates": {}, "blockers": []}
        # Search the *tree*, not the checkout. Do not stop at the first Insights
        # hit: another file anywhere in data/ may add a production deployment.
        grep = git(self.path, "grep", "--all-match", "-ilzF", "-e", SAAS, "-e", identity.rsplit("/", 1)[-1],
                   self.sha, "--", "data", timeout=self.timeout, check=False)
        if grep.returncode not in (0, 1):
            raise AuditError("Could not search the pinned app-interface tree")
        paths = [hit.split(":", 1)[1] for hit in grep.stdout.split("\0") if hit]
        for path in sorted(paths, key=lambda p: (not p.startswith("data/services/insights/"), p)):
            try:
                doc = self.document(path, SAAS)
                templates = doc.get("resourceTemplates")
                if not isinstance(templates, list):
                    raise AuditError("Missing resourceTemplates")
                for rt in templates:
                    if not isinstance(rt, dict):
                        raise AuditError("Invalid resource template")
                    if repo_identity(rt.get("url")) != identity:
                        continue
                    selector = f"{path}#{text(rt.get('name'), 'template name')}"
                    if selector in result["templates"]:
                        raise AuditError(f"Ambiguous template: {selector}")
                    entry = {"path": text(rt.get("path"), "resource template path"),
                             "targets": [], "excluded": [], "blockers": []}
                    result["templates"][selector] = entry
                    targets = rt.get("targets")
                    if not isinstance(targets, list) or not targets:
                        entry["blockers"].append(f"{selector}: empty/invalid targets")
                        continue
                    for i, item in enumerate(targets):
                        try:
                            group, evidence = self.target(item, selector, i)
                            entry[group].append(evidence)
                        except (AuditError, ValueError) as exc:
                            entry["blockers"].append(f"{selector}.targets[{i}]: {safe_error(exc)}")
            except (AuditError, ValueError) as exc:
                result["blockers"].append(f"{path}: {safe_error(exc)}")
        return result


def prepare(work, timeout=180):
    if yaml is None:
        raise AuditError("PyYAML unavailable. Use git_audit.py sync and review pinned YAML manually.")
    config_id = text(work.get("app_interface_repository"), "app-interface repository id")
    specs = work.get("repositories")
    if not isinstance(specs, list) or not all(isinstance(s, dict) for s in specs):
        raise AuditError("Expected a repositories list")
    ids = [text(s.get("id"), "repository id") for s in specs]
    if len(ids) != len(set(ids)):
        raise AuditError("Duplicate repository id in work manifest")
    configs = [s for s in specs if s["id"] == config_id]
    if len(configs) != 1:
        raise AuditError("Expected one verified app-interface checkout")
    snapshots = sync_repositories({"repositories": configs}, timeout)
    config = snapshots["repositories"][config_id]
    if "error" in config:
        return snapshots  # No release proof can use a failed configuration snapshot.
    resolver = TargetResolver(config, timeout)
    maps = snapshots["target_maps"] = {}
    services = []
    for spec in specs:
        if spec.get("id") == config_id:
            continue
        try:
            identity = text(spec.get("expected_identity"), "expected repository identity")
            extra = spec.get("required_commits", [])
            if not isinstance(extra, list):
                raise AuditError("required_commits must be a list of full SHAs")
            commits = {full_sha(oid) for oid in extra}
        except AuditError as exc:
            snapshots["repositories"][spec["id"]] = {"error": safe_error(exc)}
            continue
        if identity not in maps:
            try:
                maps[identity] = resolver.resolve(identity)
            except AuditError as exc:
                maps[identity] = {"app_interface_sha": resolver.sha, "templates": {}, "blockers": [safe_error(exc)]}
        # Include off-default production history in at most one supplemental fetch.
        commits.update(t["sha"] for rt in maps[identity]["templates"].values() for t in rt["targets"])
        services.append({**spec, "required_commits": sorted(commits)})
    snapshots["repositories"].update(sync_repositories({"repositories": services}, timeout)["repositories"])
    return snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", help="work manifest with verified repository identities and paths")
    parser.add_argument("--output", required=True, help="private snapshot and target-map JSON")
    parser.add_argument("--timeout", type=int, default=180, help="timeout per Git command, in seconds")
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise AuditError("Timeout must be positive")
        snapshots = prepare(load_json(args.work), args.timeout)
        output = save_json(args.output, snapshots)
        errors = {name: s["error"] for name, s in snapshots["repositories"].items() if "error" in s}
        print(json.dumps({"output": output, "repository_errors": errors}))
        return int(bool(errors))
    except (AuditError, OSError, ValueError) as exc:
        print(json.dumps({"error": safe_error(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
