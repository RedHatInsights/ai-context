"""All integration tests use temporary local repositories, never live services."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "git_audit.py"
SPEC = importlib.util.spec_from_file_location("git_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def run_git(path, *args):
    command = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false",
               "-c", "tag.gpgsign=false", "-c", "user.name=Audit Test",
               "-c", "user.email=audit@example.invalid", "-C", str(path), *args]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()


class IdentityTests(unittest.TestCase):
    def test_github_transport_and_case(self):
        expected = "github.com/example/service"
        for url in ("https://github.com/Example/Service.git",
                    "git@github.com:example/service.git",
                    "ssh://git@github.com:22/example/service",
                    "https://username:secret@github.com/example/service.git"):
            self.assertEqual(audit.repo_identity(url), expected)

    def test_gitlab_subgroups_are_preserved(self):
        self.assertEqual(audit.repo_identity("git@gitlab.example.com:team/subgroup/service.git"),
                         "gitlab.example.com/team/subgroup/service")

    def test_unknown_transports_and_commands_are_rejected(self):
        for url in ("ext::some-command", "ftp://host/repo", "not-a-remote"):
            with self.assertRaises(audit.AuditError):
                audit.repo_identity(url)

    def test_errors_redact_credentials(self):
        message = audit.safe_error("https://user:secret@host/path?access_token=hidden")
        self.assertNotIn("secret", message)
        self.assertNotIn("hidden", message)


class GitAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="release-audit-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        patch = mock.patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0", "GIT_TERMINAL_PROMPT": "0",
        })
        patch.start()
        self.addCleanup(patch.stop)
        self.source = self.root / "source"
        self.remote = self.root / "remote.git"
        self.local = self.root / "checkout"
        self.source.mkdir()
        run_git(self.source, "init", "-q", "--initial-branch=trunk")
        self.base = self.commit("base.txt", "base\n", "base")
        self.landed = self.commit("feature.txt", "implemented\n", "landed work")
        self.tip = self.commit("followup.txt", "next\n", "follow-up")
        run_git(self.root, "init", "--bare", "-q", "--initial-branch=trunk", str(self.remote))
        run_git(self.source, "remote", "add", "origin", self.remote.as_uri())
        self.push()
        run_git(self.root, "clone", "-q", self.remote.as_uri(), str(self.local))
        self.request = {"repositories": [{
            "id": "service", "path": str(self.local), "remote": "origin",
            "expected_identity": self.remote.as_uri(),
        }]}
        self.refresh()

    def commit(self, filename, content, message):
        (self.source / filename).write_text(content, encoding="utf-8")
        run_git(self.source, "add", filename)
        run_git(self.source, "commit", "-q", "-m", message)
        return run_git(self.source, "rev-parse", "HEAD")

    def push(self):
        run_git(self.source, "push", "-q", "origin", "--all")

    def refresh(self):
        self.snapshots = audit.sync_repositories(self.request)
        snapshot = self.snapshots["repositories"]["service"]
        self.assertNotIn("error", snapshot, snapshot)
        self.snapshots["repositories"]["app-interface"] = dict(snapshot)
        self.snapshot = snapshot

    def manifest(self, production=None, landed=None):
        return {
            "app_interface_repository": "app-interface",
            "app_interface_sha": self.snapshot["default_sha"],
            "tickets": [{
                "key": "PROJECT-123", "url": "https://jira.example.com/browse/PROJECT-123",
                "scope_complete": True, "blockers": [],
                "requirements": [{
                    "url": "https://github.com/example/service/pull/12",
                    "repository": "service", "merged": True,
                    "landed_commits": [landed or self.landed], "targets_complete": True,
                    "targets": [{"name": "service/prod/crcp/namespace", "sha": production or self.tip,
                                 "source": "data/prod/service.yml#deployments[service].ref"}],
                }],
            }],
        }

    def result(self, manifest=None):
        return audit.check_tickets(manifest or self.manifest(), self.snapshots)["tickets"][0]

    def assert_status(self, expected, manifest=None):
        result = self.result(manifest)
        self.assertEqual(result["status"], expected, result)
        return result

    def test_equal_commit_is_released(self):
        self.assert_status("Released", self.manifest(production=self.landed))

    def test_descendant_is_released(self):
        self.assert_status("Released")

    def test_older_production_is_not_released(self):
        self.assert_status("Not released", self.manifest(production=self.base))

    def test_newer_divergent_commit_is_not_released(self):
        run_git(self.source, "switch", "-q", "-c", "divergent", self.base)
        other = self.commit("unrelated.txt", "newer unrelated work\n", "newer unrelated commit")
        run_git(self.source, "switch", "-q", "trunk")
        self.push()
        self.refresh()
        # Without an explicit need, neither the divergent branch nor its object is fetched.
        self.assert_status("Needs clarification", self.manifest(production=other))
        self.request["repositories"][0]["required_commits"] = [other, other]
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            self.refresh()
        fetches = [c.args for c in commands.call_args_list if c.args[1] == "fetch"]
        self.assertEqual(len(fetches), 2)
        self.assertEqual(fetches[0][-1], "+refs/heads/trunk:refs/remotes/origin/trunk")
        self.assertEqual(fetches[1].count(other), 1)
        self.assertNotIn("origin/divergent", run_git(self.local, "branch", "-r"))
        self.assert_status("Not released", self.manifest(production=other))

    def test_failed_supplement_blocks_only_work_that_needs_the_object(self):
        missing = "f" * 40
        self.request["repositories"][0]["required_commits"] = [missing]
        self.refresh()
        self.assertIn("supplement_error", self.snapshot)
        self.assert_status("Needs clarification", self.manifest(production=missing))
        self.assert_status("Released")

    def test_every_production_target_must_pass(self):
        document = self.manifest()
        document["tickets"][0]["requirements"][0]["targets"].append({
            "name": "service/prod-multicluster/hccp/namespace", "sha": self.base,
            "source": "data/prod-multicluster/service.yml#ref",
        })
        self.assert_status("Not released", document)

    def test_every_pr_and_landed_commit_must_pass(self):
        document = self.manifest(production=self.landed)
        requirement = copy.deepcopy(document["tickets"][0]["requirements"][0])
        requirement["url"] = "https://gitlab.example.com/team/subgroup/service/-/merge_requests/13"
        requirement["landed_commits"] = [self.landed, self.tip]
        document["tickets"][0]["requirements"].append(requirement)
        self.assert_status("Not released", document)

    def test_missing_or_symbolic_sha_needs_clarification(self):
        for production in ("f" * 40, "HEAD", self.tip[:12]):
            with self.subTest(production=production):
                self.assert_status("Needs clarification", self.manifest(production=production))

    def test_tag_object_is_not_a_commit_sha(self):
        run_git(self.local, "tag", "-a", "production-tag", "-m", "tag")
        tag_sha = run_git(self.local, "rev-parse", "refs/tags/production-tag")
        self.assert_status("Needs clarification", self.manifest(production=tag_sha))

    def test_unknown_or_unmerged_pr(self):
        for merged, expected in ((False, "Not released"), (None, "Needs clarification")):
            document = self.manifest()
            document["tickets"][0]["requirements"][0]["merged"] = merged
            self.assert_status(expected, document)

    def test_incomplete_scope_mapping_or_missing_requirements_blocks_release(self):
        for field in ("scope", "mapping", "requirements", "source", "blocker"):
            document = self.manifest()
            ticket = document["tickets"][0]
            requirement = ticket["requirements"][0]
            if field == "scope":
                ticket.pop("scope_complete")
            elif field == "mapping":
                requirement["targets_complete"] = False
            elif field == "requirements":
                ticket["requirements"] = []
            elif field == "source":
                requirement["targets"][0].pop("source")
            else:
                ticket["blockers"] = ["Unresolved non-code acceptance condition"]
            with self.subTest(field=field):
                self.assert_status("Needs clarification", document)

    def test_stale_app_interface_mapping_blocks_release(self):
        document = self.manifest()
        document["app_interface_sha"] = self.base
        self.assert_status("Needs clarification", document)

    def test_failed_snapshot_blocks_release(self):
        self.snapshots["repositories"]["service"] = {"error": "Authentication failed"}
        self.assert_status("Needs clarification")

    def test_missing_repository_blocks_release(self):
        self.snapshots["repositories"]["service"]["path"] = str(self.root / "missing")
        self.assert_status("Needs clarification")

    def test_squashed_source_sha_is_not_mistaken_for_landed_commit(self):
        run_git(self.source, "switch", "-q", "-c", "feature", self.base)
        original = self.commit("squash.txt", "change\n", "original feature")
        run_git(self.source, "switch", "-q", "trunk")
        run_git(self.source, "merge", "--squash", "feature")
        run_git(self.source, "commit", "-q", "-m", "squashed feature")
        squashed = run_git(self.source, "rev-parse", "HEAD")
        self.push()
        self.refresh()
        self.assert_status("Needs clarification", self.manifest(production=squashed, landed=original))
        self.assert_status("Released", self.manifest(production=squashed, landed=squashed))

    def test_standard_revert_and_revert_of_revert_need_review(self):
        run_git(self.source, "revert", "--no-edit", self.landed)
        reverted = run_git(self.source, "rev-parse", "HEAD")
        self.push()
        self.refresh()
        self.assert_status("Needs clarification", self.manifest(production=reverted))
        run_git(self.source, "revert", "--no-edit", reverted)
        reapplied = run_git(self.source, "rev-parse", "HEAD")
        self.push()
        self.refresh()
        self.assert_status("Needs clarification", self.manifest(production=reapplied))

    def test_git_errors_are_not_treated_as_missing_ancestry(self):
        original_git = audit.git

        def fail_ancestry(path, *args, **kwargs):
            if args[0] == "merge-base":
                return subprocess.CompletedProcess(args, 128, "", "simulated Git failure")
            return original_git(path, *args, **kwargs)

        with mock.patch.object(audit, "git", side_effect=fail_ancestry):
            result = self.assert_status("Needs clarification")
        self.assertTrue(any("Ancestry check failed" in reason for reason in result["reasons"]))

    def test_repeated_tickets_reuse_ancestry_and_revert_checks(self):
        document = self.manifest()
        second = copy.deepcopy(document["tickets"][0])
        second["key"] = "PROJECT-124"
        document["tickets"].append(second)
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            results = audit.check_tickets(document, self.snapshots)
        self.assertTrue(all(row["status"] == "Released" for row in results["tickets"]))
        ancestry = [call for call in commands.call_args_list if call.args[1] == "merge-base"]
        reverts = [call for call in commands.call_args_list if call.args[1] == "log"]
        self.assertEqual(len(ancestry), 1)
        self.assertEqual(len(reverts), 1)
        self.assertFalse(any(call.args[1] in ("fetch", "ls-remote") for call in commands.call_args_list))

    def test_duplicate_checkout_requests_fetch_once(self):
        request = copy.deepcopy(self.request)
        alias = dict(request["repositories"][0], id="same-service")
        request["repositories"].append(alias)
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            result = audit.sync_repositories(request)
        self.assertEqual(result["repositories"]["service"], result["repositories"]["same-service"])
        self.assertEqual(sum(call.args[1] == "fetch" for call in commands.call_args_list), 1)

    def test_only_independent_git_stores_sync_concurrently(self):
        linked = self.root / "linked"
        run_git(self.local, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
        service = self.request["repositories"][0]
        request = {"repositories": [service, dict(service, id="linked", path=str(linked)),
                                    dict(service, id="independent", path=str(self.source))]}
        workers = threading.Barrier(2, timeout=10)

        def sync(path, *args):
            if path != linked:
                workers.wait()
            return {"thread": threading.get_ident()}

        with mock.patch.object(audit, "sync_one", side_effect=sync):
            rows = audit.sync_repositories(request)["repositories"]
        self.assertEqual(rows["service"]["thread"], rows["linked"]["thread"])
        self.assertNotEqual(rows["service"]["thread"], rows["independent"]["thread"])

    def test_conflicting_aliases_do_not_fetch(self):
        request = copy.deepcopy(self.request)
        alias = dict(request["repositories"][0], id="conflicting", default_branch="main")
        request["repositories"][0]["default_branch"] = "trunk"
        request["repositories"].append(alias)
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            result = audit.sync_repositories(request)
        self.assertTrue(all("error" in row for row in result["repositories"].values()))
        self.assertFalse(any(call.args[1] == "fetch" for call in commands.call_args_list))

    def test_remote_identity_mismatch_does_not_fetch(self):
        request = copy.deepcopy(self.request)
        request["repositories"][0]["expected_identity"] = "github.com/somebody/other-repo"
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            result = audit.sync_repositories(request)
        self.assertIn("identity mismatch", result["repositories"]["service"]["error"])
        self.assertFalse(any(call.args[1] == "fetch" for call in commands.call_args_list))

    def test_sync_preserves_dirty_checkout_and_disables_hooks(self):
        run_git(self.local, "switch", "-q", "-c", "local-work")
        (self.local / "base.txt").write_text("staged edit\n", encoding="utf-8")
        run_git(self.local, "add", "base.txt")
        (self.local / "feature.txt").write_text("unstaged edit\n", encoding="utf-8")
        (self.local / "untracked.txt").write_text("local file\n", encoding="utf-8")
        before_status = run_git(self.local, "status", "--porcelain")
        before_head = run_git(self.local, "rev-parse", "HEAD")
        hook_dir = self.root / "hooks"
        hook_dir.mkdir()
        marker = self.root / "hook-ran"
        hook = hook_dir / "reference-transaction"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
        run_git(self.local, "config", "core.hooksPath", str(hook_dir))
        newest = self.commit("remote-only.txt", "remote\n", "new remote work")
        self.push()
        self.refresh()
        self.assertEqual(self.snapshot["default_sha"], newest)
        self.assertEqual(run_git(self.local, "status", "--porcelain"), before_status)
        self.assertEqual(run_git(self.local, "rev-parse", "HEAD"), before_head)
        self.assertEqual(run_git(self.local, "branch", "--show-current"), "local-work")
        self.assertFalse((self.local / "remote-only.txt").exists())
        self.assertFalse(marker.exists())

    def test_remote_head_not_stale_local_head_determines_default(self):
        run_git(self.local, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/nonexistent")
        self.refresh()
        self.assertEqual(self.snapshot["default_branch"], "trunk")

    def test_sync_unshallows_and_check_refuses_shallow_snapshots(self):
        shallow = self.root / "shallow"
        run_git(self.root, "clone", "-q", "--depth=1", self.remote.as_uri(), str(shallow))
        self.snapshots["repositories"]["service"]["path"] = str(shallow)
        self.assert_status("Needs clarification")
        request = copy.deepcopy(self.request)
        request["repositories"][0]["path"] = str(shallow)
        result = audit.sync_repositories(request)
        self.assertNotIn("error", result["repositories"]["service"])
        self.assertEqual(run_git(shallow, "rev-parse", "--is-shallow-repository"), "false")
        self.assertEqual(run_git(shallow, "cat-file", "-t", self.base), "commit")

    def test_promisor_and_graft_repositories_fail_closed(self):
        run_git(self.local, "config", "remote.origin.promisor", "true")
        self.assert_status("Needs clarification")
        run_git(self.local, "config", "--unset", "remote.origin.promisor")
        (self.local / ".git" / "info" / "grafts").write_text(self.landed + "\n", encoding="utf-8")
        self.assert_status("Needs clarification")

    def test_replace_refs_cannot_falsify_ancestry(self):
        run_git(self.source, "switch", "-q", "-c", "other", self.base)
        other = self.commit("other.txt", "other\n", "other branch")
        run_git(self.source, "switch", "-q", "trunk")
        self.push()
        self.request["repositories"][0]["required_commits"] = [other]
        self.refresh()
        tree = run_git(self.local, "rev-parse", f"{other}^{{tree}}")
        fake = run_git(self.local, "commit-tree", tree, "-p", self.landed, "-m", "fake parent")
        run_git(self.local, "replace", other, fake)
        run_git(self.local, "merge-base", "--is-ancestor", self.landed, other)
        self.assert_status("Not released", self.manifest(production=other))

    def test_inventory_is_local_and_normalizes_remotes(self):
        with mock.patch.object(audit, "git", wraps=audit.git) as commands:
            result = audit.inventory(self.root)
        entry = next(row for row in result["repositories"] if row["path"] == str(self.local.resolve()))
        self.assertEqual(entry["remotes"], [{"name": "origin", "identity": self.remote.as_uri()}])
        self.assertFalse(any(call.args[1] in ("fetch", "ls-remote") for call in commands.call_args_list))

    def test_cli_saves_private_evidence_and_prints_only_summary(self):
        checks = self.root / "checks.json"
        snapshots = self.root / "snapshots.json"
        results = self.root / "private" / "results.json"
        checks.write_text(json.dumps(self.manifest()), encoding="utf-8")
        snapshots.write_text(json.dumps(self.snapshots), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = audit.main(["check", str(checks), "--snapshots", str(snapshots), "--output", str(results)])
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertNotIn("checks", summary["tickets"][0])
        full = json.loads(results.read_text(encoding="utf-8"))
        self.assertTrue(full["tickets"][0]["checks"])
        self.assertEqual(stat.S_IMODE(results.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(results.parent.stat().st_mode), 0o700)

    def test_invalid_input_and_duplicate_ids_are_rejected(self):
        request = copy.deepcopy(self.request)
        request["repositories"].append(dict(request["repositories"][0]))
        with self.assertRaises(audit.AuditError):
            audit.sync_repositories(request)
        document = self.manifest()
        document["tickets"].append(copy.deepcopy(document["tickets"][0]))
        with self.assertRaises(audit.AuditError):
            audit.check_tickets(document, self.snapshots)
        invalid = self.root / "invalid.json"
        invalid.write_text("[]", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(audit.main(["sync", str(invalid)]), 2)


if __name__ == "__main__":
    unittest.main()
