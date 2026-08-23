# CSI Capture Analysis Toolkit

`csi_capture_analysis_toolkit` provides reusable ESP32 CSI capture, parsing,
and offline analysis tools. It does not own experiments or data.

Every writer requires an explicit session-owned destination. For example:

```bash
python3 -m csi_capture.capture --port /dev/ttyACM1 \
  --output /path/to/experiments/private/<session>/experiments/<id>/runs/<run>/raw/capture.jsonl
python3 tools/analyze_wifi_distance_measurement.py --data_dir <session-input> \
  --out_dir /path/to/experiments/private/<session>/analysis/distance
```

Run `make test` to execute the hardware-independent suite.
