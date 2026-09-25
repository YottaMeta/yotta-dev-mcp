#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Architecture contract for yotta-dev-mcp (`.yotta/architecture.json`).

The contract is plain JSON, versioned, and validated with deterministic
findings: every problem carries a code, a severity, a JSON pointer and short
evidence. Validation never writes to disk and never raises on bad input.
"""

import json
import re
from pathlib import Path

CONTRACT_PATH = ".yotta/architecture.json"
VERIFICATION_PATH = ".yotta/verification.json"
CONTRACT_VERSION = 1

SEVERITIES = ("critical", "high", "medium", "low", "info")
BLOCKING_SEVERITIES = ("critical", "high")
RISK_LEVELS = ("critical", "high", "medium", "low")
RULE_TYPES = ("forbid-dependency", "allow-dependency")
RULE_DEFAULT_SEVERITY = "medium"
BOUNDARY_VISIBILITY = ("public", "internal", "private")
INVARIANT_CHECKS = ("static", "command", "manual")

TOP_LEVEL_KEYS = frozenset({
    "version", "project", "layers", "rules", "boundaries",
    "data_ownership", "invariants", "risk_weights",
})

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")

_GLOB_CACHE = {}


def _is_id(value):
    return isinstance(value, str) and bool(ID_RE.match(value))


def _glob_regex(pattern):
    cached = _GLOB_CACHE.get(pattern)
    if cached is not None:
        return cached
    text = pattern
    if text.endswith("/"):
        text += "**"
    parts = []
    index = 0
    while index < len(text):
        if text.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if text.startswith("**", index):
            parts.append(".*")
            index += 2
            continue
        char = text[index]
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1
    compiled = re.compile("^" + "".join(parts) + "$")
    _GLOB_CACHE[pattern] = compiled
    return compiled


def match_path(path, pattern):
    """Return True when a repository-relative POSIX path matches a glob."""
    if not isinstance(pattern, str) or not pattern.strip():
        return False
    rel = str(path).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return bool(_glob_regex(pattern.strip()).match(rel))


def match_layers(path, contract):
    """Return the ids of every layer whose globs match the path, in order."""
    if not isinstance(contract, dict):
        return []
    matched = []
    for layer in contract.get("layers") or []:
        for pattern in layer.get("paths") or []:
            if match_path(path, pattern):
                matched.append(layer["id"])
                break
    return matched


def _path_problem(value):
    if not isinstance(value, str) or not value.strip():
        return "path must be a non-empty string"
    text = value.strip()
    if text.startswith("/") or text.startswith("~"):
        return "path must be relative to the repository root"
    if WINDOWS_ABS_RE.match(text) or text.startswith("\\\\"):
        return "path must not be an absolute Windows path"
    if any(part == ".." for part in text.replace("\\", "/").split("/")):
        return "path must not escape the repository root"
    return None


def _normalize_layer(layer):
    paths = [item for item in layer.get("paths") or [] if isinstance(item, str)]
    return {
        "id": layer.get("id"),
        "title": layer.get("title") if isinstance(layer.get("title"), str) else None,
        "paths": paths,
        "risk": layer.get("risk") if layer.get("risk") in RISK_LEVELS else None,
        "description": layer.get("description") if isinstance(layer.get("description"), str) else None,
    }


def _normalize_rule(rule):
    return {
        "id": rule.get("id"),
        "type": rule.get("type"),
        "from": rule.get("from"),
        "to": rule.get("to"),
        "severity": rule.get("severity") or RULE_DEFAULT_SEVERITY,
        "claim": rule.get("claim") if isinstance(rule.get("claim"), str) else None,
        "description": rule.get("description") if isinstance(rule.get("description"), str) else None,
    }


def _normalize_boundary(boundary):
    return {
        "id": boundary.get("id"),
        "layer": boundary.get("layer"),
        "paths": [item for item in boundary.get("paths") or [] if isinstance(item, str)],
        "visibility": boundary.get("visibility") or "internal",
        "description": boundary.get("description") if isinstance(boundary.get("description"), str) else None,
    }


def _normalize_store(store):
    return {
        "store": store.get("store"),
        "owner": store.get("owner"),
        "paths": [item for item in store.get("paths") or [] if isinstance(item, str)],
        "kind": store.get("kind") if isinstance(store.get("kind"), str) else None,
        "notes": store.get("notes") if isinstance(store.get("notes"), str) else None,
    }


def _normalize_invariant(invariant):
    return {
        "id": invariant.get("id"),
        "claim": invariant.get("claim"),
        "severity": invariant.get("severity") or RULE_DEFAULT_SEVERITY,
        "check": invariant.get("check") or "manual",
        "paths": [item for item in invariant.get("paths") or [] if isinstance(item, str)],
        "description": invariant.get("description") if isinstance(invariant.get("description"), str) else None,
    }


def _normalize(data):
    layers = [item for item in data.get("layers") or []
              if isinstance(item, dict) and _is_id(item.get("id"))]
    rules = [item for item in data.get("rules") or []
             if isinstance(item, dict) and _is_id(item.get("id"))]
    boundaries = [item for item in data.get("boundaries") or []
                  if isinstance(item, dict) and _is_id(item.get("id"))]
    stores = [item for item in data.get("data_ownership") or []
              if isinstance(item, dict) and _is_id(item.get("store"))]
    invariants = [item for item in data.get("invariants") or []
                  if isinstance(item, dict) and _is_id(item.get("id"))]
    weights = data.get("risk_weights")
    return {
        "version": data.get("version"),
        "project": data.get("project") if isinstance(data.get("project"), str) else None,
        "layers": [_normalize_layer(item) for item in layers],
        "rules": [_normalize_rule(item) for item in rules],
        "boundaries": [_normalize_boundary(item) for item in boundaries],
        "data_ownership": [_normalize_store(item) for item in stores],
        "invariants": [_normalize_invariant(item) for item in invariants],
        "risk_weights": dict(weights) if isinstance(weights, dict) else {},
    }


def _result(present, path, contract, findings, version=None):
    ordered = sorted(findings, key=lambda item: (item["pointer"], item["code"], item["message"]))
    blocking = any(item["severity"] in BLOCKING_SEVERITIES for item in ordered)
    return {
        "present": bool(present),
        "path": path,
        "ok": not blocking,
        "status": "FAIL" if blocking else "PASS",
        "version": version if version is not None
                   else (contract or {}).get("version"),
        "contract": contract,
        "layers": [layer["id"] for layer in (contract or {}).get("layers") or []],
        "rules": [rule["id"] for rule in (contract or {}).get("rules") or []],
        "findings": ordered,
    }


def validate_contract(data, source=CONTRACT_PATH):
    """Validate a decoded contract and return findings plus a normalized copy."""
    source = str(source).replace("\\", "/")
    findings = []

    def add(code, severity, message, pointer="", evidence=""):
        findings.append({
            "code": code,
            "severity": severity,
            "message": message,
            "pointer": pointer,
            "path": source,
            "evidence": evidence,
        })

    def check_paths(values, pointer, object_code, required=False):
        if values is None:
            if required:
                add(object_code, "high",
                    "paths must be a list of glob strings", pointer, "None")
            return
        if not isinstance(values, list):
            add(object_code, "high",
                "paths must be a list of glob strings", pointer, repr(values))
            return
        for index, value in enumerate(values):
            problem = _path_problem(value)
            if problem:
                add("contract-invalid-path", "high", problem,
                    "%s/%d" % (pointer, index), repr(value))

    if not isinstance(data, dict):
        add("contract-not-object", "critical",
            "contract root must be a JSON object", "", type(data).__name__)
        return _result(True, source, None, findings)

    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != CONTRACT_VERSION:
        add("contract-unsupported-version", "critical",
            "contract version must be %d" % CONTRACT_VERSION,
            "/version", repr(version))

    for key in sorted(set(data) - TOP_LEVEL_KEYS):
        add("contract-unknown-key", "low",
            "unknown top-level key is ignored", "/" + key, key)

    layer_ids = []
    layers = data.get("layers")
    if not isinstance(layers, list):
        add("contract-invalid-layers", "critical",
            "layers must be a list", "/layers", type(layers).__name__)
        layers = []
    elif not layers:
        add("contract-empty-layers", "medium",
            "layers is empty; every module stays UNKNOWN", "/layers", "")
    for index, layer in enumerate(layers):
        pointer = "/layers/%d" % index
        if not isinstance(layer, dict):
            add("contract-invalid-layer", "high",
                "layer must be an object", pointer, type(layer).__name__)
            continue
        layer_id = layer.get("id")
        if not _is_id(layer_id):
            add("contract-invalid-layer", "high",
                "layer id must match [a-z0-9][a-z0-9._-]*",
                pointer + "/id", repr(layer_id))
        elif layer_id in layer_ids:
            add("contract-duplicate-layer", "high",
                "duplicate layer id", pointer + "/id", layer_id)
        else:
            layer_ids.append(layer_id)
        if layer.get("paths") is None:
            add("contract-layer-without-paths", "medium",
                "layer declares no paths", pointer + "/paths", str(layer_id))
        else:
            check_paths(layer.get("paths"), pointer + "/paths", "contract-invalid-layer")
        risk = layer.get("risk")
        if risk is not None and risk not in RISK_LEVELS:
            add("contract-invalid-risk", "medium",
                "risk must be one of %s" % ", ".join(RISK_LEVELS),
                pointer + "/risk", repr(risk))

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        add("contract-invalid-rules", "high",
            "rules must be a list", "/rules", type(rules).__name__)
        rules = []
    rule_ids = []
    for index, rule in enumerate(rules):
        pointer = "/rules/%d" % index
        if not isinstance(rule, dict):
            add("contract-invalid-rule", "high",
                "rule must be an object", pointer, type(rule).__name__)
            continue
        rule_id = rule.get("id")
        if not _is_id(rule_id):
            add("contract-invalid-rule", "high",
                "rule id must match [a-z0-9][a-z0-9._-]*", pointer + "/id", repr(rule_id))
        elif rule_id in rule_ids:
            add("contract-duplicate-rule", "high",
                "duplicate rule id", pointer + "/id", rule_id)
        else:
            rule_ids.append(rule_id)
        severity = rule.get("severity")
        if severity is not None and severity not in SEVERITIES:
            add("contract-invalid-severity", "high",
                "severity must be one of %s" % ", ".join(SEVERITIES),
                pointer + "/severity", repr(severity))
        rule_type = rule.get("type")
        if rule_type not in RULE_TYPES:
            add("contract-invalid-rule", "high",
                "rule type must be one of %s" % ", ".join(RULE_TYPES),
                pointer + "/type", repr(rule_type))
            continue
        source_layer = rule.get("from")
        if source_layer not in layer_ids:
            add("contract-unknown-layer-ref", "high",
                "rule references an undefined layer",
                pointer + "/from", repr(source_layer))
        target = rule.get("to")
        if rule_type == "forbid-dependency":
            if not isinstance(target, str):
                add("contract-invalid-rule", "high",
                    "forbid-dependency needs one target layer in 'to'",
                    pointer + "/to", repr(target))
            elif target not in layer_ids:
                add("contract-unknown-layer-ref", "high",
                    "rule references an undefined layer", pointer + "/to", repr(target))
        else:
            if not isinstance(target, list) or not target:
                add("contract-invalid-rule", "high",
                    "allow-dependency needs a list of allowed layers in 'to'",
                    pointer + "/to", repr(target))
            else:
                for position, item in enumerate(target):
                    if item not in layer_ids:
                        add("contract-unknown-layer-ref", "high",
                            "rule references an undefined layer",
                            "%s/to/%d" % (pointer, position), repr(item))

    boundaries = data.get("boundaries", [])
    if not isinstance(boundaries, list):
        add("contract-invalid-boundaries", "high",
            "boundaries must be a list", "/boundaries", type(boundaries).__name__)
        boundaries = []
    boundary_ids = []
    for index, boundary in enumerate(boundaries):
        pointer = "/boundaries/%d" % index
        if not isinstance(boundary, dict):
            add("contract-invalid-boundary", "high",
                "boundary must be an object", pointer, type(boundary).__name__)
            continue
        boundary_id = boundary.get("id")
        if not _is_id(boundary_id):
            add("contract-invalid-boundary", "high",
                "boundary id must match [a-z0-9][a-z0-9._-]*",
                pointer + "/id", repr(boundary_id))
        elif boundary_id in boundary_ids:
            add("contract-duplicate-boundary", "high",
                "duplicate boundary id", pointer + "/id", boundary_id)
        else:
            boundary_ids.append(boundary_id)
        if boundary.get("layer") not in layer_ids:
            add("contract-unknown-layer-ref", "high",
                "boundary references an undefined layer",
                pointer + "/layer", repr(boundary.get("layer")))
        check_paths(boundary.get("paths"), pointer + "/paths", "contract-invalid-boundary")
        visibility = boundary.get("visibility")
        if visibility is not None and visibility not in BOUNDARY_VISIBILITY:
            add("contract-invalid-boundary", "medium",
                "visibility must be one of %s" % ", ".join(BOUNDARY_VISIBILITY),
                pointer + "/visibility", repr(visibility))

    stores = data.get("data_ownership", [])
    if not isinstance(stores, list):
        add("contract-invalid-data-ownership", "high",
            "data_ownership must be a list", "/data_ownership", type(stores).__name__)
        stores = []
    store_ids = []
    for index, store in enumerate(stores):
        pointer = "/data_ownership/%d" % index
        if not isinstance(store, dict):
            add("contract-invalid-store", "high",
                "data_ownership entry must be an object", pointer, type(store).__name__)
            continue
        store_id = store.get("store")
        if not _is_id(store_id):
            add("contract-invalid-store", "high",
                "store id must match [a-z0-9][a-z0-9._-]*", pointer + "/store", repr(store_id))
        elif store_id in store_ids:
            add("contract-duplicate-store", "high",
                "duplicate store id", pointer + "/store", store_id)
        else:
            store_ids.append(store_id)
        if store.get("owner") not in layer_ids:
            add("contract-unknown-layer-ref", "high",
                "store references an undefined owner layer",
                pointer + "/owner", repr(store.get("owner")))
        check_paths(store.get("paths"), pointer + "/paths", "contract-invalid-store")

    invariants = data.get("invariants", [])
    if not isinstance(invariants, list):
        add("contract-invalid-invariants", "high",
            "invariants must be a list", "/invariants", type(invariants).__name__)
        invariants = []
    invariant_ids = []
    for index, invariant in enumerate(invariants):
        pointer = "/invariants/%d" % index
        if not isinstance(invariant, dict):
            add("contract-invalid-invariant", "high",
                "invariant must be an object", pointer, type(invariant).__name__)
            continue
        invariant_id = invariant.get("id")
        if not _is_id(invariant_id):
            add("contract-invalid-invariant", "high",
                "invariant id must match [a-z0-9][a-z0-9._-]*",
                pointer + "/id", repr(invariant_id))
        elif invariant_id in invariant_ids:
            add("contract-duplicate-invariant", "high",
                "duplicate invariant id", pointer + "/id", invariant_id)
        else:
            invariant_ids.append(invariant_id)
        claim = invariant.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            add("contract-invalid-invariant", "high",
                "invariant needs a non-empty claim", pointer + "/claim", repr(claim))
        severity = invariant.get("severity")
        if severity is not None and severity not in SEVERITIES:
            add("contract-invalid-severity", "high",
                "severity must be one of %s" % ", ".join(SEVERITIES),
                pointer + "/severity", repr(severity))
        check = invariant.get("check")
        if check is not None and check not in INVARIANT_CHECKS:
            add("contract-invalid-invariant", "medium",
                "check must be one of %s" % ", ".join(INVARIANT_CHECKS),
                pointer + "/check", repr(check))
        check_paths(invariant.get("paths"), pointer + "/paths", "contract-invalid-invariant")

    weights = data.get("risk_weights")
    if weights is not None and not isinstance(weights, dict):
        add("contract-invalid-risk-weight", "medium",
            "risk_weights must be an object", "/risk_weights", type(weights).__name__)
    elif isinstance(weights, dict):
        for key in sorted(weights):
            if key not in layer_ids:
                add("contract-unknown-layer-ref", "high",
                    "risk_weights references an undefined layer",
                    "/risk_weights/" + key, repr(key))
            value = weights[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 5:
                add("contract-invalid-risk-weight", "medium",
                    "risk weight must be a number between 0 and 5",
                    "/risk_weights/" + key, repr(value))

    return _result(True, source, _normalize(data), findings)


def load_contract(root, contract_file=None):
    """Load and validate the contract; never raises on malformed input."""
    rel = str(contract_file or CONTRACT_PATH).replace("\\", "/")
    target = Path(root) / rel
    if not target.is_file():
        return _result(False, rel, None, [], version=None)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        finding = {
            "code": "contract-unreadable", "severity": "critical",
            "message": "contract could not be read", "pointer": "",
            "path": rel, "evidence": str(exc),
        }
        return _result(True, rel, None, [finding])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        finding = {
            "code": "contract-invalid-json", "severity": "critical",
            "message": "invalid JSON: %s" % exc.msg, "pointer": "",
            "path": rel,
            "evidence": "line %d, column %d" % (exc.lineno, exc.colno),
        }
        return _result(True, rel, None, [finding])
    return validate_contract(data, source=rel)


def contract_schema():
    """Return a JSON-Schema description of `.yotta/architecture.json` v1."""
    layer = {
        "type": "object",
        "required": ["id"],
        "additionalProperties": True,
        "properties": {
            "id": {"type": "string", "pattern": ID_RE.pattern},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "risk": {"enum": list(RISK_LEVELS)},
        },
    }
    rule = {
        "type": "object",
        "required": ["id", "type", "from"],
        "additionalProperties": True,
        "properties": {
            "id": {"type": "string", "pattern": ID_RE.pattern},
            "type": {"enum": list(RULE_TYPES)},
            "from": {"type": "string"},
            "to": {
                "description": "target layer for forbid-dependency, layer list for allow-dependency",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "severity": {"enum": list(SEVERITIES)},
            "claim": {"type": "string"},
            "description": {"type": "string"},
        },
    }
    boundary = {
        "type": "object",
        "required": ["id", "layer"],
        "additionalProperties": True,
        "properties": {
            "id": {"type": "string", "pattern": ID_RE.pattern},
            "layer": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "visibility": {"enum": list(BOUNDARY_VISIBILITY)},
            "description": {"type": "string"},
        },
    }
    store = {
        "type": "object",
        "required": ["store", "owner"],
        "additionalProperties": True,
        "properties": {
            "store": {"type": "string", "pattern": ID_RE.pattern},
            "owner": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string"},
            "notes": {"type": "string"},
        },
    }
    invariant = {
        "type": "object",
        "required": ["id", "claim"],
        "additionalProperties": True,
        "properties": {
            "id": {"type": "string", "pattern": ID_RE.pattern},
            "claim": {"type": "string"},
            "severity": {"enum": list(SEVERITIES)},
            "check": {"enum": list(INVARIANT_CHECKS)},
            "paths": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "yotta architecture contract (.yotta/architecture.json)",
        "type": "object",
        "required": ["version", "layers"],
        "additionalProperties": False,
        "properties": {
            "version": {"const": CONTRACT_VERSION},
            "project": {"type": "string"},
            "layers": {"type": "array", "items": layer},
            "rules": {"type": "array", "items": rule},
            "boundaries": {"type": "array", "items": boundary},
            "data_ownership": {"type": "array", "items": store},
            "invariants": {"type": "array", "items": invariant},
            "risk_weights": {
                "type": "object",
                "additionalProperties": {"type": "number", "minimum": 0, "maximum": 5},
            },
        },
    }
