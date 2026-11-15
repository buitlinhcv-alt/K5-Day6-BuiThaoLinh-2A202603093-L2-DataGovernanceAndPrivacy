"""Run a fresh offline T04 demo and render inspectable results without GT.

python -m src.t04_demo
python -m src.t04_demo --refresh-only  # after the human review is applied
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

from .common.io import sha256_file


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(root: Path, html_path: Path) -> dict:
    """Join predictions to inputs by frame ID; never read GT or old metrics JSON."""
    poc_dir, baseline_dir = root / "poc", root / "baseline"
    info = _read_json(poc_dir / "run_info.json")
    base_info = _read_json(baseline_dir / "run_info.json")
    for run in (info, base_info):
        if run.get("track") != "t04" or run.get("status") != 0 or not run.get("offline"):
            raise ValueError("Report requires successful offline T04 runs")
    if info["input"] != base_info["input"] or info["seed"] != base_info["seed"]:
        raise ValueError("Baseline and PoC must use the same input and seed")
    source = Path(info["input"])
    camera = pd.read_csv(source / "camera.csv")
    imu = pd.read_csv(source / "imu.csv")
    poc = pd.read_csv(poc_dir / "pairs.csv")
    base = pd.read_csv(baseline_dir / "pairs.csv")
    for predictions in (poc, base):
        if predictions.frame_id.duplicated().any() or set(predictions.frame_id) != set(camera.frame_id):
            raise ValueError("Predictions must cover every input frame exactly once")
    merged = camera.merge(poc, on="frame_id", validate="one_to_one").merge(
        base[["frame_id", "imu_t_ms", "score"]].rename(
            columns={"imu_t_ms": "baseline_imu_ms", "score": "baseline_residual_ms"}),
        on="frame_id", validate="one_to_one")
    origin = float(imu.t_imu_ms.min())
    offset = float(info["stats"]["offset_ms"])
    merged["time_s"] = (merged.t_cam_s * 1000 - origin) / 1000
    merged["adjusted_ms"] = merged.t_cam_s * 1000 + offset
    stamps = np.sort(imu.t_imu_ms.to_numpy(dtype=float))
    idx = np.searchsorted(stamps, merged.adjusted_ms.to_numpy())
    merged["imu_left_ms"] = np.where(idx > 0, stamps[np.clip(idx - 1, 0, len(stamps) - 1)], np.nan)
    merged["imu_right_ms"] = np.where(idx < len(stamps), stamps[np.clip(idx, 0, len(stamps) - 1)], np.nan)
    merged["imu_gap_ms"] = merged.imu_right_ms - merged.imu_left_ms
    merged["hold"] = merged.imu_t_ms.isna()
    queue = pd.read_csv(poc_dir / "review_queue.csv", dtype=str).fillna("")
    # Always start a new human review blank. Existing decisions are never prefilled.
    for field in ("reviewer", "reviewer_decision", "override_value", "review_note", "reviewed_at"):
        queue[field] = ""
    fit = {}
    audit_path = poc_dir / "audit_log.jsonl"
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("action") == "xcorr_offset":
                fit = event["after"]
    reviewed_path = poc_dir / "review_queue_reviewed.csv"
    reviewed_count = 0
    if reviewed_path.exists():
        reviewed = pd.read_csv(reviewed_path, dtype=str).fillna("")
        reviewed_count = int(reviewed.reviewer_decision.ne("").sum())
    segments = pd.read_csv(source / "segments.csv")
    segment_duration_ms = float(info.get("config", {}).get("params", {}).get("segment_duration_ms", 20_000))
    fit_indices = fit.get("segment_indices", [])
    offsets = fit.get("segment_offsets_ms", [])
    fit_points = [{"time_s": (int(i) + 0.5) * segment_duration_ms / 1000, "offset_ms": o}
                  for i, o in zip(fit_indices, offsets)]
    payload = {
        "track": "T04", "official_metrics_available": False,
        "evaluation_note": "Chưa có GT overlay; chưa xác minh Pair Accuracy hoặc cải thiện so với baseline.",
        "protocol_note": "Fit offset/drift trên cửa sổ dev 20 giây, bỏ biên ±250 ms. GT overlay vẫn cần để nghiệm thu split và đánh giá chính thức.",
        "input": str(source), "output": str(root), "poc_dir": str(poc_dir),
        "seed": info["seed"], "git_sha": info.get("git_sha"), "config_hash": info.get("config_hash"),
        "input_sha256": {name: sha256_file(source / name) for name in ("camera.csv", "imu.csv", "segments.csv")},
        "summary": {"frames": len(merged), "paired": int((~merged.hold).sum()),
                    "hold": int(merged.hold.sum()), "baseline_paired": int(base.imu_t_ms.notna().sum()),
                    "offset_ms": offset, "runtime_s": info["runtime_seconds"],
                    "baseline_runtime_s": base_info["runtime_seconds"],
                    "reviewed_file_rows": reviewed_count,
                    "proxy_dev": info["stats"].get("accuracy_proxy")},
        "decision": _read_json(poc_dir / "decision.json"), "fit": fit, "fit_points": fit_points,
        "segment_duration_ms": segment_duration_ms,
        "frames": json.loads(merged.to_json(orient="records", double_precision=10)),
        "queue": json.loads(queue.to_json(orient="records")), "queue_columns": list(queue.columns),
    }
    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    # Escape script-closing input text, including an adversarial reviewer/frame ID.
    embedded = data_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = Path(__file__).with_name("t04_demo.html").read_text(encoding="utf-8")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(template.replace("__T04_DATA__", embedded), encoding="utf-8")
    html_path.with_suffix(".json").write_text(data_json + "\n", encoding="utf-8")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("out/t04_demo"))
    parser.add_argument("--html", type=Path, default=Path("reports/t04_demo.html"))
    parser.add_argument("--refresh-only", action="store_true", help="Render existing outputs after review")
    args = parser.parse_args(argv)
    try:
        if not args.refresh_only:
            if (args.output / "poc/review_queue_reviewed.csv").exists():
                raise ValueError("Reviewed CSV exists. Apply it using src.main, then --refresh-only, or select a new --output.")
            env = dict(os.environ, OPENBLAS_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
            for name, pipeline in (("baseline", "src.examples.t04_naive_baseline"), ("poc", "src.pipeline")):
                command = [sys.executable, "-m", "src.main", "--config", "configs/lab.yaml", "--track", "t04",
                           "--input", "fixtures/t04_sensor_sync", "--output", str(args.output / name),
                           "--run-name", name, "--pipeline", pipeline, "--offline", "--seed", "42"]
                result = subprocess.run(command, env=env, check=False)
                if result.returncode:
                    return result.returncode
        data = build_report(args.output, args.html)
    except (ValueError, KeyError, OSError) as error:
        print(f"[t04_demo] {error}", file=sys.stderr)
        return 2
    print(json.dumps(data["summary"], indent=2))
    print(f"[t04_demo] Open: {args.html.resolve()}")
    print("[t04_demo] Diagnostics only. Official accuracy and protocol sign-off remain pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
