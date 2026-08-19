# Experiment Framework Documentation

Date: 2026-03-02

This folder contains the governance and engineering docs for the unified experiment framework.

## Core Documents

- Requirements: `docs/experiments/requirements.md`
- Design: `docs/experiments/design.md`
- Plan: `docs/experiments/plan.md`
- Validation: `docs/experiments/validation_report.md`

## Design Diagrams

PlantUML source + rendered PNGs:

- `docs/design/plantuml/README.md`

## Operational Playbooks

- Two-laptop static sign workflow:
  `../studies/csi_capture_characterization/runbooks/static_sign_v1_two_laptop_workflow.md`

## Target Environment Profile

Current baseline profile:

- `esp32s3_csi_v1`

List available profiles:

```bash
./tools/exp --list-target-profiles
```

## Quick Command Reference

Distance capture from config:

```bash
./tools/exp distance --config ../studies/csi_capture_characterization/configs/distance_capture.sample.json
```

Angle capture from config:

```bash
python3 -m csi_capture.experiment angle \
  --config ../studies/csi_capture_characterization/configs/angle_radial_45deg_2runs.sample.json
```

static_sign_v1 capture:

```bash
./tools/exp capture \
  --experiment static_sign_v1 \
  --target-profile esp32s3_csi_v1 \
  --label hands_up \
  --runs 5 \
  --duration 20s
```
