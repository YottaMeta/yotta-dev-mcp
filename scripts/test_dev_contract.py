#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for the .yotta/architecture.json contract module."""

import json
import tempfile
import unittest
from pathlib import Path

import dev_contract


def valid_contract():
    return {
        "version": 1,
        "project": "demo",
        "layers": [
            {"id": "core", "title": "Core", "paths": ["src/core/**"], "risk": "high"},
            {"id": "api", "paths": ["src/api/**"]},
            {"id": "ui", "paths": ["src/ui/**"]},
        ],
        "rules": [
            {
                "id": "core-no-ui",
                "type": "forbid-dependency",
                "from": "core",
                "to": "ui",
                "severity": "high",
                "claim": "core must not import ui",
            },
        ],
        "boundaries": [
            {
                "id": "public-api",
                "layer": "api",
                "paths": ["src/api/public/**"],
                "visibility": "public",
            },
        ],
        "data_ownership": [
            {
                "store": "memory-db",
                "owner": "core",
                "paths": ["var/memory/**"],
                "kind": "sqlite",
            },
        ],
        "invariants": [
            {
                "id": "no-plaintext-secrets",
                "claim": "secrets are never stored in plaintext",
                "severity": "high",
                "check": "static",
                "paths": ["src/**"],
            },
        ],
    }


class DevContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_contract(self, data, rel=dev_contract.CONTRACT_PATH):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        else:
            target.write_text(json.dumps(data), encoding="utf-8")
        return target

    @staticmethod
    def codes(result):
        return [finding["code"] for finding in result["findings"]]

    @staticmethod
    def pointers(result, code):
        return [f["pointer"] for f in result["findings"] if f["code"] == code]

    def test_valid_contract_passes_and_normalizes(self):
        self.write_contract(valid_contract())
        result = dev_contract.load_contract(str(self.root))
        self.assertTrue(result["present"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["layers"], ["core", "api", "ui"])
        self.assertEqual(result["rules"], ["core-no-ui"])
        self.assertEqual(result["findings"], [])

    def test_missing_contract_is_absent_not_an_error(self):
        result = dev_contract.load_contract(str(self.root))
        self.assertFalse(result["present"])
        self.assertTrue(result["ok"])
        self.assertIsNone(result["contract"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["path"], dev_contract.CONTRACT_PATH)

    def test_invalid_json_reports_evidence(self):
        self.write_contract("{not json")
        result = dev_contract.load_contract(str(self.root))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("contract-invalid-json", self.codes(result))
        finding = result["findings"][0]
        self.assertEqual(finding["severity"], "critical")
        self.assertTrue(finding["evidence"])

    def test_non_object_root_is_rejected(self):
        self.write_contract([1, 2, 3])
        result = dev_contract.load_contract(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("contract-not-object", self.codes(result))

    def test_unsupported_version_is_rejected(self):
        data = valid_contract()
        data["version"] = 2
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertIn("contract-unsupported-version", self.codes(result))

        data = valid_contract()
        del data["version"]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertIn("contract-unsupported-version", self.codes(result))

    def test_duplicate_and_malformed_layers(self):
        data = valid_contract()
        data["layers"] = [
            {"id": "core", "paths": ["src/core/**"]},
            {"id": "core", "paths": ["src/other/**"]},
            {"id": "Core Layer", "paths": ["src/bad/**"]},
            {"id": "nopath"},
            "not-a-layer",
        ]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        codes = self.codes(result)
        self.assertIn("contract-duplicate-layer", codes)
        self.assertIn("contract-invalid-layer", codes)
        self.assertIn("contract-layer-without-paths", codes)
        self.assertIn("/layers/1/id", self.pointers(result, "contract-duplicate-layer"))
        self.assertIn("/layers/4", self.pointers(result, "contract-invalid-layer"))

    def test_invalid_paths_are_rejected(self):
        data = valid_contract()
        data["layers"][0]["paths"] = ["../escape", "/abs/path", "C:/win", ""]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        pointers = self.pointers(result, "contract-invalid-path")
        self.assertIn("/layers/0/paths/0", pointers)
        self.assertIn("/layers/0/paths/1", pointers)
        self.assertIn("/layers/0/paths/2", pointers)
        self.assertIn("/layers/0/paths/3", pointers)

    def test_unknown_layer_references_are_rejected(self):
        data = valid_contract()
        data["rules"][0]["to"] = "ghost"
        data["boundaries"][0]["layer"] = "ghost"
        data["data_ownership"][0]["owner"] = "ghost"
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        pointers = self.pointers(result, "contract-unknown-layer-ref")
        self.assertIn("/rules/0/to", pointers)
        self.assertIn("/boundaries/0/layer", pointers)
        self.assertIn("/data_ownership/0/owner", pointers)

    def test_rule_shape_errors(self):
        data = valid_contract()
        data["rules"] = [
            {"id": "bad-type", "type": "teleport", "from": "core", "to": "ui"},
            {"id": "bad-severity", "type": "forbid-dependency", "from": "core",
             "to": "ui", "severity": "urgent"},
            {"id": "missing-to", "type": "forbid-dependency", "from": "core"},
            {"id": "allow-not-list", "type": "allow-dependency", "from": "ui", "to": "core"},
        ]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        invalid = self.pointers(result, "contract-invalid-rule")
        self.assertIn("/rules/0/type", invalid)
        self.assertIn("/rules/2/to", invalid)
        self.assertIn("/rules/3/to", invalid)
        self.assertIn("/rules/1/severity", self.pointers(result, "contract-invalid-severity"))

    def test_allow_dependency_accepts_layer_list(self):
        data = valid_contract()
        data["rules"].append(
            {"id": "ui-only", "type": "allow-dependency", "from": "ui",
             "to": ["ui", "api"], "severity": "medium"}
        )
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["rules"], ["core-no-ui", "ui-only"])

    def test_duplicate_rule_ids(self):
        data = valid_contract()
        data["rules"].append(dict(data["rules"][0]))
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertIn("contract-duplicate-rule", self.codes(result))

    def test_invariants_require_claim_and_unique_id(self):
        data = valid_contract()
        data["invariants"] = [
            {"id": "no-claim"},
            {"id": "dup", "claim": "first"},
            {"id": "dup", "claim": "second"},
            "nope",
        ]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        codes = self.codes(result)
        self.assertIn("contract-invalid-invariant", codes)
        self.assertIn("contract-duplicate-invariant", codes)

    def test_invariant_paths_are_optional(self):
        data = valid_contract()
        data["invariants"] = [{"id": "simple-claim", "claim": "something holds"}]
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["findings"], [])

    def test_unknown_top_level_key_is_warning(self):
        data = valid_contract()
        data["extras"] = {"anything": True}
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        self.assertTrue(result["ok"])
        finding = next(f for f in result["findings"] if f["code"] == "contract-unknown-key")
        self.assertEqual(finding["severity"], "low")
        self.assertEqual(finding["pointer"], "/extras")

    def test_findings_are_sorted_and_complete(self):
        data = valid_contract()
        data["layers"][0]["paths"] = ["../escape"]
        data["rules"][0]["to"] = "ghost"
        data["extras"] = {}
        self.write_contract(data)
        result = dev_contract.load_contract(str(self.root))
        keys = [(f["pointer"], f["code"]) for f in result["findings"]]
        self.assertEqual(keys, sorted(keys))
        for finding in result["findings"]:
            for key in ("code", "severity", "message", "pointer", "path", "evidence"):
                self.assertIn(key, finding)
            self.assertEqual(finding["path"], dev_contract.CONTRACT_PATH)

    def test_schema_describes_version_one(self):
        schema = dev_contract.contract_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["version", "layers"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["version"]["const"], 1)
        rule_types = schema["properties"]["rules"]["items"]["properties"]["type"]["enum"]
        self.assertEqual(sorted(rule_types), sorted(dev_contract.RULE_TYPES))

    def test_glob_matching_semantics(self):
        cases = [
            ("src/core/a/b.py", "src/core/**", True),
            ("src/corex/a.py", "src/core/**", False),
            ("pkg/a.py", "pkg/*.py", True),
            ("pkg/sub/a.py", "pkg/*.py", False),
            ("pkg/sub/a.py", "pkg/", True),
            ("pkg/a.py", "pkg/", True),
            ("src/Core/a.py", "src/core/**", False),
            ("a.py", "**/*.py", True),
            ("setup.py", "setup.py", True),
        ]
        for path, pattern, expected in cases:
            with self.subTest(path=path, pattern=pattern):
                self.assertEqual(dev_contract.match_path(path, pattern), expected)

    def test_match_layers_is_declaration_ordered(self):
        data = valid_contract()
        data["layers"] = [
            {"id": "everything", "paths": ["src/**"]},
            {"id": "core", "paths": ["src/core/**"]},
        ]
        self.write_contract(data)
        contract = dev_contract.load_contract(str(self.root))["contract"]
        self.assertEqual(
            dev_contract.match_layers("src/core/a.py", contract),
            ["everything", "core"],
        )
        self.assertEqual(dev_contract.match_layers("tools/x.py", contract), [])

    def test_contract_file_override(self):
        self.write_contract(valid_contract(), rel="config/arch.json")
        result = dev_contract.load_contract(str(self.root), contract_file="config/arch.json")
        self.assertTrue(result["present"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "config/arch.json")

    def test_load_contract_does_not_write(self):
        self.write_contract(valid_contract())
        folder = self.root / ".yotta"
        before = sorted(item.name for item in folder.iterdir())
        dev_contract.load_contract(str(self.root))
        after = sorted(item.name for item in folder.iterdir())
        self.assertEqual(before, after)


class VerificationPolicyTest(unittest.TestCase):
    """Contract tests for .yotta/verification.json policy validation (S1.3)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def valid_policy(self):
        return {
            "version": 1,
            "checks": [
                {
                    "id": "unit-tests",
                    "level": "L2",
                    "kind": "python-unittest",
                    "claim": "unit tests pass",
                },
                {
                    "id": "integration-tests",
                    "level": "L3",
                    "kind": "npm-test",
                    "cwd": "web",
                    "timeout": 300,
                    "required": False,
                    "claim": "integration tests pass",
                },
            ],
            "manual": [
                {"id": "review", "claim": "a second person reviews the change"}
            ],
        }

    def write_policy(self, data, rel=dev_contract.VERIFICATION_PATH):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    @staticmethod
    def codes(result):
        return {item["code"] for item in result["findings"]}

    def test_valid_policy_is_normalized(self):
        self.write_policy(self.valid_policy())
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertTrue(result["present"])
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["version"], 1)
        self.assertEqual([item["id"] for item in result["checks"]],
                         ["unit-tests", "integration-tests"])
        first = result["checks"][0]
        self.assertEqual(first["cwd"], ".")
        self.assertEqual(first["timeout"], 120)
        self.assertIs(first["required"], True)
        second = result["checks"][1]
        self.assertEqual(second["cwd"], "web")
        self.assertEqual(second["timeout"], 300)
        self.assertIs(second["required"], False)

    def test_missing_policy_is_optional(self):
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertFalse(result["present"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["checks"], [])
        self.assertEqual(result["findings"], [])

    def test_invalid_json_is_critical(self):
        target = self.root / dev_contract.VERIFICATION_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{", encoding="utf-8")
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("verification-invalid-json", self.codes(result))

    def test_unsupported_version_fails(self):
        policy = self.valid_policy()
        policy["version"] = 3
        self.write_policy(policy)
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("verification-unsupported-version", self.codes(result))

    def test_unknown_runner_kind_fails(self):
        policy = self.valid_policy()
        policy["checks"][0]["kind"] = "rm -rf"
        self.write_policy(policy)
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("verification-invalid-kind", self.codes(result))

    def test_cwd_escape_fails(self):
        policy = self.valid_policy()
        policy["checks"][0]["cwd"] = "../outside"
        self.write_policy(policy)
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("verification-invalid-cwd", self.codes(result))

    def test_duplicate_ids_and_bad_levels_fail(self):
        policy = self.valid_policy()
        policy["checks"][1]["id"] = "unit-tests"
        policy["checks"][1]["level"] = "L9"
        self.write_policy(policy)
        result = dev_contract.load_verification_policy(str(self.root))
        codes = self.codes(result)
        self.assertIn("verification-duplicate-check", codes)
        self.assertIn("verification-invalid-level", codes)

    def test_manual_claims_require_ids_and_claims(self):
        policy = self.valid_policy()
        policy["manual"] = [{"id": "review"}, {"claim": "no id"}]
        self.write_policy(policy)
        result = dev_contract.load_verification_policy(str(self.root))
        self.assertIn("verification-invalid-manual", self.codes(result))

    def test_schema_describes_version_one(self):
        schema = dev_contract.verification_schema()
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["version"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["version"]["const"], 1)
        levels = schema["properties"]["checks"]["items"]["properties"]["level"]["enum"]
        self.assertEqual(sorted(levels), ["L2", "L3", "L4"])

    def test_load_policy_does_not_write(self):
        self.write_policy(self.valid_policy())
        folder = self.root / ".yotta"
        before = sorted(item.name for item in folder.iterdir())
        dev_contract.load_verification_policy(str(self.root))
        after = sorted(item.name for item in folder.iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
