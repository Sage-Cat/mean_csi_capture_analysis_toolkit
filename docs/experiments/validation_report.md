# Validation Report - Common Environment Refactor

Date: 2026-03-02  
Repository: `csi_capturing_example`

## 1) Unit Tests

Command:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Result:

- `Ran 33 tests`
- `OK`

## 2) CLI Help and Profile Discovery

Commands:

```bash
./tools/exp --help
./tools/exp --list-target-profiles
```

Result:

- help includes `list-target-profiles` and `capture/train/eval` commands
- target profile list shows baseline profile `esp32s3_csi_v1`

## 3) PlantUML Rendering Verification

Command:

```bash
./scripts/generate_plantuml_pngs.sh
```

Result:

- all `docs/design/plantuml/*.puml` rendered to matching `.png` files

## 4) Backward Compatibility Spot Check

Commands:

```bash
python3 -m csi_capture.experiment --help
./tools/exp distance --help
```

Result:

- distance/angle command paths remain available
- additional optional target profile flags are additive

## 5) Hardware-dependent Validation Status

Not executed in this report:

- live serial dry-run packet validation
- full physical capture run

Reason:

- requires active TX/RX lab setup at execution time.
