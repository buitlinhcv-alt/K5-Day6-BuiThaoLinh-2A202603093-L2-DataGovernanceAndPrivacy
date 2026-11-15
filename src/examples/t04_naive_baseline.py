"""Naive baseline for T04 (Sensor Sync Guard).

Thí nghiệm đối chứng (Baseline):
- Ghép cặp nearest-timestamp trực tiếp theo timestamp thô (không hiệu chỉnh clock offset qua cross-correlation)
- Không có bộ lọc dropout (ghép tất cả các frame vào mẫu IMU gần nhất bất kể khoảng cách)
- Đặt decision là HOLD (baseline never releases to production)

Chạy:
    python -m src.main --track t04 --pipeline src.examples.t04_naive_baseline --output out/baseline --run-name baseline --offline --seed 42
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common.context import RunContext
from ..common.decision import Decision, write_decision
from ..common.manifest import verify_manifest
from ..common.review import make_review_queue, write_review_queue
from ..metrics.tracks import get_track


def load(ctx: RunContext) -> dict:
    manifest = ctx.input_dir / "manifest.csv"
    if manifest.exists():
        problems = verify_manifest(manifest)
        if problems and ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems)")

    data = {
        "camera": pd.read_csv(ctx.input_dir / "camera.csv"),
        "imu": pd.read_csv(ctx.input_dir / "imu.csv"),
        "segments": pd.read_csv(ctx.input_dir / "segments.csv") if (ctx.input_dir / "segments.csv").exists() else None,
    }
    return data


def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    cam = data["camera"].copy()
    imu = data["imu"].copy()

    # Đơn vị: camera t_cam_s (giây) -> ms
    cam["t_cam_ms"] = (cam["t_cam_s"] * 1000.0).astype(np.float64)
    imu["t_imu_ms"] = imu["t_imu_ms"].astype(np.float64)

    cam_sorted = cam.sort_values("t_cam_ms")
    imu_sorted = imu.sort_values("t_imu_ms")

    # Baseline không ước lượng offset, nhưng dùng cutoff residual cố định
    # trước evaluation để không ghép mẫu cách camera frame quá xa.
    paired = pd.merge_asof(
        cam_sorted,
        imu_sorted[["t_imu_ms"]],
        left_on="t_cam_ms",
        right_on="t_imu_ms",
        direction="nearest",
    )

    residual = (paired["t_cam_ms"] - paired["t_imu_ms"]).abs()
    tolerance_ms = float(ctx.param("baseline_tol_ms", ctx.param("dropout_tol_ms", 30)))
    paired["imu_t_ms"] = paired["t_imu_ms"].where(residual <= tolerance_ms, other=np.nan)
    paired["score"] = residual
    paired["reason"] = np.where(residual <= tolerance_ms, "naive_nearest", "baseline_cutoff_hold")

    return paired[["frame_id", "imu_t_ms", "score", "reason"]]


def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    budget = int(ctx.param("budget", 20))
    q = make_review_queue(
        scored,
        id_col="frame_id",
        score_col="score",
        budget=budget,
        reason_col="reason",
    )
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    return scored


def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    return Decision(
        status="HOLD",
        reason_code="BASELINE_ONLY",
        reason="Naive baseline without offset correction: never release on baseline.",
        owner=ctx.config.get("owner", "baseline"),
        scope=ctx.track,
    )


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    final[spec.pred_columns + [c for c in final.columns if c not in spec.pred_columns]].to_csv(
        ctx.out(spec.pred_file), index=False
    )
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
