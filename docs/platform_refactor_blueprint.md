# ESP32 CSI/RSSI Platform Refactor Blueprint

Date: 2026-03-08

## 1) Architecture Audit

### Current strengths

- `csi_capture/` already contains reusable packet parsing, serial capture, target-profile metadata, and a config-driven distance/angle runner.
- `static_sign_v1` already proves the repo can support capture + feature extraction + train/eval in one codebase.
- The repository already preserves practical lab constraints: serial capture from `CSI_DATA,` lines, Linux/macOS device handling, and the `esp32s3_csi_v1` baseline.

### Current architectural faults

- There are two parallel experiment stacks:
  - `distance` and `angle` use `csi_capture.experiment`.
  - `static_sign_v1` uses separate dataset, CLI, model, and evaluation flows.
- Metadata and schema naming are inconsistent:
  - `manifest.json` for distance/angle.
  - `metadata.json` + `frames.jsonl` for static sign.
- Storage is inconsistent:
  - `../../private/experiments/csi_capture_characterization/runs/<exp_id>/<experiment_type>/run_<id>/trial_<id>/capture.jsonl`
  - `../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/<dataset>/<label>/run_<id>/frames.jsonl`
- `tools/analyze_wifi_{distance,angular_localization,stability_statistics}.py` duplicated scenario normalization, file discovery, packet decoding, and path inference logic.
- CLI discovery was hardcoded around one experiment family, making future plugin-style extension expensive.

### Refactor objective

Keep existing working flows alive, but make the platform feel like one product with:

- one experiment registry
- one domain vocabulary
- one canonical manifest schema
- one dataset/layout adapter layer
- one CLI dispatch surface
- one reusable analysis helper layer

## 2) Target Architecture

### Module boundaries

- `csi_capture/parser.py`, `csi_capture/capture.py`
  - raw serial transport and `CSI_DATA,` packet parsing
- `csi_capture/core/domain.py`
  - shared platform vocabulary for experiments, runs, trials, provenance, labels, geometry, features, model artifacts, and evaluation reports
- `csi_capture/core/layout.py`
  - canonical and legacy layout builders for capture/artifact paths
- `csi_capture/core/dataset.py`
  - normalized run/trial adapters and manifest validation
- `csi_capture/experiments/registry.py`
  - plugin registry and capability dispatch
- `csi_capture/experiments/*.py`
  - per-experiment handlers and config validation
- `csi_capture/analysis/common.py`
  - shared analysis-side file discovery, path inference, and CSI decoding helpers
- `csi_capture/cli.py`
  - coherent user-facing dispatch surface
- `tools/*.py`
  - thin report wrappers or transitional entrypoints around reusable modules

### Canonical domain entities

- `DeviceProfile`
- `EnvironmentProfile`
- `ExperimentDefinition`
- `RunManifest`
- `TrialDefinition`
- `AcquisitionBlock`
- `PacketRecord`
- `SubjectRef`
- `ScenarioRef`
- `LabelSet`
- `GroundTruth`
- `Geometry`
- `DerivedFeatureSet`
- `TrainedModelArtifact`
- `EvaluationReport`

The implementation now includes these entities in `csi_capture/core/domain.py`.

## 3) Recommended Directory Tree

```text
csi_capture/
  analysis/
    common.py
  core/
    dataset.py
    device.py
    domain.py
    environment.py
    evaluation.py
    features.py
    layout.py
    models.py
  experiments/
    angle.py
    distance.py
    presence_v1.py
    registry.py
    static_sign_v1.py
  capture.py
  cli.py
  experiment.py
  parser.py

docs/
  configs/
  platform_refactor_blueprint.md

tools/
  analyze_wifi_distance_measurement.py
  analyze_wifi_angular_localization.py
  analyze_wifi_stability_statistics.py
  analyze_wifi_static_gesture.py
  exp
```

## 4) Canonical Schema Design

### Manifest contract

Canonical manifest fields:

- `schema_name = "esp32-csi-platform"`
- `schema_version = "v1"`
- `layout_style`
- `experiment`
- `dataset_id`
- `run_id`
- `status`
- `created_at_utc`
- `scenario`
- `subject`
- `geometry`
- `trials`
- `provenance`
- `capture`
- `config_snapshot`
- `extra`

### Layout strategy

The platform now supports three layout styles through `csi_capture/core/layout.py`:

- `canonical_v1`
- `legacy_distance_angle_v1`
- `legacy_static_sign_v1`

This keeps old data usable while giving future experiments one standard layout target.

### Provenance fields to preserve

- target profile id
- serial device path
- resolved realpath
- git revision / dirty flag when available
- room/environment notes
- scenario tags
- ground truth

## 5) Unified CLI Design

Current implemented surface in `csi_capture/cli.py`:

- `list-devices`
- `list-target-profiles`
- `list-experiments`
- `capture --experiment <id> ...`
- `train --experiment <id> ...`
- `eval --experiment <id> ...`
- `validate-config --experiment <id> --mode <mode> --config <path>`
- `distance` compatibility wrapper

Current registered plugins:

- `distance`
- `angle`
- `static_sign_v1`
- `presence_v1`

## 6) Migration Map

### Current -> target

- `csi_capture.experiment` distance/angle capture
  - remains operational
  - now emits canonical-schema manifest fields while preserving legacy path layout
- `csi_capture.experiments.static_sign_v1`
  - remains operational
  - now writes both legacy `metadata.json` and canonical `manifest.json`
- `csi_capture.core.dataset.load_static_sign_runs`
  - now loads both old static-sign layout and canonicalized runs through `load_normalized_runs`
- `tools/analyze_wifi_distance_measurement.py`
- `tools/analyze_wifi_angular_localization.py`
- `tools/analyze_wifi_stability_statistics.py`
  - now share reusable loaders/decoders from `csi_capture.analysis.common`

### Future migrations

- Move report logic from `tools/` into `csi_capture/analysis/` submodules.
- Add `report` and `inspect` handlers to plugins when report pipelines are migrated out of the scripts.
- Promote `canonical_v1` as the default layout for new experiment families.

## 7) Prioritized Refactor Plan

### Phase 1: minimum viable unification

Implemented in this refactor:

- shared domain model
- layout helper layer
- experiment registry
- unified CLI dispatch
- normalized dataset adapters
- shared analysis helper extraction
- future-ready `presence_v1` plugin shape

### Phase 2: schema consolidation and adapters

Next:

- add explicit packet schema validation
- add richer normalized loaders for distance/angle/stability datasets
- unify feature/artifact metadata manifests

### Phase 3: report/plugin architecture

Next:

- move analysis/report logic from `tools/` into `csi_capture/analysis/<experiment>.py`
- add `report` and `inspect` handlers to registry plugins
- support report-only pipelines on existing datasets through normalized adapters

### Phase 4: cleanup

Next:

- deprecate direct legacy layout writes where practical
- standardize new experiment capture on `canonical_v1`
- reduce transitional wrapper code in `tools/`

## 8) Adding a New Experiment Type

Recommended minimal steps:

1. Add a new module under `csi_capture/experiments/`, for example `nlos_classifier_v1.py`.
2. Define `ExperimentDefinition`.
3. Add config validation.
4. Add any capture/train/eval/report handlers required.
5. Register it in `csi_capture/experiments/__init__.py`.
6. Add tests for registry, validation, layout, and any schema adapter logic.

`presence_v1` is now included as the minimal future-ready example.

## 9) Recommended Tests

Already added or updated in this refactor:

- registry discovery and capabilities
- layout path generation
- existing parser/capture/config/dataset/CLI tests

Recommended next additions:

- canonical manifest roundtrip tests
- distance/angle normalized-run adapter tests
- plugin dispatch tests for `capture --experiment distance|angle`
- report module tests once report code is migrated out of `tools/`
