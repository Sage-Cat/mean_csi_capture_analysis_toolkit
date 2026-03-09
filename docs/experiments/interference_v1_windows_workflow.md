# interference_v1 Native Windows Workflow

This workflow runs the `interference_v1` experiment on native Windows without WSL.

The capture protocol is now exposed as:

- `py -3 -m csi_capture.interference_protocol`
- `scripts\run_interference_protocol.cmd`

Use plain `COM` port names such as `COM3` and `COM4`.

## 1. Install Python dependencies

From `cmd.exe` or PowerShell:

```powershell
cd C:\path\to\csi_capturing_example
py -3 -m pip install -r requirements.txt
```

Expected result:

- `pyserial`, `numpy`, `pandas`, `scikit-learn`, and `matplotlib` install successfully.

## 2. Check which COM ports are visible

```powershell
cd C:\path\to\csi_capturing_example
py -3 -m csi_capture.cli --list-devices
```

Expected result:

- You should see output like `COM3` and `COM4`.
- If no COM ports appear, check Device Manager and the USB cable.

## 3. Flash or start the TX board

Open an ESP-IDF PowerShell or Command Prompt where `idf.py` is already available:

```powershell
cd %USERPROFILE%\esp\esp-csi\examples\get-started\csi_send
idf.py set-target esp32s3
idf.py -p COM3 -b 921600 flash
```

Expected result:

- Flash completes without errors.
- The TX board restarts and begins transmitting packets.

If the firmware is already flashed and you only need to power the board, you can skip reflashing.

## 4. Flash or start the RX board

In the same ESP-IDF shell:

```powershell
cd %USERPROFILE%\esp\esp-csi\examples\get-started\csi_recv
idf.py set-target esp32s3
idf.py -p COM4 -b 921600 flash
```

Expected result:

- Flash completes without errors.
- The RX board restarts and begins emitting `CSI_DATA` lines.

## 5. Print the scenario matrix before capture

From the repository root:

```powershell
py -3 -m csi_capture.interference_protocol --list-scenarios
py -3 -m csi_capture.interference_protocol --scenario-set full --list-scenarios
```

Expected result:

- A table of scenarios is printed.
- Each scenario includes a one-line `setup:` prompt.

## 6. Run a quick preflight capture

This is the fastest way to confirm that packets are being captured before the real experiment:

```powershell
py -3 -m csi_capture.interference_protocol `
  --device COM4 `
  --exp-id exp_interference_preflight_win `
  --scenario-set core `
  --runs 1 `
  --max-records 5 `
  --yes `
  --notes "windows preflight"
```

Expected result:

- The script prints the environment banner and selected serial device.
- The dry-run reports `Dry-run success. Parsed packets: 5`.
- The script walks through all `core` scenarios.
- For each scenario, it writes:
  - `experiments/<exp_id>/meta.json`
  - `experiments/<exp_id>/<scenario_id>/run_1/capture.jsonl`
  - `experiments/<exp_id>/<scenario_id>/run_1/manifest.json`

Use `--yes` only for automated preflight. For the real experiment, omit it so the script pauses between scenarios.

## 7. Run the real `core` experiment

```powershell
py -3 -m csi_capture.interference_protocol `
  --device COM4 `
  --target-profile esp32s3_csi_v1 `
  --exp-id exp_interference_core_20260309 `
  --scenario-set core `
  --runs 3 `
  --max-records 1500 `
  --notes "room campaign core set"
```

Expected result:

- The script prints the full scenario table.
- A dry-run probe happens first unless you add `--skip-dry-run`.
- The script stops at each prompt so the operator can rearrange the room.
- Each run finishes with `Captured <N> records`.
- `N` should equal `--max-records` unless you interrupted the run.

## 8. Run the real `full` experiment

```powershell
py -3 -m csi_capture.interference_protocol `
  --device COM4 `
  --target-profile esp32s3_csi_v1 `
  --exp-id exp_interference_full_20260309 `
  --scenario-set full `
  --runs 5 `
  --max-records 2500 `
  --notes "room campaign full set"
```

Expected result:

- The script runs the `core` scenarios plus `s10` through `s13`.
- More data is written under the same folder layout.

## 9. What the script prompts mean

1. `Confirm TX is running and RX is streaming CSI_DATA. Press Enter to continue...`
   Expected action: verify TX is powered and RX firmware is active.
2. `Arrange the environment for <scenario_id> and press Enter to start...`
   Expected action: physically set up the room for that scenario.
3. `Press Enter to capture this run...`
   Expected action: freeze the setup, then start the run.
4. `Captured <N> records`
   Expected result: a run finished successfully and files were written.

## 10. Scenario-by-scenario operator checklist

### core

- `s01_ref_los_empty`
  Action: same room, clear LoS, move extra furniture away.
- `s02_los_tables_chairs`
  Action: same room, normal tables/chairs, keep LoS open.
- `s03_human_block_static`
  Action: one person stands still in the middle of the TX-RX path.
- `s04_human_motion_side`
  Action: one person walks near the link or beside RX without fully blocking LoS.
- `s05_adjacent_door_open`
  Action: adjacent-room geometry with the door open.
- `s06_adjacent_door_closed`
  Action: same geometry as `s05`, but close the door.
- `s07_one_wall`
  Action: place TX and RX in neighboring rooms with one wall between them.
- `s08_two_walls_corridor`
  Action: place TX and RX so the path crosses two walls and a corridor.
- `s09_boxes_wood_partition`
  Action: capture in a cluttered area with boxes, shelves, or wood partitions.

### full only

- `s10_chair_cluster_rx`
  Action: place a dense cluster of chairs or tables near RX.
- `s11_door_frame_offset`
  Action: use the doorway, but offset the path from the doorway center.
- `s12_corridor_people_motion`
  Action: use the corridor geometry and add human movement in the corridor.
- `s13_boxes_and_human`
  Action: keep the cluttered layout and add one person standing near RX.

## 11. Files to inspect after a run

- `experiments/<exp_id>/meta.json`
  Expected: experiment-level metadata, selected device, and scenario list.
- `experiments/<exp_id>/<scenario_id>/run_<n>/capture.jsonl`
  Expected: one JSON object per captured packet.
- `experiments/<exp_id>/<scenario_id>/run_<n>/manifest.json`
  Expected: per-run metadata including `records_captured`.

If a run reports `0` records or the dry-run fails, stop and fix the RX stream before collecting the real dataset.
