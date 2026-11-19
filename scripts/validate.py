#!/usr/bin/env python3
"""Offline checks for this repository's deliberately small authoring conventions."""

import datetime as dt
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEADINGS = (
    "Required reading", "When to use", "Inputs and prerequisites",
    "Procedure", "Stop and escalate", "Expected output",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(text(item) for item in value)


def slug(value):
    return text(value) and len(value) <= 64 and SLUG.fullmatch(value)


def local_file(root, location, base=None):
    require(text(location), "file reference must be a nonempty string")
    parsed = urlsplit(location)
    require(not parsed.scheme and not parsed.netloc and not Path(parsed.path).is_absolute(),
            f"expected a repository-relative path: {location}")
    target = ((base or root) / unquote(parsed.path)).resolve()
    require(target.is_relative_to(root.resolve()), f"path escapes repository: {location}")
    require(target.is_file(), f"missing file: {location}")
    return target


def https_url(value):
    require(text(value) and not any(c.isspace() for c in value),
            "URL must be a nonempty string without whitespace")
    parsed = urlsplit(value)
    require(parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password, f"expected an HTTPS URL without credentials: {value}")


def review_metadata(entry, statuses):
    require(entry.get("status") in statuses, f"status must be one of {sorted(statuses)}")
    require("owner" in entry and "last_verified" in entry, "owner/last_verified fields required")
    owner, date = entry["owner"], entry["last_verified"]
    require(owner is None or text(owner), "owner must be a nonempty string or null")
    if date is not None:
        require(isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date),
                "last_verified must be YYYY-MM-DD or null")
        dt.date.fromisoformat(date)
    if entry["status"] == "reviewed":
        require(text(owner) and date is not None, "reviewed entries need owner and verification date")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate(root=ROOT):
    root = Path(root).resolve()
    errors, warnings = [], []

    def check(label, action):
        try:
            action()
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append(f"{label}: {exc}")

    def load(relative):
        result = {}

        def read():
            data = json.loads((root / relative).read_text(), object_pairs_hook=unique_object)
            require(isinstance(data, dict), "expected a JSON object")
            result.update(data)
        check(relative, read)
        return result

    def records(data, key):
        result = []

        def read():
            require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
                    "schema_version must be 1")
            require(isinstance(data.get(key), list), f"{key} must be an array")
            seen = set()
            for entry in data[key]:
                require(isinstance(entry, dict) and slug(entry.get("id")), "invalid record ID")
                require(entry["id"] not in seen, f"duplicate ID: {entry['id']}")
                seen.add(entry["id"])
                result.append(entry)
        check(key, read)
        return result

    catalog = load("catalog.json")
    skills = records(catalog, "skills")
    sources = records(load("directory/sources.json"), "sources")
    services = records(load("directory/services.json"), "services")
    source_ids = {item["id"] for item in sources}
    service_ids = {item["id"] for item in services}

    for key, expected in (("baseline", "principles/engineering-behavior.md"),
                          ("services", "directory/services.json"),
                          ("sources", "directory/sources.json")):
        def catalog_reference(key=key, expected=expected):
            require(catalog.get(key) == expected, f"must point to {expected}")
            local_file(root, expected)
        check(f"catalog.{key}", catalog_reference)

    for entry in sources:
        def source(entry=entry):
            review_metadata(entry, {"missing", "draft", "reviewed"})
            for key in ("title", "use_when", "scope", "access"):
                require(text(entry.get(key)), f"{key} required")
            require("location" in entry, "location field required")
            location = entry["location"]
            if entry["status"] == "missing":
                require(location is None, "missing sources must have a null location")
            elif text(location) and urlsplit(location).scheme:
                https_url(location)
            else:
                local_file(root, location)
            if entry["status"] != "reviewed":
                warnings.append(f"source {entry['id']}: {entry['status']}")
        check(f"source {entry['id']}", source)

    identities = {}
    for entry in services:
        def service(entry=entry):
            require(entry["id"] != "replace-me", "replace the template service ID")
            for key in ("aliases", "environments", "related_services"):
                require(strings(entry.get(key)), f"{key} must be an array of strings")
            for identity in [entry["id"], *entry["aliases"]]:
                folded = identity.casefold()
                require(folded not in identities, f"ambiguous service ID/alias: {identity}")
                identities[folded] = entry["id"]
            require("owner" in entry and (entry["owner"] is None or text(entry["owner"])),
                    "owner must be a nonempty string or null")
            require("repository" in entry, "repository field required")
            if entry["repository"] is not None:
                https_url(entry["repository"])
            mappings = entry.get("source_ids")
            require(isinstance(mappings, dict), "source_ids must be a topic-to-ID-list object")
            for topic, refs in mappings.items():
                require(text(topic) and strings(refs), "source_ids values must be string arrays")
                require(set(refs) <= source_ids, f"unknown source IDs in topic {topic}")
            require(set(entry["related_services"]) <= service_ids, "unknown related service")
            if not entry["owner"] or not entry["repository"] or not entry["environments"]:
                warnings.append(f"service {entry['id']}: incomplete owner/repository/environments")
        check(f"service {entry['id']}", service)
    if not services:
        warnings.append("no services registered; operational routing is not configured")

    registered_paths = set()
    for entry in skills:
        def skill(entry=entry):
            review_metadata(entry, {"draft", "reviewed"})
            require(text(entry.get("use_when")), "use_when required")
            expected = f"skills/{entry['id']}/SKILL.md"
            require(entry.get("path") == expected, f"path must be {expected}")
            body = local_file(root, expected).read_text()
            registered_paths.add(expected)
            # This repo uses a restricted, dependency-free frontmatter convention,
            # not a general YAML parser. See CONTRIBUTING.md.
            match = re.match(r"\A---\nname: ([^\n]+)\ndescription: ([^\n]+)\n---(?:\n|$)", body)
            require(match is not None, "expected single-line name/description frontmatter")
            require(match[1] == entry["id"], "frontmatter name must match skill ID/folder")
            description = match[2]
            require(text(description) and len(description) <= 1024, "invalid description length")
            require(description[0] not in "'\"[{&*!|>@`%#" and ": " not in description
                    and " #" not in description, "description must be a plain single-line YAML scalar")
            for heading in HEADINGS:
                require(f"## {heading}" in body.splitlines(), f"missing heading: {heading}")
            required = entry.get("required_reading")
            require(strings(required) and required, "required_reading must be nonempty")
            baseline = {"principles/engineering-behavior.md", "policies/data-handling.md",
                        "policies/change-safety.md", "directory/README.md"}
            require(baseline <= set(required), "required_reading must include baseline, policies, and directory")
            linked_files = set()
            for link in re.findall(r"\[[^\]\n]*\]\(([^\s)]+)\)", body):
                if not urlsplit(link).scheme and not link.startswith("#"):
                    linked_files.add(local_file(root, link, (root / expected).parent))
            for reference in required:
                target = local_file(root, reference)
                require(target in linked_files, f"required reading must be linked by skill: {reference}")
            evaluation = entry.get("evaluation")
            if evaluation is not None:
                local_file(root, evaluation)
            if entry["status"] == "draft":
                warnings.append(f"skill {entry['id']}: draft; not operationally approved")
        check(f"skill {entry['id']}", skill)

    discovered = {p.relative_to(root).as_posix() for p in (root / "skills").rglob("SKILL.md")}
    if discovered != registered_paths:
        errors.append(f"skill catalog coverage mismatch: {sorted(discovered ^ registered_paths)}")

    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "node_modules" for part in relative.parts):
            continue

        def links(path=path):
            body = re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", path.read_text())
            for link in re.findall(r"\[[^\]\n]*\]\(([^\s)]+)\)", body):
                if link.startswith("#") or urlsplit(link).scheme:
                    continue
                local_file(root, link, path.parent)
        check(str(relative), links)

    manifest = load("package.json")
    check("Pi manifest", lambda: require(
        isinstance(manifest.get("pi"), dict) and manifest["pi"].get("skills") == ["./skills"],
        "pi.skills must expose ./skills"))
    return errors, warnings


def main():
    errors, warnings = validate()
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"Validation failed: {len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"Validation passed ({len(warnings)} onboarding warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
