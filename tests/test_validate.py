"""Mutation tests: validate copied fixtures, never alter the working repository."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.validate import ROOT, validate


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def update(self, path, change):
        target = self.root / path
        data = json.loads(target.read_text())
        change(data)
        target.write_text(json.dumps(data))

    def assert_invalid(self, message):
        errors, _ = validate(self.root)
        self.assertTrue(any(message in error for error in errors), errors)

    def test_starter_passes_with_honest_warnings(self):
        errors, warnings = validate(self.root)
        self.assertEqual(errors, [])
        self.assertTrue(any("no services registered" in warning for warning in warnings))
        catalog = json.loads((self.root / "catalog.json").read_text())
        expected_skill_warnings = {
            f"skill {entry['id']}: draft; not operationally approved"
            for entry in catalog["skills"] if entry["status"] == "draft"
        }
        self.assertEqual(
            {warning for warning in warnings if warning.startswith("skill ")},
            expected_skill_warnings,
        )
        self.assertEqual(sum("source " in warning for warning in warnings), 5)

    def test_reviewed_skill_requires_owner_and_date(self):
        self.update("catalog.json", lambda data: data["skills"][0].update(status="reviewed"))
        self.assert_invalid("reviewed entries need owner")

    def test_invalid_verification_date(self):
        self.update("catalog.json", lambda data: data["skills"][0].update(last_verified="2026-02-30"))
        self.assert_invalid("day is out of range")

    def test_missing_source_cannot_have_location(self):
        self.update("directory/sources.json", lambda data: data["sources"][0].update(location="README.md"))
        self.assert_invalid("missing sources must have a null location")

    def test_reviewed_source_requires_location(self):
        self.update("directory/sources.json", lambda data: data["sources"][0].update(
            status="reviewed", owner="fixture-team", last_verified="2026-01-01"))
        self.assert_invalid("file reference must be a nonempty string")

    def test_reviewed_source_can_use_a_local_file(self):
        self.update("directory/sources.json", lambda data: data["sources"][0].update(
            status="reviewed", owner="fixture-team", last_verified="2026-01-01", location="README.md"))
        self.assertEqual(validate(self.root)[0], [])

    def test_broken_markdown_link(self):
        with (self.root / "README.md").open("a") as handle:
            handle.write("\n[Missing](missing.md)\n")
        self.assert_invalid("missing file: missing.md")

    def test_path_escape(self):
        self.update("catalog.json", lambda data: data["skills"][0].update(evaluation="../outside.md"))
        self.assert_invalid("path escapes repository")

    def test_duplicate_source_id(self):
        self.update("directory/sources.json", lambda data: data["sources"].append(data["sources"][0]))
        self.assert_invalid("duplicate ID")

    def test_malformed_json_is_reported(self):
        (self.root / "catalog.json").write_text("{")
        self.assert_invalid("catalog.json:")

    def test_duplicate_json_keys_are_reported(self):
        (self.root / "directory/services.json").write_text(
            '{"schema_version": 1, "services": [], "services": []}')
        self.assert_invalid("duplicate JSON key")

    def test_malformed_record_is_reported(self):
        self.update("directory/services.json", lambda data: data["services"].append("not an object"))
        self.assert_invalid("invalid record ID")

    def add_service(self, **overrides):
        service = json.loads((self.root / "templates/service.json").read_text())
        service.update(id="sample-service", **overrides)
        self.update("directory/services.json", lambda data: data["services"].append(service))

    def test_unknown_source_reference(self):
        self.add_service(source_ids={"deployment": ["does-not-exist"]})
        self.assert_invalid("unknown source IDs")

    def test_ambiguous_service_alias(self):
        self.add_service(aliases=["SAMPLE-SERVICE"])
        self.assert_invalid("ambiguous service ID/alias")

    def test_unknown_related_service(self):
        self.add_service(related_services=["does-not-exist"])
        self.assert_invalid("unknown related service")

    def test_credentials_in_url(self):
        self.add_service(repository="https://user:secret@example.invalid/repo")
        self.assert_invalid("HTTPS URL without credentials")

    def test_valid_service_mapping(self):
        self.add_service(owner="fixture-team", repository="https://example.invalid/sample",
                         environments=["fixture"], source_ids={"architecture": ["platform-architecture"]})
        self.assertEqual(validate(self.root)[0], [])

    def test_unregistered_skill(self):
        directory = self.root / "skills" / "unregistered"
        directory.mkdir()
        (directory / "SKILL.md").write_text("# Not registered\n")
        self.assert_invalid("skill catalog coverage mismatch")

    def test_skill_frontmatter_name_must_match(self):
        target = self.root / "skills/release-pending-audit/SKILL.md"
        target.write_text(target.read_text().replace("name: release-pending-audit", "name: another-name"))
        self.assert_invalid("frontmatter name must match")

    def test_skill_must_link_required_reading(self):
        target = self.root / "skills/release-pending-audit/SKILL.md"
        target.write_text(target.read_text().replace(
            "[change safety](../../policies/change-safety.md)", "change safety"))
        self.assert_invalid("required reading must be linked by skill")

    def test_skill_requires_stop_conditions(self):
        target = self.root / "skills/release-pending-audit/SKILL.md"
        target.write_text(target.read_text().replace("## Stop and escalate", "## Other notes"))
        self.assert_invalid("missing heading: Stop and escalate")

    def test_manifest_must_expose_skills(self):
        self.update("package.json", lambda data: data.update(pi={"skills": []}))
        self.assert_invalid("pi.skills must expose ./skills")


if __name__ == "__main__":
    unittest.main()
