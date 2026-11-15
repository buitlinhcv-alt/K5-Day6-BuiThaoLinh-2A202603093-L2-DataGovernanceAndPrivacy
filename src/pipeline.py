"""T04 Sensor Sync Guard — Pipeline của Vũ Văn Đăng (Core Algorithm & Pipeline Lead)

Lab contract = 5 automated steps, run by `python -m src.main ...`:
    01 load      input + provenance check (manifest.csv)
    02 process   cross-correlation offset estimation + merge_asof pairing + dropout detection
    03 rank      ưu tiên các frame có residual cao nhất (review first)
    04 review    human-in-the-loop: queue -> reviewer decisions -> overrides + audit log
    05 decide    GO / HOLD dựa trên Pair Accuracy@50ms trên dev split

Track T04 output: out/pairs.csv với cột [frame_id, imu_t_ms]
  - imu_t_ms = NaN → frame bị HOLD (IMU dropout hoặc residual quá lớn)
  - Metric chính: Pair Accuracy@50ms (higher is better)

Rules: không đọc fixtures/*/gt/; seed qua ctx.rng; không gọi mạng; fail loud.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import interpolate, signal

from .common.context import RunContext
from .common.decision import Decision, write_decision
from .common.manifest import verify_manifest
from .common.review import load_review_decisions, make_review_queue, write_review_queue
from .metrics.tracks import get_track


# ---------------------------------------------------------------- 01 load
def load(ctx: RunContext) -> dict:
    """Load camera.csv, imu.csv, segments.csv + kiểm tra provenance."""
    manifest = ctx.input_dir / "manifest.csv"
    problems = verify_manifest(manifest) if manifest.exists() else [{"problem": "no_manifest"}]
    if problems:
        ctx.audit.log(actor="pipeline", action="provenance_problems", item_id=str(manifest), after=problems[:20])
        if ctx.config.get("strict_provenance", True):
            raise ValueError(f"provenance check failed ({len(problems)} problems), first: {problems[:3]}")

    data: dict = {"manifest": pd.read_csv(manifest) if manifest.exists() else None}

    # Load T04 tables — chỉ load khi đúng track T04
    # (track khác: process() sẽ raise NotImplementedError — giữ behaviour template)
    if ctx.track == "t04":
        data["camera"] = pd.read_csv(ctx.input_dir / "camera.csv")
        data["imu"] = pd.read_csv(ctx.input_dir / "imu.csv")
        data["segments"] = pd.read_csv(ctx.input_dir / "segments.csv")
        ctx.audit.log(actor="pipeline", action="load_ok",
                      item_id="t04",
                      after={
                          "n_camera": len(data["camera"]),
                          "n_imu": len(data["imu"]),
                          "n_segments": len(data["segments"]),
                      })
    return data


# ---------------------------------------------------------------- 02 process
def process(ctx: RunContext, data: dict) -> pd.DataFrame:
    """
    T04 Algorithm:
    1. Unit conversion: t_cam_s → t_cam_ms
    2. Fit offset in time-bounded dev windows; estimate linear drift from dev windows
    3. Apply the dev-only offset/drift model and find nearest IMU timestamps
    4. HOLD out-of-range frames, large residuals, and frames inside wide IMU gaps
    6. Score column: residual (cao = đáng ngờ → xếp ưu tiên review)
    """
    # Nếu không phải track T04, raise NotImplementedError (preserve template behavior)
    if ctx.track != "t04":
        spec = get_track(ctx.track)
        raise NotImplementedError(
            f"Implement process() for {spec.track} ({spec.title}). It must produce a DataFrame with at least "
            f"{spec.pred_columns} (saved as out/{spec.pred_file}). See docs/tracks/{spec.track.upper()}.md"
        )

    cam = data["camera"].copy()
    imu = data["imu"].copy()
    segs = data["segments"].copy()
    _require_columns(cam, {"frame_id", "t_cam_s", "motion_score"}, "camera")
    _require_columns(imu, {"t_imu_ms", "az"}, "imu")
    if "segment" not in segs.columns and "seg_id" in segs.columns:
        segs = segs.rename(columns={"seg_id": "segment"})
    _require_columns(segs, {"segment", "split"}, "segments")
    if cam.empty or imu.empty or segs.empty:
        raise ValueError("T04 camera, imu, and segments inputs must be non-empty")
    if ctx.param("camera_ts_unit", "s") != "s" or ctx.param("imu_ts_unit", "ms") != "ms":
        raise ValueError("T04 currently requires camera_ts_unit='s' and imu_ts_unit='ms'")
    if cam["frame_id"].isna().any() or cam["frame_id"].duplicated().any():
        raise ValueError("camera.frame_id must be present and unique")

    cam["t_cam_ms"] = pd.to_numeric(cam["t_cam_s"], errors="coerce") * 1000.0
    imu["t_imu_ms"] = pd.to_numeric(imu["t_imu_ms"], errors="coerce")
    cam["motion_score"] = pd.to_numeric(cam["motion_score"], errors="coerce")
    imu["az"] = pd.to_numeric(imu["az"], errors="coerce")
    for frame, col, label in ((cam, "t_cam_ms", "camera.t_cam_s"), (cam, "motion_score", "camera.motion_score"),
                              (imu, "t_imu_ms", "imu.t_imu_ms"), (imu, "az", "imu.az")):
        if not np.isfinite(frame[col].to_numpy(dtype=float)).all():
            raise ValueError(f"{label} must contain only finite numeric values")
    # Guard against duplicates (must be strictly increasing) and major clock resets (>200ms backwards)
    cam_diff = np.diff(cam.t_cam_ms.to_numpy(dtype=float))
    imu_diff = np.diff(imu.t_imu_ms.to_numpy(dtype=float))
    if (np.abs(cam_diff) < 1e-9).any() or (np.abs(imu_diff) < 1e-9).any():
        raise ValueError("camera and IMU timestamps must be strictly increasing; duplicate timestamps detected")
    if (cam_diff < -200.0).any() or (imu_diff < -200.0).any():
        raise ValueError("camera or IMU timestamps dropped sharply; possible clock reset")
    if (cam_diff < 0).any():
        ctx.audit.log(actor="pipeline", action="warning:non_monotonic_camera_ts", item_id="t04",
                      reason="Camera timestamps have slight jitter across boundary; sorting by timestamp")
    if (imu_diff < 0).any():
        ctx.audit.log(actor="pipeline", action="warning:non_monotonic_imu_ts", item_id="t04",
                      reason="IMU timestamps have slight jitter across boundary; sorting by timestamp")
    cam = cam.sort_values("t_cam_ms").reset_index(drop=True)
    imu = imu.sort_values("t_imu_ms").reset_index(drop=True)
    segs["segment"] = pd.to_numeric(segs["segment"], errors="coerce")
    if not np.isfinite(segs.segment.to_numpy(dtype=float)).all() or segs.segment.duplicated().any():
        raise ValueError("segments.segment must contain unique numeric IDs")
    segs = segs.sort_values("segment").reset_index(drop=True)
    splits = segs["split"].astype(str).str.lower()
    if not splits.isin({"dev", "eval"}).all() or not splits.eq("dev").any():
        raise ValueError("segments.split must contain dev/eval labels and at least one dev segment")

    dropout_tol_ms = float(ctx.param("dropout_tol_ms", 30))
    max_offset_ms = float(ctx.param("max_offset_ms", 250))
    step_ms = float(ctx.param("xcorr_step_ms", 10))
    max_imu_gap_ms = float(ctx.param("max_imu_gap_ms", 30))
    segment_duration_ms = float(ctx.param("segment_duration_ms", 20_000))
    if not np.isfinite([dropout_tol_ms, max_offset_ms, step_ms, max_imu_gap_ms, segment_duration_ms]).all() or min(
        dropout_tol_ms, max_offset_ms, step_ms, max_imu_gap_ms, segment_duration_ms
    ) <= 0:
        raise ValueError("T04 tolerance, max offset, grid step, and max IMU gap must be positive")

    # Segment ownership comes from the documented 20-second clock windows,
    # not row counts (which change when either stream drops samples). Exclude a
    # max-offset margin at each edge so clock skew cannot leak adjacent eval data.
    origin_ms = float(imu.t_imu_ms.iloc[0])
    boundary_margin_ms = min(max_offset_ms, segment_duration_ms * 0.1)
    fit_results = []
    for index, split in enumerate(splits):
        if split != "dev":
            continue
        start_ms = origin_ms + index * segment_duration_ms
        end_ms = start_ms + segment_duration_ms
        cam_window = cam[(cam.t_cam_ms >= start_ms + boundary_margin_ms) &
                         (cam.t_cam_ms < end_ms - boundary_margin_ms)]
        imu_window = imu[(imu.t_imu_ms >= start_ms + boundary_margin_ms) &
                         (imu.t_imu_ms < end_ms - boundary_margin_ms)]
        offset, info = _estimate_offset(cam_window, imu_window, max_offset_ms, step_ms)
        fit_results.append((index, offset, info))
    successful_fits = [(index, offset, info) for index, offset, info in fit_results
                       if info["status"] == "estimated"]
    if successful_fits:
        centers_s = np.asarray([(index + 0.5) * segment_duration_ms / 1000.0
                                for index, _, _ in successful_fits])
        fit_offsets = np.asarray([offset for _, offset, _ in successful_fits], dtype=float)
        reference_s = float(np.mean(centers_s))
        if len(successful_fits) >= 2:
            drift_ms_per_s, reference_offset = np.polyfit(centers_s - reference_s, fit_offsets, 1)
            fit_residuals = fit_offsets - (reference_offset + drift_ms_per_s * (centers_s - reference_s))
        else:
            drift_ms_per_s, reference_offset = 0.0, float(fit_offsets[0])
            fit_residuals = np.zeros(1)
        offset_ms = float(reference_offset)
        expected_dev_fits = int(splits.eq("dev").sum())
        fit_status = "estimated" if expected_dev_fits < 2 or len(successful_fits) >= 2 else "insufficient_dev_segment_fits"
        if float(np.max(np.abs(fit_residuals))) > dropout_tol_ms:
            fit_status = "inconsistent_dev_drift"
        fit_info = {"status": fit_status, "n_dev_segments_fit": len(successful_fits),
                    "segment_indices": [int(index) for index, _, _ in successful_fits],
                    "segment_offsets_ms": fit_offsets.tolist(),
                    "peak_correlations": [info["peak_correlation"] for _, _, info in successful_fits],
                    "reference_time_s": reference_s, "reference_offset_ms": offset_ms,
                    "drift_ms_per_s": float(drift_ms_per_s),
                    "dev_fit_residuals_ms": fit_residuals.tolist(),
                    "max_dev_fit_residual_ms": float(np.max(np.abs(fit_residuals)))}
    else:
        offset_ms = 0.0
        drift_ms_per_s, reference_s = 0.0, 0.0
        fit_info = {"status": fit_results[0][2]["status"], "n_dev_segments_fit": 0,
                    "segment_statuses": [info["status"] for _, _, info in fit_results]}
    if not successful_fits:
        drift_ms_per_s = 0.0
    ctx.audit.log(actor="pipeline", action="xcorr_offset", item_id="t04",
                  after={**fit_info, "offset_ms": offset_ms, "fit_split": "dev"})

    camera_raw_ms = cam.t_cam_ms.to_numpy(dtype=float)
    cam_times = camera_raw_ms + offset_ms + drift_ms_per_s * (
        camera_raw_ms - (origin_ms + reference_s * 1000.0)) / 1000.0
    imu_times = imu.t_imu_ms.to_numpy(dtype=float)
    if len(imu_times) < 2:
        raise ValueError("IMU input must contain at least two samples")
    right = np.searchsorted(imu_times, cam_times, side="left")
    left = right - 1
    valid_bounds = (left >= 0) & (right < len(imu_times))
    nearest = np.full(len(cam), -1, dtype=int)
    nearest[valid_bounds] = np.where(
        np.abs(imu_times[right[valid_bounds]] - cam_times[valid_bounds]) <
        np.abs(imu_times[left[valid_bounds]] - cam_times[valid_bounds]),
        right[valid_bounds], left[valid_bounds])
    after_start = (left < 0) & (right < len(imu_times)) & (cam_times >= imu_times[0])
    before_end = (right >= len(imu_times)) & (left >= 0) & (cam_times <= imu_times[-1])
    nearest[after_start] = right[after_start]
    nearest[before_end] = left[before_end]
    # Permit exact matches at either endpoint while still holding out-of-range frames.
    nearest[cam_times == imu_times[0]] = 0
    nearest[cam_times == imu_times[-1]] = len(imu_times) - 1
    candidate = np.full(len(cam), np.nan)
    has_candidate = nearest >= 0
    candidate[has_candidate] = imu_times[nearest[has_candidate]]
    residual = np.abs(cam_times - candidate)
    bracket_gap = np.full(len(cam), np.inf)
    exact = (right < len(imu_times)) & (imu_times[np.minimum(right, len(imu_times) - 1)] == cam_times)
    interior = (left >= 0) & (right < len(imu_times))
    bracket_gap[interior] = imu_times[right[interior]] - imu_times[left[interior]]
    bracket_gap[after_start] = imu_times[1] - imu_times[0]
    bracket_gap[before_end] = imu_times[-1] - imu_times[-2]
    exact_positions = np.flatnonzero(exact)
    exact_gap = np.where(exact_positions < len(imu_times) - 1,
                         imu_times[np.minimum(right[exact], len(imu_times) - 2) + 1] - imu_times[right[exact]],
                         imu_times[-1] - imu_times[-2])
    bracket_gap[exact] = exact_gap
    acceptable = has_candidate & (residual <= dropout_tol_ms) & ((bracket_gap <= max_imu_gap_ms) | exact)
    imu_t_ms = np.where(acceptable, candidate, np.nan)
    reasons = np.where(~has_candidate, "outside_imu_range",
                       np.where(bracket_gap > max_imu_gap_ms, "imu_gap_hold",
                                np.where(residual > dropout_tol_ms, "residual_hold", "paired")))
    scores = np.where(has_candidate, residual, 999.0)
    segment_index = np.floor((camera_raw_ms - origin_ms) / segment_duration_ms).astype(int)
    segment_index = np.clip(segment_index, 0, len(segs) - 1)
    segment_splits = np.full(len(cam), "boundary", dtype=object)
    within_margin = ((camera_raw_ms >= origin_ms + segment_index * segment_duration_ms + boundary_margin_ms) &
                     (camera_raw_ms < origin_ms + (segment_index + 1) * segment_duration_ms - boundary_margin_ms))
    segment_splits[within_margin] = splits.to_numpy()[segment_index[within_margin]]
    result = pd.DataFrame({"frame_id": cam.frame_id.to_numpy(), "imu_t_ms": imu_t_ms,
                           "score": scores, "reason": reasons, "split": segment_splits})
    ctx.stats.update(n_paired=int(acceptable.sum()), n_dropout=int((~acceptable).sum()),
                     offset_ms=float(offset_ms), offset_fit_split="dev", offset_fit_status=fit_info["status"])
    ctx.stats["drift_ms_per_s"] = float(drift_ms_per_s)
    ctx.stats["max_dev_fit_residual_ms"] = float(fit_info.get("max_dev_fit_residual_ms", 0.0))
    ctx.audit.log(actor="pipeline", action="process_summary", item_id="t04", after={
        "n_total": len(result), "n_paired": ctx.stats["n_paired"], "n_dropout": ctx.stats["n_dropout"],
        "offset_ms": offset_ms, "dropout_tol_ms": dropout_tol_ms, "max_imu_gap_ms": max_imu_gap_ms})
    return result


def _require_columns(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _estimate_offset(cam: pd.DataFrame, imu: pd.DataFrame, max_offset_ms: float,
                     step_ms: float) -> tuple[float, dict]:
    """Estimate camera clock correction from dev-only signals, within a bounded lag."""
    if len(cam) < 5 or len(imu) < 10:
        return 0.0, {"status": "insufficient_dev_samples", "peak_correlation": None}
    lo = max(cam.t_cam_ms.min(), imu.t_imu_ms.min()) - max_offset_ms
    hi = min(cam.t_cam_ms.max(), imu.t_imu_ms.max()) + max_offset_ms
    grid = np.arange(lo, hi + step_ms, step_ms)
    if len(grid) < 10:
        return 0.0, {"status": "insufficient_overlap", "peak_correlation": None}
    cam_signal = interpolate.interp1d(cam.t_cam_ms, cam.motion_score, bounds_error=False,
                                      fill_value=np.nan)(grid)
    imu_signal = interpolate.interp1d(imu.t_imu_ms, np.abs(imu.az - 9.81), bounds_error=False,
                                      fill_value=np.nan)(grid)
    # Keep only actual overlap, so padded zeros at stream boundaries cannot dominate.
    valid = np.isfinite(cam_signal) & np.isfinite(imu_signal)
    if valid.sum() < 10:
        return 0.0, {"status": "insufficient_overlap", "peak_correlation": None}
    a, b = cam_signal[valid], imu_signal[valid]
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0, {"status": "flat_signal_hold", "peak_correlation": None}
    a = (a - a.mean()) / a.std()
    b = (b - b.mean()) / b.std()
    corr = signal.correlate(b, a, mode="full", method="fft")
    lags = signal.correlation_lags(len(b), len(a), mode="full")
    bounded = np.abs(lags * step_ms) <= max_offset_ms
    corr, lags = corr[bounded], lags[bounded]
    best = int(np.argmax(corr))
    # correlate(IMU, camera) returns the correction needed for camera timestamps.
    peak = float(corr[best] / max(len(a), 1))
    return float(lags[best] * step_ms), {"status": "estimated", "lag_steps": int(lags[best]),
                                           "peak_correlation": peak, "grid_step_ms": step_ms}


# ---------------------------------------------------------------- 03 rank
def rank(ctx: RunContext, scored: pd.DataFrame) -> pd.DataFrame:
    """Top-B frames theo score (residual cao nhất = suspicious nhất)."""
    budget = int(ctx.param("budget", 20))
    q = make_review_queue(
        scored,
        id_col="frame_id",
        score_col="score",
        budget=budget,
        reason_col="reason" if "reason" in scored.columns else None,
    )
    write_review_queue(q, ctx.out("review_queue.csv"))
    return q


# ---------------------------------------------------------------- 04 review
def review(ctx: RunContext, scored: pd.DataFrame, queue: pd.DataFrame) -> pd.DataFrame:
    """Áp reviewer overrides nếu có file review_queue_reviewed.csv.

    - `reject` → imu_t_ms = NaN (force HOLD frame)
    - `approve` → giữ nguyên giá trị hiện tại
    Edits are not accepted until timestamp and residual revalidation is implemented.
    """
    reviewed_path = ctx.output_dir / "review_queue_reviewed.csv"
    if not reviewed_path.exists():
        return scored

    reviewed = load_review_decisions(reviewed_path, allowed={"approve", "reject"})
    if reviewed.item_id.duplicated().any():
        raise ValueError("Review contains duplicate item_id values")
    current = queue.set_index("item_id")
    for row in reviewed.itertuples(index=False):
        if row.item_id not in current.index:
            raise ValueError(f"Stale review: {row.item_id} is not in the current queue; review the new queue")
        expected = current.loc[row.item_id]
        if not np.isclose(float(row.score), float(expected.score), rtol=0, atol=1e-6) or row.reason != expected.reason:
            raise ValueError(f"Stale review evidence for {row.item_id}; review the new queue")
    ctx.stats["n_reviewed"] = len(reviewed)

    # Xử lý `reject`: force imu_t_ms = NaN
    result = scored.copy()
    result["frame_id"] = result["frame_id"].astype(str)
    for r in reviewed.itertuples(index=False):
        if r.reviewer_decision == "approve":
            value = result.loc[result.frame_id == str(r.item_id), "imu_t_ms"].iloc[0]
            value = float(value) if pd.notna(value) else None
            ctx.audit.log(actor=r.reviewer, action="review:approve", item_id=r.item_id,
                          before=value, after=value, reason=r.review_note,
                          reviewed_at=r.reviewed_at)
        if r.reviewer_decision == "reject":
            mask = result["frame_id"] == str(r.item_id)
            if mask.any():
                before = result.loc[mask, "imu_t_ms"].tolist()
                result.loc[mask, "imu_t_ms"] = np.nan
                result.loc[mask, "reason"] = "reviewer_reject"
                ctx.audit.log(
                    actor=r.reviewer,
                    action="override:reject",
                    item_id=r.item_id,
                    before=float(before[0]) if len(before) == 1 and pd.notna(before[0]) else None,
                    after=None,
                    reason=r.review_note,
                    reviewed_at=r.reviewed_at,
                )

    return result


# ---------------------------------------------------------------- 05 decide
def decide(ctx: RunContext, final: pd.DataFrame) -> Decision:
    """
    Chính sách GO/HOLD dựa trên Pair Accuracy@50ms trên dev split.
    Ngưỡng được đóng băng TRƯỚC khi nhìn kết quả eval.

    GO nếu: accuracy_dev >= 0.90 (90% frame có imu_t_ms hợp lệ và residual ≤ 50ms)
    """
    tol_ms = float(ctx.param("tol_ms", 50))
    go_threshold = float(ctx.param("go_threshold", 0.90))

    # Tính accuracy trên toàn bộ predictions (không có GT ở đây, chỉ dùng residual)
    # Proxy: frame paired và score <= tol_ms (residual ≤ 50ms)
    dev = final[final["split"].astype(str).str.lower() == "dev"] if "split" in final else final
    valid = dev["imu_t_ms"].notna() & (dev["score"] <= tol_ms)
    n_total = len(dev)
    n_valid = int(valid.sum())
    accuracy_proxy = n_valid / max(n_total, 1)
    fit_failed = ctx.stats.get("offset_fit_status") not in (None, "estimated")

    if accuracy_proxy >= go_threshold and not fit_failed:
        status = "GO"
        reason_code = "SYNC_OK"
        reason = (
            f"Pair Accuracy@{tol_ms:g}ms proxy = {accuracy_proxy:.1%} >= {go_threshold:.0%} threshold. "
            f"{n_valid}/{n_total} frames paired và residual ≤ {tol_ms}ms."
        )
    else:
        status = "HOLD"
        reason_code = "SYNC_ACCURACY_BELOW_THRESHOLD"
        reason = (
            f"Pair Accuracy@{tol_ms:g}ms proxy = {accuracy_proxy:.1%}; threshold={go_threshold:.0%}. "
            f"Offset fit status={ctx.stats.get('offset_fit_status', 'unknown')}. {n_valid}/{n_total} dev frames OK. "
            f"Dropout nhiều hoặc clock offset chưa hội tụ. Kiểm tra lại IMU data."
        )

    ctx.stats["accuracy_proxy"] = round(accuracy_proxy, 4)
    ctx.stats["n_valid_pairs"] = n_valid
    ctx.audit.log(
        actor="pipeline",
        action=f"decide:{status}",
        item_id="t04",
        after={
            "status": status,
            "reason_code": reason_code,
            "accuracy_proxy": accuracy_proxy,
            "go_threshold": go_threshold,
        },
    )

    return Decision(
        status=status,
        reason_code=reason_code,
        reason=reason,
        owner=ctx.config.get("owner", "Vu Van Dang (Core Algorithm & Pipeline Lead)"),
        scope=ctx.track,
    )


def run(ctx: RunContext) -> None:
    spec = get_track(ctx.track)
    data = load(ctx)
    scored = process(ctx, data)
    ctx.stats["n_items"] = len(scored)
    queue = rank(ctx, scored)
    final = review(ctx, scored, queue)
    # Đảm bảo cột pred đứng đầu, các cột phụ theo sau
    pred_cols = spec.pred_columns
    extra_cols = [c for c in final.columns if c not in pred_cols]
    final[pred_cols + extra_cols].to_csv(ctx.out(spec.pred_file), index=False)
    write_decision(decide(ctx, final), ctx.out("decision.json"), ctx.out("decision_log.csv"))
