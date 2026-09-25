# Architecture contract and system model

`system_model` builds a deterministic model of a local repository and attaches
layer data from an optional architecture contract. Everything runs offline with
the Python standard library; no file is written.

## Contract file

Path: `.yotta/architecture.json`. Plain JSON, version `1`.

```json
{
  "version": 1,
  "project": "demo",
  "layers": [
    {"id": "core", "title": "Core", "paths": ["src/core/**"], "risk": "high"},
    {"id": "api", "paths": ["src/api/**"]},
    {"id": "ui", "paths": ["src/ui/**"]}
  ],
  "rules": [
    {
      "id": "core-no-ui",
      "type": "forbid-dependency",
      "from": "core",
      "to": "ui",
      "severity": "high",
      "claim": "core must not import ui"
    }
  ],
  "boundaries": [
    {"id": "public-api", "layer": "api", "paths": ["src/api/public/**"], "visibility": "public"}
  ],
  "data_ownership": [
    {"store": "memory-db", "owner": "core", "paths": ["var/memory/**"], "kind": "sqlite"}
  ],
  "invariants": [
    {"id": "no-plaintext-secrets", "claim": "secrets are never stored in plaintext",
     "severity": "high", "check": "static"}
  ],
  "risk_weights": {"core": 3, "api": 2, "ui": 1}
}
```

### Fields

| Field | Required | Notes |
|---|---|---|
| `version` | yes | Must be `1`. |
| `project` | no | Free-form name. |
| `layers` | yes | Layer id, optional title/description, `paths` globs, optional `risk`. |
| `rules` | no | `forbid-dependency` (`to` is one layer) or `allow-dependency` (`to` is a layer list). `from`, `to`, `severity` and `claim` describe the invariant. |
| `boundaries` | no | Layer id plus globs; `visibility` is `public`, `internal` or `private`. |
| `data_ownership` | no | Store id, owning layer, globs and optional `kind`. |
| `invariants` | no | id plus a non-empty `claim`; `check` is `static`, `command` or `manual`. |
| `risk_weights` | no | Layer id to a number between 0 and 5. |

Ids match `[a-z0-9][a-z0-9._-]{0,63}`. Paths are repository-relative POSIX
globs and must not be absolute or contain `..`.

### Glob rules

`*` stays inside one path segment, `**` crosses segments, `?` matches one
character, and a trailing `/` means the whole directory (`pkg/` equals
`pkg/**`). Matching is case-sensitive. Layers are evaluated in declaration
order and the first match wins; a module that matches several layers is
reported as `layer-overlap`.

### Validation findings

Every finding carries `code`, `severity`, `message`, a JSON `pointer`, the
contract `path` and short `evidence`. Severity `critical` or `high` makes the
contract `FAIL`; `medium` and `low` are advisory.

| Code | Meaning |
|---|---|
| `contract-invalid-json` | The file is not valid JSON. |
| `contract-not-object` | The root value is not a JSON object. |
| `contract-unsupported-version` | `version` is missing or not `1`. |
| `contract-invalid-layer` / `contract-duplicate-layer` | Layer shape or ids are wrong. |
| `contract-layer-without-paths` | A layer declares no globs. |
| `contract-invalid-path` | A glob is absolute, escapes the root, or is empty. |
| `contract-invalid-rule` / `contract-duplicate-rule` | Rule shape, type or ids are wrong. |
| `contract-invalid-severity` / `contract-invalid-risk` | Enum values are outside the allowed set. |
| `contract-unknown-layer-ref` | A rule, boundary, store or risk weight points at a missing layer. |
| `contract-invalid-boundary` / `contract-invalid-store` / `contract-invalid-invariant` | Section entries are malformed. |
| `contract-unknown-key` | Unknown top-level key; reported as a warning and ignored. |

## system_model output

| Key | Content |
|---|---|
| `status` | `PASS`, `FAIL` (contract has blocking findings) or `UNKNOWN` (something still needs evidence). |
| `contract` | Path, presence, validity, version, layer and rule ids, findings. |
| `model.modules` | Repository-relative module id, language, line count and resolved layer. |
| `model.layers` | Declared layers with the modules that match them. |
| `model.imports` | `source`, `target`, `kind`, `line` and the raw specifier. |
| `model.entrypoints` | Files that look like executable entrypoints. |
| `model.tests` | Test files and the internal modules they import. |
| `model.configs` | Configuration files with a coarse kind. |
| `model.data_stores` | Declared stores (with owner and owner modules) and detected local database files. |
| `unknowns` | `contract-missing`, `contract-invalid`, `unassigned-module`, `layer-overlap`, `unresolved-import`. |
| `unverified_claims` | Verification levels that were not executed, with the reason. |
| `evidence` | Bounded, sorted evidence lines for the findings above. |
| `truncated` | True when the file limit cut the scan short. |
| `model_digest` | `sha256:` digest of the model, stable across runs on the same tree. |

Import kinds: `internal` (another source module), `internal-file` (a
repository file that is not source, such as `package.json`), `external`
(outside the repository) and `unresolved` (a relative path that does not
exist; also listed under `unknowns`).

`system_model` never asserts that a change is safe. Levels `L1` and above stay
listed in `unverified_claims` until a dedicated check runs them.

## Command line

```bash
python scripts/dev_engine.py system-model .
python scripts/dev_engine.py system-model . --contract config/architecture.json
```
