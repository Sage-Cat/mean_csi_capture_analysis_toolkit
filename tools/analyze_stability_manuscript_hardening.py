#!/usr/bin/env python3
"""Generate manuscript-hardening evidence tables/figures for ESP32 RSSI/CSI dataset."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", required=True, help="Experiment root with JSONL files.")
    parser.add_argument(
        "--out_dir",
        default="out/stability_statistics_hardening",
        help="Output directory for generated artifacts.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--bootstrap_iters",
        type=int,
        default=4000,
        help="Bootstrap iterations for median CIs.",
    )
    parser.add_argument(
        "--silhouette_bootstrap_iters",
        type=int,
        default=120,
        help="Bootstrap iterations for silhouette CIs.",
    )
    parser.add_argument(
        "--silhouette_bootstrap_n",
        type=int,
        default=2000,
        help="Resample size per silhouette bootstrap iteration.",
    )
    parser.add_argument(
        "--acf_max_lag",
        type=int,
        default=200,
        help="Maximum lag for first-ACF-below-threshold calculation.",
    )
    parser.add_argument(
        "--acf_threshold",
        type=float,
        default=0.2,
        help="ACF threshold used for first-lag summaries.",
    )
    parser.add_argument(
        "--run_rx_script",
        default="scripts/run_rx_laptop.sh",
        help="Path to run_rx_laptop.sh for protocol defaults extraction.",
    )
    return parser.parse_args()


def parse_run_rx_defaults(path: Path) -> dict[str, str]:
    defaults: dict[str, str] = {}
    if not path.exists():
        return defaults
    text = path.read_text(encoding="utf-8", errors="replace")
    key_map = {
        "CHANNEL": "channel",
        "BANDWIDTH_MHZ": "bandwidth_mhz",
        "PACKET_RATE_HZ": "packet_rate_hz",
        "TX_POWER_DBM": "tx_power_dbm",
        "MAX_RECORDS": "max_records",
        "BAUD": "baud",
        "TARGET": "target_chip",
    }
    for var, out_key in key_map.items():
        match = re.search(rf"^{var}=\"([^\"]+)\"", text, flags=re.MULTILINE)
        if match:
            defaults[out_key] = match.group(1)
    return defaults


def discover_jsonl_files(data_dir: Path) -> list[Path]:
    files = sorted([p for p in data_dir.rglob("*.jsonl") if p.is_file()])
    if not files:
        raise FileNotFoundError(f"No JSONL files found under {data_dir}")
    return files


def parse_distance_from_filename(path: Path) -> float:
    match = re.search(r"distance_(\d+)p(\d+)m", path.name)
    if not match:
        raise ValueError(f"Cannot parse distance from file name: {path.name}")
    return float(f"{match.group(1)}.{match.group(2)}")


def fisher_skew(values: np.ndarray) -> float:
    centered = values - np.mean(values)
    var = np.mean(centered * centered)
    if var <= 0:
        return 0.0
    return float(np.mean(centered**3) / ((var ** 1.5) + 1e-8))


def fisher_excess_kurtosis(values: np.ndarray) -> float:
    centered = values - np.mean(values)
    var = np.mean(centered * centered)
    if var <= 0:
        return 0.0
    return float((np.mean(centered**4) / ((var * var) + 1e-8)) - 3.0)


def build_packet_frame(files: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for file_path in files:
        scenario = file_path.parts[-3]
        run_id = file_path.parts[-2].split("_")[-1]
        distance_m = parse_distance_from_filename(file_path)
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                record = json.loads(line)
                csi = np.asarray(record["csi"], dtype=np.float32)
                if csi.size % 2 != 0:
                    csi = csi[:-1]
                i_vals = csi[0::2]
                q_vals = csi[1::2]
                amp = np.sqrt(i_vals * i_vals + q_vals * q_vals, dtype=np.float32)

                mean_amp = float(np.mean(amp))
                std_amp = float(np.std(amp))
                cv_amp = float(std_amp / (mean_amp + 1e-8))
                rows.append(
                    {
                        "source_file": str(file_path),
                        "scenario": scenario,
                        "run_id": str(run_id),
                        "distance_m": float(distance_m),
                        "timestamp": float(record.get("timestamp", np.nan)),
                        "esp_timestamp": float(record.get("esp_timestamp", np.nan)),
                        "rssi_dbm": float(record["rssi"]),
                        "mean_amp": mean_amp,
                        "std_amp": std_amp,
                        "cv_amp": cv_amp,
                        "skew_amp_fisher": fisher_skew(amp.astype(np.float64)),
                        "kurtosis_amp_excess_fisher": fisher_excess_kurtosis(amp.astype(np.float64)),
                        "csi_iq_int_count": int(csi.size),
                        "csi_subcarrier_count": int(amp.size),
                        "exp_id": str(record.get("exp_id", "")),
                        "mac": str(record.get("mac", "")),
                        "record_keys": ";".join(sorted(record.keys())),
                    }
                )

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("No packet rows parsed from input files.")
    frame = frame.sort_values(
        ["scenario", "run_id", "distance_m", "timestamp", "source_file"]
    ).reset_index(drop=True)
    frame["packet_idx"] = frame.groupby(["scenario", "run_id", "distance_m"]).cumcount()
    return frame


def bootstrap_median_ci(
    values: np.ndarray,
    rng: np.random.Generator,
    iters: int,
) -> tuple[float, float, float]:
    vals = np.asarray(values, dtype=np.float64)
    vals = vals[~np.isnan(vals)]
    if vals.size == 0:
        return float("nan"), float("nan"), float("nan")
    if vals.size == 1:
        v = float(vals[0])
        return v, v, v
    n = vals.size
    idx = rng.integers(0, n, size=(iters, n))
    meds = np.median(vals[idx], axis=1)
    return float(np.median(vals)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    if x.size == 0 or y.size == 0:
        return float("nan")
    diff = x[:, None] - y[None, :]
    greater = float(np.sum(diff > 0))
    less = float(np.sum(diff < 0))
    return (greater - less) / float(x.size * y.size)


def first_lag_acf_below(values: np.ndarray, max_lag: int, threshold: float) -> float:
    x = np.asarray(values, dtype=np.float64)
    x = x - np.mean(x)
    denom = float(np.dot(x, x))
    if denom <= 0:
        return float("nan")
    for lag in range(1, max_lag + 1):
        if lag >= x.size:
            return float("nan")
        acf = float(np.dot(x[:-lag], x[lag:]) / denom)
        if acf < threshold:
            return float(lag)
    return float("nan")


def kendall_tau_small(x: np.ndarray, y: np.ndarray) -> float:
    a = np.asarray(x, dtype=np.float64)
    b = np.asarray(y, dtype=np.float64)
    n = len(a)
    if n < 2:
        return float("nan")
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            sx = np.sign(a[i] - a[j])
            sy = np.sign(b[i] - b[j])
            prod = sx * sy
            if prod > 0:
                concordant += 1
            elif prod < 0:
                discordant += 1
    denom = n * (n - 1) / 2
    return float((concordant - discordant) / denom)


def save_fading_ci_figure(table: pd.DataFrame, out_path: Path) -> None:
    scenarios = table["scenario"].tolist()
    x = np.arange(len(scenarios))
    width = 0.36

    med_rssi = table["fd_rssi_median"].to_numpy(dtype=float)
    lo_rssi = table["fd_rssi_ci_low"].to_numpy(dtype=float)
    hi_rssi = table["fd_rssi_ci_high"].to_numpy(dtype=float)
    err_rssi = np.vstack([med_rssi - lo_rssi, hi_rssi - med_rssi])

    med_amp = table["fd_amp_median"].to_numpy(dtype=float)
    lo_amp = table["fd_amp_ci_low"].to_numpy(dtype=float)
    hi_amp = table["fd_amp_ci_high"].to_numpy(dtype=float)
    err_amp = np.vstack([med_amp - lo_amp, hi_amp - med_amp])

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), sharex=False)

    axes[0].bar(x, med_rssi, width=width, color="#1f77b4")
    axes[0].errorbar(x, med_rssi, yerr=err_rssi, fmt="none", ecolor="black", capsize=4, linewidth=1)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(scenarios, rotation=15)
    axes[0].set_ylabel("Median fd_rssi (dB)")
    axes[0].set_title("RSSI Fading Depth Median with 95% CI")
    axes[0].grid(axis="y", alpha=0.3)

    axes[1].bar(x, med_amp, width=width, color="#ff7f0e")
    axes[1].errorbar(x, med_amp, yerr=err_amp, fmt="none", ecolor="black", capsize=4, linewidth=1)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(scenarios, rotation=15)
    axes[1].set_ylabel("Median fd_amp (mean_amp units)")
    axes[1].set_title("CSI Mean-Amplitude Fading Depth Median with 95% CI")
    axes[1].grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)

    files = discover_jsonl_files(data_dir)
    frame = build_packet_frame(files)

    # 1) Protocol + metadata coverage table.
    defaults = parse_run_rx_defaults(Path(args.run_rx_script))
    field_nonnull = {
        "timestamp": int(frame["timestamp"].notna().sum()),
        "esp_timestamp": int(frame["esp_timestamp"].notna().sum()),
        "rssi_dbm": int(frame["rssi_dbm"].notna().sum()),
        "csi_subcarrier_count": int(frame["csi_subcarrier_count"].notna().sum()),
        "mac": int((frame["mac"] != "").sum()),
        "exp_id": int((frame["exp_id"] != "").sum()),
        "scenario": int(frame["scenario"].notna().sum()),
        "run_id": int(frame["run_id"].notna().sum()),
        "distance_m": int(frame["distance_m"].notna().sum()),
    }

    protocol_rows = [
        {"item": "phy_band", "value": "IEEE 802.11 (2.4 GHz) with ESP32-S3 csi_send/csi_recv workflow"},
        {"item": "channel_default_from_run_script", "value": defaults.get("channel", "unknown")},
        {"item": "bandwidth_mhz_default_from_run_script", "value": defaults.get("bandwidth_mhz", "unknown")},
        {"item": "packet_rate_hz_target_from_run_script", "value": defaults.get("packet_rate_hz", "unknown")},
        {"item": "tx_power_setting_from_run_script", "value": defaults.get("tx_power_dbm", "unknown")},
        {"item": "records_per_file_target_from_run_script", "value": defaults.get("max_records", "unknown")},
        {"item": "serial_baud_from_run_script", "value": defaults.get("baud", "unknown")},
        {"item": "target_chip_from_run_script", "value": defaults.get("target_chip", "unknown")},
        {"item": "observed_exp_id_values", "value": ";".join(sorted(frame["exp_id"].unique()))},
        {"item": "observed_mac_values", "value": ";".join(sorted(frame["mac"].unique()))},
        {"item": "observed_json_record_keys", "value": ";".join(sorted(set(frame["record_keys"])))},
        {
            "item": "observed_csi_iq_int_count",
            "value": ";".join(str(v) for v in sorted(frame["csi_iq_int_count"].unique())),
        },
        {
            "item": "observed_csi_subcarrier_count",
            "value": ";".join(str(v) for v in sorted(frame["csi_subcarrier_count"].unique())),
        },
        {"item": "total_packets", "value": str(int(len(frame)))},
        {"item": "num_source_files", "value": str(int(frame["source_file"].nunique()))},
        {"item": "num_scenarios", "value": str(int(frame["scenario"].nunique()))},
        {"item": "num_runs", "value": str(int(frame["run_id"].nunique()))},
        {
            "item": "distance_values_m",
            "value": ";".join(f"{d:.1f}" for d in sorted(frame["distance_m"].unique())),
        },
    ]
    for field, nonnull in field_nonnull.items():
        protocol_rows.append(
            {
                "item": f"field_coverage_{field}",
                "value": f"{nonnull}/{len(frame)}",
            }
        )
    protocol_df = pd.DataFrame(protocol_rows)
    protocol_df.to_csv(tables_dir / "table_capture_protocol_metadata.csv", index=False)

    # 2) Inter-packet interval summaries.
    interval_rows: list[dict[str, Any]] = []
    all_deltas: list[np.ndarray] = []
    for (scenario, run_id, distance_m), group in frame.groupby(["scenario", "run_id", "distance_m"]):
        ts = group.sort_values("timestamp")["timestamp"].to_numpy(dtype=np.float64)
        if ts.size < 2:
            continue
        delta = np.diff(ts)
        all_deltas.append(delta)
        interval_rows.append(
            {
                "scope": "stratum",
                "scenario": scenario,
                "run_id": run_id,
                "distance_m": distance_m,
                "n_intervals": int(delta.size),
                "median_ms": float(np.median(delta)),
                "p05_ms": float(np.percentile(delta, 5)),
                "p95_ms": float(np.percentile(delta, 95)),
                "mean_ms": float(np.mean(delta)),
                "std_ms": float(np.std(delta)),
            }
        )

    interval_df = pd.DataFrame(interval_rows)
    summary_rows: list[dict[str, Any]] = []
    all_concat = np.concatenate(all_deltas)
    summary_rows.append(
        {
            "scope": "overall",
            "scenario": "ALL",
            "n_intervals": int(all_concat.size),
            "median_ms": float(np.median(all_concat)),
            "p05_ms": float(np.percentile(all_concat, 5)),
            "p95_ms": float(np.percentile(all_concat, 95)),
            "mean_ms": float(np.mean(all_concat)),
            "std_ms": float(np.std(all_concat)),
        }
    )
    for scenario, g in interval_df.groupby("scenario"):
        pooled = np.concatenate(g.apply(lambda row: np.array([row["median_ms"]]), axis=1).to_numpy())
        # pooled above is placeholder for stable frame schema; use direct deltas aggregation below
        scenario_deltas = []
        for _, row in g.iterrows():
            stratum = frame[
                (frame["scenario"] == row["scenario"])
                & (frame["run_id"] == row["run_id"])
                & (np.isclose(frame["distance_m"], row["distance_m"]))
            ].sort_values("timestamp")
            ts = stratum["timestamp"].to_numpy(dtype=np.float64)
            scenario_deltas.append(np.diff(ts))
        d = np.concatenate(scenario_deltas)
        summary_rows.append(
            {
                "scope": "scenario",
                "scenario": scenario,
                "n_intervals": int(d.size),
                "median_ms": float(np.median(d)),
                "p05_ms": float(np.percentile(d, 5)),
                "p95_ms": float(np.percentile(d, 95)),
                "mean_ms": float(np.mean(d)),
                "std_ms": float(np.std(d)),
            }
        )
    interval_summary_df = pd.DataFrame(summary_rows)
    interval_summary_df.to_csv(tables_dir / "table_packet_interval_summary.csv", index=False)

    # 3) Fading-depth medians, CIs, and effect sizes.
    fading_group = frame.groupby(["scenario", "run_id", "distance_m"], as_index=False).agg(
        rssi_p95=("rssi_dbm", lambda x: np.percentile(x, 95)),
        rssi_p5=("rssi_dbm", lambda x: np.percentile(x, 5)),
        amp_p95=("mean_amp", lambda x: np.percentile(x, 95)),
        amp_p5=("mean_amp", lambda x: np.percentile(x, 5)),
    )
    fading_group["fd_rssi"] = fading_group["rssi_p95"] - fading_group["rssi_p5"]
    fading_group["fd_amp"] = fading_group["amp_p95"] - fading_group["amp_p5"]

    fd_rows: list[dict[str, Any]] = []
    for scenario, g in fading_group.groupby("scenario"):
        med_rssi, lo_rssi, hi_rssi = bootstrap_median_ci(
            g["fd_rssi"].to_numpy(dtype=np.float64), rng, args.bootstrap_iters
        )
        med_amp, lo_amp, hi_amp = bootstrap_median_ci(
            g["fd_amp"].to_numpy(dtype=np.float64), rng, args.bootstrap_iters
        )
        fd_rows.append(
            {
                "scenario": scenario,
                "n_strata": int(len(g)),
                "fd_rssi_median": med_rssi,
                "fd_rssi_ci_low": lo_rssi,
                "fd_rssi_ci_high": hi_rssi,
                "fd_amp_median": med_amp,
                "fd_amp_ci_low": lo_amp,
                "fd_amp_ci_high": hi_amp,
            }
        )
    fd_ci_df = pd.DataFrame(fd_rows).sort_values("scenario")
    fd_ci_df.to_csv(tables_dir / "table_fading_depth_ci_by_scenario.csv", index=False)

    los_fd = fading_group[fading_group["scenario"] == "LoS"]
    human_fd = fading_group[fading_group["scenario"] == "NLoS_human"]
    effect_df = pd.DataFrame(
        [
            {
                "comparison": "NLoS_human_vs_LoS",
                "metric": "fd_rssi",
                "cliffs_delta": cliffs_delta(
                    human_fd["fd_rssi"].to_numpy(dtype=np.float64),
                    los_fd["fd_rssi"].to_numpy(dtype=np.float64),
                ),
                "median_ratio": float(
                    np.median(human_fd["fd_rssi"].to_numpy(dtype=np.float64))
                    / np.median(los_fd["fd_rssi"].to_numpy(dtype=np.float64))
                ),
            },
            {
                "comparison": "NLoS_human_vs_LoS",
                "metric": "fd_amp",
                "cliffs_delta": cliffs_delta(
                    human_fd["fd_amp"].to_numpy(dtype=np.float64),
                    los_fd["fd_amp"].to_numpy(dtype=np.float64),
                ),
                "median_ratio": float(
                    np.median(human_fd["fd_amp"].to_numpy(dtype=np.float64))
                    / np.median(los_fd["fd_amp"].to_numpy(dtype=np.float64))
                ),
            },
        ]
    )
    effect_df.to_csv(tables_dir / "table_effect_sizes_human_vs_los.csv", index=False)

    save_fading_ci_figure(fd_ci_df, figs_dir / "fading_depth_ci_by_scenario.png")

    # 4) ACF lag summary (stratum-level, scenario medians + CIs).
    acf_rows: list[dict[str, Any]] = []
    for (scenario, run_id, distance_m), g in frame.groupby(["scenario", "run_id", "distance_m"]):
        g = g.sort_values("timestamp")
        lag_rssi = first_lag_acf_below(
            g["rssi_dbm"].to_numpy(dtype=np.float64),
            max_lag=args.acf_max_lag,
            threshold=args.acf_threshold,
        )
        lag_amp = first_lag_acf_below(
            g["mean_amp"].to_numpy(dtype=np.float64),
            max_lag=args.acf_max_lag,
            threshold=args.acf_threshold,
        )
        acf_rows.append(
            {
                "scenario": scenario,
                "run_id": run_id,
                "distance_m": float(distance_m),
                "lag_rssi": lag_rssi,
                "lag_amp": lag_amp,
            }
        )
    acf_strata_df = pd.DataFrame(acf_rows)
    acf_strata_df.to_csv(tables_dir / "table_acf_lag_by_stratum.csv", index=False)

    acf_summary_rows: list[dict[str, Any]] = []
    for scenario, g in acf_strata_df.groupby("scenario"):
        med_rssi, lo_rssi, hi_rssi = bootstrap_median_ci(
            g["lag_rssi"].to_numpy(dtype=np.float64), rng, args.bootstrap_iters
        )
        med_amp, lo_amp, hi_amp = bootstrap_median_ci(
            g["lag_amp"].to_numpy(dtype=np.float64), rng, args.bootstrap_iters
        )
        acf_summary_rows.append(
            {
                "scenario": scenario,
                "n_strata_total": int(len(g)),
                "n_strata_valid_rssi": int(np.sum(~np.isnan(g["lag_rssi"].to_numpy(dtype=np.float64)))),
                "n_strata_valid_amp": int(np.sum(~np.isnan(g["lag_amp"].to_numpy(dtype=np.float64)))),
                "lag_rssi_median": med_rssi,
                "lag_rssi_ci_low": lo_rssi,
                "lag_rssi_ci_high": hi_rssi,
                "lag_amp_median": med_amp,
                "lag_amp_ci_low": lo_amp,
                "lag_amp_ci_high": hi_amp,
            }
        )
    acf_summary_df = pd.DataFrame(acf_summary_rows).sort_values("scenario")
    acf_summary_df.to_csv(tables_dir / "table_acf_lag_ci_by_scenario.csv", index=False)

    # 5) Silhouette uncertainty (baseline definitions from current manuscript pipeline).
    labels = (frame["scenario"] != "LoS").astype(int).to_numpy()
    x_all = frame[
        ["rssi_dbm", "mean_amp", "std_amp", "cv_amp", "skew_amp_fisher", "kurtosis_amp_excess_fisher"]
    ].to_numpy(dtype=np.float64)

    feature_sets = {
        "RSSI_only": frame[["rssi_dbm"]].to_numpy(dtype=np.float64),
        "CSI_only": frame[
            ["mean_amp", "std_amp", "cv_amp", "skew_amp_fisher", "kurtosis_amp_excess_fisher"]
        ].to_numpy(dtype=np.float64),
        "Combined": x_all,
    }

    sil_rows: list[dict[str, Any]] = []
    n_total = len(frame)
    n_boot = min(args.silhouette_bootstrap_n, n_total)
    for name, x_vals in feature_sets.items():
        x_std = StandardScaler().fit_transform(x_vals)
        base_score = float(
            silhouette_score(
                x_std,
                labels,
                metric="euclidean",
                sample_size=min(10000, n_total),
                random_state=args.seed,
            )
        )
        boot_scores = []
        for b in range(args.silhouette_bootstrap_iters):
            idx = rng.integers(0, n_total, size=n_boot)
            yb = labels[idx]
            if np.unique(yb).size < 2:
                continue
            score = float(silhouette_score(x_std[idx], yb, metric="euclidean"))
            boot_scores.append(score)
        lo = float(np.percentile(boot_scores, 2.5))
        hi = float(np.percentile(boot_scores, 97.5))
        sil_rows.append(
            {
                "feature_set": name,
                "base_silhouette_los_vs_nlos": base_score,
                "bootstrap_ci_low": lo,
                "bootstrap_ci_high": hi,
                "bootstrap_replicates": int(len(boot_scores)),
                "bootstrap_sample_size": int(n_boot),
            }
        )
    sil_df = pd.DataFrame(sil_rows)
    sil_df.to_csv(tables_dir / "table_silhouette_bootstrap_ci.csv", index=False)

    # 6) Cross-run stability (scenario ordering tau across runs).
    run_rows: list[dict[str, Any]] = []

    run_metric_sources: dict[str, pd.DataFrame] = {
        "fd_rssi": fading_group[["scenario", "run_id", "distance_m", "fd_rssi"]].rename(
            columns={"fd_rssi": "value"}
        ),
        "fd_amp": fading_group[["scenario", "run_id", "distance_m", "fd_amp"]].rename(
            columns={"fd_amp": "value"}
        ),
        "acf_lag_rssi": acf_strata_df[["scenario", "run_id", "distance_m", "lag_rssi"]].rename(
            columns={"lag_rssi": "value"}
        ),
        "acf_lag_amp": acf_strata_df[["scenario", "run_id", "distance_m", "lag_amp"]].rename(
            columns={"lag_amp": "value"}
        ),
    }

    for metric, metric_df in run_metric_sources.items():
        run1 = (
            metric_df[metric_df["run_id"] == "1"]
            .groupby("scenario", as_index=False)
            .agg(run1_median=("value", "median"))
        )
        run2 = (
            metric_df[metric_df["run_id"] == "2"]
            .groupby("scenario", as_index=False)
            .agg(run2_median=("value", "median"))
        )
        merged = run1.merge(run2, on="scenario", how="inner").dropna()
        if merged.empty:
            tau = float("nan")
            order_run1 = ""
            order_run2 = ""
        else:
            tau = kendall_tau_small(
                merged["run1_median"].to_numpy(dtype=np.float64),
                merged["run2_median"].to_numpy(dtype=np.float64),
            )
            order_run1 = ";".join(
                merged.sort_values("run1_median")["scenario"].astype(str).tolist()
            )
            order_run2 = ";".join(
                merged.sort_values("run2_median")["scenario"].astype(str).tolist()
            )

        run_rows.append(
            {
                "metric": metric,
                "n_scenarios_compared": int(len(merged)),
                "kendall_tau_run1_vs_run2": tau,
                "scenario_order_run1_ascending": order_run1,
                "scenario_order_run2_ascending": order_run2,
            }
        )

    run_stability_df = pd.DataFrame(run_rows)
    run_stability_df.to_csv(tables_dir / "table_cross_run_stability_tau.csv", index=False)

    # 7) Short report.
    report_lines = [
        "# Manuscript hardening evidence report",
        "",
        f"Input data directory: `{data_dir}`",
        f"Parsed packets: {len(frame):,}",
        f"Scenarios: {', '.join(sorted(frame['scenario'].unique()))}",
        f"Runs: {', '.join(sorted(frame['run_id'].unique()))}",
        f"Distances (m): {', '.join(f'{d:.1f}' for d in sorted(frame['distance_m'].unique()))}",
        "",
        "Generated tables:",
        "- table_capture_protocol_metadata.csv",
        "- table_packet_interval_summary.csv",
        "- table_fading_depth_ci_by_scenario.csv",
        "- table_effect_sizes_human_vs_los.csv",
        "- table_acf_lag_by_stratum.csv",
        "- table_acf_lag_ci_by_scenario.csv",
        "- table_silhouette_bootstrap_ci.csv",
        "- table_cross_run_stability_tau.csv",
        "",
        "Generated figure:",
        "- fading_depth_ci_by_scenario.png",
    ]
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Outputs written to: {out_dir}")


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
