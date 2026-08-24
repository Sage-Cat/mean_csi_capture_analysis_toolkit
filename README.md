# CSI Capture Analysis Toolkit

`csi_capture_analysis_toolkit` provides reusable ESP32 CSI capture, parsing,
and parameterized offline analysis tools. It does not own study definitions,
experiments, datasets, models, or results.

Every writer requires an explicit session-owned destination. For example:

```bash
python3 -m csi_capture.capture --port /dev/ttyACM1 \
  --output /path/to/experiments/private/<session>/experiments/<id>/runs/<run>/raw/capture.jsonl \
  --metadata-json '{"experiment_id":"<id>","run_id":"<run>"}'
python3 tools/analyze_wifi_classification.py \
  --data_dir /path/to/experiments/private/<session>/experiments/<id>/runs \
  --out_dir /path/to/experiments/private/<session>/analysis/classification \
  --experiment_id <id> --run_id <run-a> --run_id <run-b> \
  --subject-map /path/to/session-owned/subject-map.json \
  --labels <negative> <positive> --positive_label <positive>
```

The optional subject-map schema is one JSON object whose keys exactly equal
the selected run IDs and whose values are private grouping tokens, for example
`{"<run-a>": "<group-token>", "<run-b>": "<group-token>"}`. Tokens are used
only in memory; generated artifacts contain deterministic `subject-NNN` IDs.
Without a map, only explicit raw `subject_id` values are used.

Domain analyzers expose their complete parameter contract through `--help`.
Run `make test` for the hardware-independent unit suite.
