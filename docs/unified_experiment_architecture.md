# Unified Experiment Architecture (Distance + Angle)

## Scope

This document defines a shared capture architecture for two experiment types:

- `distance`: distance estimation via CSI/RSSI.
- `angle`: angle/AoA dataset capture for later AoA estimation.

The goal is to reuse one capture/session pipeline while keeping existing distance workflows backward compatible.

All experiment types share a common environment profile (`target_profile`). Current baseline profile:

- `esp32s3_csi_v1`

## Unified Experiment Model

### Core entities

- `ExperimentType`: enum-like string, one of `distance | angle`.
- `Run`: one invocation of the runner (`run_id` unique within `exp_id` + `experiment_type`).
- `Trial`: one ground-truth point inside a run (`trial_id`), repeated captures allowed.
- `PacketRecord`: one parsed CSI packet with host timestamp and metadata tags.
- `TargetProfile`: hardware/toolchain baseline shared by all runs.

### Shared concepts

- Device capture session:
  - serial source (`device.path`, `baud`), timeout behavior, parsed packet count.
- Trial/run:
  - `exp_id`, `experiment_type`, `run_id`, `trial_id`, repeat index.
- Scenario tags:
  - list labels (e.g., `LoS`, `NLoS`, `multipath`, `interference`) stored on every packet.
- Ground truth fields:
  - `distance_m` for distance trials.
  - `angle_deg` for angle trials.
- Metrics placeholders:
  - manifest contains `analysis_status` and optional `analysis_notes` placeholders; capture stage does not output model results.

## Data Schemas

### Packet record (JSONL row)

Required/common fields:

- `timestamp`, `rssi`, `csi`, `esp_timestamp`, `mac` (existing parser output)
- `exp_id`, `experiment_type`, `run_id`, `trial_id`, `scenario_tags`
- `target_profile`
- `device_path`

Distance-specific field:

- `distance_m`

Angle-specific fields:

- `angle_deg`
- `array_config` (embedded minimal object for downstream AoA compatibility)

### Run manifest (`manifest.json`)

Per run, write:

- identity: `exp_id`, `experiment_type`, `run_id`, `created_at_utc`
- environment profile: `target_profile`, `environment_profile`
- reproducibility: `git_commit`, `git_dirty`, `config_snapshot`
- capture settings: `device.path`, `device.baud`, packet limits/duration
- environment metadata: `room_id`, `notes`, scenario tags
- angle geometry metadata: orientation reference and measurement position notes

## CLI Interface

New config-driven runner:

- `python -m csi_capture.experiment run --config <path>`

Convenience subcommands:

- `python -m csi_capture.experiment distance --config <path>`
- `python -m csi_capture.experiment angle --config <path>`

Compatibility:

- Existing `python -m csi_capture.capture ...` remains unchanged.
- Existing `scripts/run_rx_laptop.sh` flags and output naming remain valid.

## Folder Layout

New run layout:

```text
experiments/
  <exp_id>/
    <experiment_type>/
      run_<run_id>/
        manifest.json
        trial_<trial_id>/
          capture.jsonl
```

Distance legacy layout from `run_rx_laptop.sh` is preserved:

```text
../../private/experiments/csi_capture_characterization/runs/<exp_id>/<scenario>/run_<run_id>/distance_<X>m.jsonl
```

## Reproducibility and Metadata

- deterministic naming includes `experiment_type` and `run_id`.
- optional `run_ids` in config allows multiple runs from one command (for repeated sweeps).
- each run writes `manifest.json` with full config snapshot.
- include git revision (`git rev-parse HEAD`) and dirty flag (if detectable).
- include serial device path (default `/dev/esp32_csi`).

## Angle Experiment Specifics

Required config dimensions:

- `angles`: list of `angle_deg` values.
- `repeats_per_angle`: integer.
- capture amount: `packets_per_repeat` (and optional `duration_s` placeholder).
- scenario labels: `LoS`, `NLoS`, `multipath`, `interference` (list, free-form allowed).
- environment metadata: `room_id`, `notes`.
- array config: `num_antennas`, optional `antenna_spacing_m` (required only for AoA estimation stage).

Geometry metadata fields:

- `orientation_reference`: description of 0-degree axis and rotation convention.
- `measurement_positions`: optional list/notes for AP/receiver placement.

AoA pipeline hooks (capture only):

- preserve raw CSI arrays per packet.
- store array/geometry metadata in manifest and packet tags.
- no MUSIC/ESPRIT implementation required at capture stage.
