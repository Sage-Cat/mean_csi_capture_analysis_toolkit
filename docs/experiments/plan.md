# Implementation Plan - Unified Experiment Environment

Date: 2026-03-02

## Goal

Move all experiment types to a common environment abstraction while preserving existing operator workflows.

## Phase 1 - Environment Abstraction

Tasks:

1. Add `csi_capture/core/environment.py` as a single registry for target profiles.
2. Define baseline profile `esp32s3_csi_v1`.
3. Add profile resolver, listing helpers, and operator banner output.

Definition of done:

- profile registry exists
- unknown profiles are rejected
- defaults are explicit and centralized

## Phase 2 - Pipeline Integration

Tasks:

1. Integrate `target_profile` into `csi_capture.experiment` config normalization.
2. Include environment profile snapshot in run manifests and packet metadata.
3. Integrate `target_profile` into `static_sign_v1` capture metadata.
4. Add CLI support to list profiles and pass profile overrides.

Definition of done:

- distance/angle/static_sign all produce environment-aware outputs
- existing commands remain backward compatible

## Phase 3 - Config and Script Harmonization

Tasks:

1. Update sample configs (`../studies/csi_capture_characterization/configs/*`) to include `target_profile`.
2. Update protocol scripts to pass profile id explicitly where appropriate.
3. Keep defaults aligned with shared profile settings.

Definition of done:

- sample configs are immediately usable and consistent
- protocol scripts surface profile selection to operators

## Phase 4 - Testing and Validation

Tasks:

1. Extend tests for target profile resolution and config validation.
2. Re-run full unit suite.
3. Capture validation outcomes in a dedicated report.

Definition of done:

- tests pass
- report has concrete command outcomes

## Phase 5 - Documentation and Design

Tasks:

1. Build PlantUML design package with rendered PNGs.
2. Update requirements/design/plan docs to reflect new architecture.
3. Add/refresh UA experiment docs for all experiment types.

Definition of done:

- documentation can be used directly by operators and reviewers
- diagrams are versioned and reproducible

## Risks and Mitigations

- Risk: profile metadata divergence across modules.
  Mitigation: profile registry as single source of truth.

- Risk: breaking legacy operator commands.
  Mitigation: additive CLI flags and backward-compatible defaults.

- Risk: hardware-dependent validation not always available.
  Mitigation: dry-run capture gate + explicit blocked-state documentation.
