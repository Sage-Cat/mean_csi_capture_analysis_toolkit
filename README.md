# CSI Capture Analysis Toolkit

`csi_capture_analysis_toolkit` provides reusable ESP32 CSI capture, parsing,
and parameterized offline analysis tools. It does not own study definitions,
experiments, datasets, models, or results.

Every writer requires an explicit experiment-owned destination. For example:

```bash
python3 -m csi_capture.capture --port /dev/ttyACM1 \
  --output /path/to/experiments/private/<experiment>/runs/<run>/raw/capture.jsonl \
  --metadata-json '{"experiment_id":"<experiment>","run_id":"<run>"}'
python3 tools/analyze_wifi_classification.py \
  --data_dir /path/to/experiments/private/<experiment>/runs \
  --out_dir /path/to/experiments/private/<experiment>/analysis/classification \
  --experiment_id <experiment> --run_id <run-a> --run_id <run-b> \
  --subject-map /path/to/experiments/private/<experiment>/setup/subject-map.json \
  --labels <negative> <positive> --positive_label <positive>
```

The optional subject-map schema is one JSON object whose keys exactly equal
the selected run IDs and whose values are private grouping tokens, for example
`{"<run-a>": "<group-token>", "<run-b>": "<group-token>"}`. Tokens are used
only in memory; generated artifacts contain deterministic `subject-NNN` IDs.
Without a map, only explicit raw `subject_id` values are used.

Domain analyzers expose their complete parameter contract through `--help`.
Run `make test` for the hardware-independent unit suite.

For a physical node-local descriptor path, copy the unchanged standard-library
exporter to the sensing host and give it that host's sealed collector archive
plus an experiment-owned output directory:

```bash
python3 tools/export_node_local_descriptors.py \
  --archive /var/lib/cws-collector/runs/<collector-run-id> \
  --output /path/owned/by/the/experiment \
  --source-id <source-id> --run-id <run-id> --node-id <node-id> \
  --execution-boundary physical-node-local
```

The exporter emits a fixed ordered 12-component descriptor, exact input/output
hashes, execution-boundary identity, serialized bytes, CPU/wall time, and peak
resident memory. Running the identical file against the copied archive supplies
the centralized-replay side of a paired-path comparison; it does not by itself
establish a scientific equivalence or performance claim.
