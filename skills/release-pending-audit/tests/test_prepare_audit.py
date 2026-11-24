"""Synthetic SaaS trees and file:// Git remotes; no Jira, cluster or host access."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import git_audit as audit
import prepare_audit as prep
from test_git_audit import run_git


class PrepareTests(unittest.TestCase):
    bundle = "data/services/insights/shared/deploy.yml"
    other = "data/services/elsewhere/app.yaml"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                          "GIT_CONFIG_COUNT": "0", "GIT_TERMINAL_PROMPT": "0"})
        env.start()
        self.addCleanup(env.stop)
        self.app, self.service = self.root / "config", self.root / "service"
        for path in (self.app, self.service):
            path.mkdir()
            run_git(path, "init", "-q", "--initial-branch=trunk")
        self.base = self.commit(self.service, "base")
        (self.service / "feature.txt").write_text("landed work\n")
        self.tip = self.commit(self.service, "landed")
        for phase in ("prod", "stage"):
            self.data(f"data/env/{phase}.yml", {"$schema": "/app-sre/environment-1.yml", "servicePhase": phase})
        for name, phase in (("a", "prod"), ("b", "prod"), ("misleading-prod-name", "stage")):
            self.data(f"data/cluster/{name}.yml", {"$schema": "/openshift/cluster-1.yml", "name": name})
            self.data(f"data/ns/{name}.yml", {"$schema": "/openshift/namespace-1.yml", "name": "service",
                      "cluster": {"$ref": f"/cluster/{name}.yml"}, "environment": {"$ref": f"/env/{phase}.yml"}})
        self.data("data/shared/b.yml", {"$schema": "/app-sre/saas-file-target-1.yml", **self.target("b", self.tip)})
        self.application = self.template("app", [self.target("a", self.tip), {"$ref": "/shared/b.yml"},
                                                 self.target("misleading-prod-name", "main"),
                                                 {"disable": True, "ref": "internal"}])
        self.dashboard = self.template("dashboard", [self.target("a", self.base)])
        self.write_bundle()
        self.config_sha = self.commit(self.app, "initial configuration")
        for source in (self.app, self.service):
            run_git(self.root, "clone", "-q", source.as_uri(), str(self.root / (source.name + "-clone")))
        self.req = {"url": "https://github.com/example/service/pull/1", "repository": "service",
                    "merged": True, "landed_commits": [self.tip], "targets_complete": True}
        self.work = {"app_interface_repository": "config", "app_interface_sha": self.config_sha,
                     "repositories": [{"id": p.name, "path": str(self.root / (p.name + "-clone")),
                                       "expected_identity": p.as_uri()} for p in (self.app, self.service)],
                     "tickets": [{"key": "X-1", "url": "https://jira.example.com/browse/X-1",
                                  "scope_complete": True, "requirements": [self.req]}]}

    @staticmethod
    def commit(path, label):
        run_git(path, "add", ".")
        run_git(path, "commit", "--allow-empty", "-qm", label)
        return run_git(path, "rev-parse", "HEAD")

    def data(self, path, document):
        file = self.app / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(document, indent=2) if isinstance(document, dict) else document)

    def template(self, name, targets):
        return {"name": name, "url": self.service.as_uri(), "path": "/" + name + ".yaml", "targets": targets}

    @staticmethod
    def target(namespace, sha):
        return {"namespace": {"$ref": f"/ns/{namespace}.yml"}, "ref": sha}

    def write_bundle(self):
        decoy = {**self.application, "name": "foreign", "url": "https://example.invalid/fork/service"}
        self.data(self.bundle, {"$schema": prep.SAAS, "resourceTemplates": [self.application, decoy, self.dashboard]})

    def status(self, snapshots):
        return audit.check_tickets(self.work, snapshots)["tickets"][0]

    def resolve(self):
        pin = self.commit(self.app, "update")
        return prep.TargetResolver({"path": self.app, "default_sha": pin}).resolve(self.service.as_uri())

    def test_pinned_bundle_scope_and_partial_batch(self):
        # A dirty checkout (including staged or untracked YAML) is not the source of truth.
        clone = self.root / "config-clone"
        (clone / self.bundle).write_text("uncommitted config")
        missing = {"id": "absent", "path": str(self.root / "missing"), "expected_identity": "example.invalid/missing"}
        self.work["repositories"].append(missing)
        other = copy.deepcopy(self.work["tickets"][0])
        other.update(key="X-2", requirements=[{**self.req, "repository": "absent"}])
        self.work["tickets"].append(other)
        snapshots = prep.prepare(self.work)
        mapping = snapshots["target_maps"][self.service.as_uri()]
        self.assertEqual(mapping["blockers"], [])
        self.assertEqual(set(mapping["templates"]), {self.bundle + "#app", self.bundle + "#dashboard"})
        rt = mapping["templates"][self.bundle + "#app"]
        self.assertEqual(len(rt["excluded"]), 2)
        self.assertEqual({t["cluster"] for t in rt["targets"]}, {"data/cluster/a.yml", "data/cluster/b.yml"})
        self.assertTrue(any("data/shared/b.yml" in t["source"] for t in rt["targets"]))
        self.assertEqual(self.status(snapshots)["status"], "Not released")  # dashboard behind
        self.req["exclude_templates"] = {self.bundle + "#dashboard": "Monitoring; ticket requires app only"}
        rows = audit.check_tickets(self.work, snapshots)["tickets"]
        self.assertEqual([r["status"] for r in rows], ["Released", "Needs clarification"])
        self.assertEqual(len(rows[0]["checks"]), 2)
        self.assertEqual((clone / self.bundle).read_text(), "uncommitted config")
        for bad in ({self.bundle + "#dashboard": ""}, {"old-path#dashboard": "old selector"}):
            self.req["exclude_templates"] = bad
            self.assertEqual(self.status(snapshots)["status"], "Needs clarification")

    def test_new_target_outside_insights_invalidates_old_proof(self):
        self.req["exclude_templates"] = {self.bundle + "#dashboard": "Unrelated component"}
        self.assertEqual(self.status(prep.prepare(self.work))["status"], "Released")
        self.data(self.other, {"$schema": prep.SAAS, "resourceTemplates": [self.template("app-b", [self.target("b", self.base)])]})
        new_pin = self.commit(self.app, "another production deployment")
        refreshed = prep.prepare(self.work)
        self.assertEqual(self.status(refreshed)["status"], "Needs clarification")  # stale review pin
        self.work["app_interface_sha"] = new_pin
        self.assertEqual(self.status(refreshed)["status"], "Not released")
        refreshed["target_maps"][self.service.as_uri()]["app_interface_sha"] = self.config_sha
        self.assertEqual(self.status(refreshed)["status"], "Needs clarification")

    def test_dynamic_missing_escaping_and_mutable_targets_cannot_partially_pass(self):
        self.application["targets"].extend([
            self.target("a", "main"), self.target("unknown", self.tip), {"$ref": "/../outside.yml"},
            {"namespaceSelector": {"include": ["*"]}, "ref": self.tip},
            {**self.target("a", self.tip), "disable": "false"},
        ])
        self.write_bundle()
        self.work["app_interface_sha"] = self.commit(self.app, "unresolved production coverage")
        snapshots = prep.prepare(self.work)
        rt = snapshots["target_maps"][self.service.as_uri()]["templates"][self.bundle + "#app"]
        self.assertEqual(len(rt["targets"]), 2)
        self.assertEqual(len(rt["blockers"]), 5)
        self.assertEqual(self.status(snapshots)["status"], "Needs clarification")

    def test_duplicate_yaml_key_is_not_last_ref_wins(self):
        self.data("data/shared/b.yml", f"""$schema: /app-sre/saas-file-target-1.yml
namespace: {{$ref: /ns/b.yml}}
ref: {self.base}
ref: {self.tip}
""")
        mapping = self.resolve()
        blockers = mapping["templates"][self.bundle + "#app"]["blockers"]
        self.assertTrue(any("ambiguous YAML" in b for b in blockers), blockers)

    def test_no_candidate_is_not_merge_equals_release(self):
        (self.app / self.bundle).unlink()
        self.work["app_interface_sha"] = self.commit(self.app, "remove definition")
        snapshots = prep.prepare(self.work)
        self.assertEqual(self.status(snapshots)["status"], "Needs clarification")
        self.req["targets"] = [{"name": "reviewed policy target", "source": "policy evidence", "sha": self.tip}]
        self.assertEqual(self.status(snapshots)["status"], "Needs clarification")  # no mapping justification

    def test_allowlisted_merge_policy_uses_refreshed_default_and_checks_for_reverts(self):
        key = "merge_equals_release_repositories"
        self.req["release_policy"] = key
        snapshots = prep.prepare(self.work)
        self.assertEqual(self.status(snapshots)["status"], "Needs clarification")  # not on real list
        with mock.patch.object(audit, "load_json", return_value={key: [self.service.as_uri()]}):
            row = self.status(snapshots)
            self.assertEqual(row["status"], "Released")
            self.assertIn("merge-equals-release", row["reasons"][0])
            self.assertEqual(row["checks"][0]["production_sha"], self.tip)
            run_git(self.service, "revert", "--no-edit", self.tip)
            row = self.status(prep.prepare(self.work))
            self.assertEqual(row["status"], "Needs clarification")
            self.assertTrue(row["checks"][0]["possible_reverts"])

    def test_undeployed_policy_needs_independent_evidence_and_no_conflicting_map(self):
        self.req.update(release_policy="merge_equals_release_when_undeployed",
                        deployment_absence_evidence="Reviewed applicable deployment inventory",
                        package_evidence="Reviewed package identity and publishing contract on pinned default")
        self.assertEqual(self.status(prep.prepare(self.work))["status"], "Needs clarification")
        (self.app / self.bundle).unlink()
        self.work["app_interface_sha"] = self.commit(self.app, "no deployment")
        snapshots = prep.prepare(self.work)
        self.assertEqual(self.status(snapshots)["status"], "Released")
        for field in ("package_evidence", "deployment_absence_evidence"):
            value = self.req.pop(field)
            self.assertEqual(self.status(snapshots)["status"], "Needs clarification")
            self.req[field] = value

    def test_off_default_prod_commit_is_fetched_by_sha_not_misclassified_as_missing(self):
        run_git(self.service, "switch", "-qc", "prod-line", self.base)
        off_default = self.commit(self.service, "independent production history")
        run_git(self.service, "switch", "-q", "trunk")
        self.data("data/shared/b.yml", {"$schema": "/app-sre/saas-file-target-1.yml", **self.target("b", off_default)})
        self.work["app_interface_sha"] = self.commit(self.app, "pin off-default target")
        self.req["exclude_templates"] = {self.bundle + "#dashboard": "Unrelated monitoring"}
        row = self.status(prep.prepare(self.work))
        self.assertEqual(row["status"], "Not released")
        self.assertEqual({c["production_sha"]: c["included"] for c in row["checks"]}, {self.tip: True, off_default: False})

    def test_cli_saves_maps_privately_and_offline_check_needs_no_yaml_parser(self):
        run = self.root / "run"
        work = self.root / "work.json"
        work.write_text(json.dumps(self.work))
        snapshots = run / "snapshots.json"
        cmd = [sys.executable, "-B", prep.__file__, str(work), "--output", str(snapshots)]
        output = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(output.stdout)["repository_errors"], {})
        self.assertEqual(snapshots.stat().st_mode & 0o777, 0o600)
        check = [sys.executable, "-BS", audit.__file__, "check", str(work), "--snapshots", str(snapshots)]
        result = subprocess.run(check, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["tickets"][0]["status"], "Not released")
        with mock.patch.object(prep, "yaml", None), self.assertRaisesRegex(audit.AuditError, "manually"):
            prep.prepare(self.work)
