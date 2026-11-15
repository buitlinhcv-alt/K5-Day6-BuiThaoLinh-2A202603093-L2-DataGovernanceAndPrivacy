"""Unit tests cho T04 Sensor Sync Guard pipeline.

Test 1: Happy path — perfect sync, không dropout
  Tạo camera và IMU với timestamps đồng bộ hoàn hảo (không có offset, không có gap).
  Kỳ vọng: TẤT CẢ frame được paired (imu_t_ms không có NaN), residual < 50ms.

Test 2: Dropout detection — IMU có gap lớn
  Tạo IMU với gap 500ms ở giữa. Camera có frames trong khoảng gap đó.
  Kỳ vọng: Các frame camera rơi vào gap IMU phải có imu_t_ms = NaN (HOLD).

Cả hai tests đều KHÔNG dùng fixtures/*/gt/ — chạy được trên mọi máy không có instructor overlay.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

# Import các hàm cần test trực tiếp từ src.pipeline
from src.pipeline import decide, process


# ---------------------------------------------------------------------------
# Helpers để tạo RunContext giả mà không cần chạy src.main
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path: Path, params: dict | None = None) -> SimpleNamespace:
    """Tạo RunContext tối giản cho unit test, không cần file thật."""
    from src.common.review import AuditLog

    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = AuditLog(out_dir / "audit_log.jsonl")
    stats = {}

    ctx = SimpleNamespace(
        track="t04",
        input_dir=tmp_path / "input",
        output_dir=out_dir,
        config={
            "owner": "Test Runner",
            "strict_provenance": False,
            "params": params or {},
        },
        seed=42,
        rng=np.random.default_rng(42),
        run_name="test",
        offline=True,
        audit=audit,
        stats=stats,
    )

    # Gắn method ctx.param() và ctx.out() như RunContext thật
    def _param(key, default=None):
        return (ctx.config.get("params") or {}).get(key, default)

    def _out(name: str) -> Path:
        p = ctx.output_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    ctx.param = _param
    ctx.out = _out
    return ctx


def _make_data(
    n_cam: int,
    t_cam_ms_values: np.ndarray,
    t_imu_ms_values: np.ndarray,
    motion_score: np.ndarray | None = None,
    az_values: np.ndarray | None = None,
) -> dict:
    """Tạo data dict (camera, imu, segments) với giá trị tuỳ ý."""
    cam = pd.DataFrame({
        "frame_id": np.arange(n_cam),
        "t_cam_s": t_cam_ms_values / 1000.0,   # pipeline nhận GIÂY
        "exposure_ms": np.full(n_cam, 10.0),
        "motion_score": motion_score if motion_score is not None else np.ones(n_cam),
    })

    n_imu = len(t_imu_ms_values)
    imu = pd.DataFrame({
        "t_imu_ms": t_imu_ms_values,
        "ax": np.zeros(n_imu),
        "ay": np.zeros(n_imu),
        "az": az_values if az_values is not None else np.full(n_imu, 9.81),
        "gx": np.zeros(n_imu),
        "gy": np.zeros(n_imu),
        "gz": np.zeros(n_imu),
    })

    # segments đơn giản: 1 segment bao toàn bộ dữ liệu
    segs = pd.DataFrame({
        "segment": [0],
        "split": ["dev"],
    })

    return {"camera": cam, "imu": imu, "segments": segs, "manifest": None}


# ===========================================================================
# TEST 1: Happy path — perfect sync (không có offset, không có dropout)
# ===========================================================================

class TestNoPerfectSync:
    """T04-UT-01: Khi camera và IMU có timestamps đồng bộ hoàn hảo,
    tất cả frame phải được paired và imu_t_ms không được có NaN."""

    def test_no_dropout_all_paired(self, tmp_path):
        """
        Cho camera và IMU timestamps giống nhau (đã đồng bộ).
        Kỳ vọng:
        - Tất cả frame có imu_t_ms không phải NaN (không có dropout)
        - Residual của tất cả frame <= 50ms (tol của T04)
        - reason = 'paired' cho tất cả frame
        """
        # --- Setup ---
        # 30 frame camera tại 10 FPS (100ms mỗi frame)
        t_cam_ms = np.arange(0, 3000, 100, dtype=float)   # 0, 100, ..., 2900 ms
        n_cam = len(t_cam_ms)

        # IMU tại 100Hz (10ms mỗi mẫu) — timestamps KHỚP HOÀN TOÀN
        t_imu_ms = np.arange(0, 3000, 10, dtype=float)    # 0, 10, ..., 2990 ms

        # Motion score tạo ra tín hiệu có thể cross-correlate
        motion = np.abs(np.sin(t_cam_ms / 500.0))
        az = 9.81 + np.interp(t_imu_ms, t_cam_ms, motion) * 2.0

        ctx = _make_ctx(tmp_path, params={"dropout_tol_ms": 30})
        data = _make_data(n_cam, t_cam_ms, t_imu_ms, motion_score=motion, az_values=az)

        # --- Run ---
        result = process(ctx, data)

        # --- Assert ---
        assert isinstance(result, pd.DataFrame), "process() phải trả về DataFrame"
        assert "frame_id" in result.columns, "Phải có cột frame_id"
        assert "imu_t_ms" in result.columns, "Phải có cột imu_t_ms"

        n_null = result["imu_t_ms"].isna().sum()
        assert n_null == 0, (
            f"Khi timestamps đồng bộ hoàn hảo, không được có frame nào NaN. "
            f"Thực tế: {n_null}/{len(result)} frame là NaN"
        )

        # Kiểm tra residual (score) <= 50ms cho mọi frame
        assert "score" in result.columns, "Phải có cột score (residual)"
        max_residual = result["score"].max()
        assert max_residual <= 50.0, (
            f"Residual tối đa phải <= 50ms (T04 tolerance). "
            f"Thực tế max_residual = {max_residual:.2f}ms"
        )

    def test_output_has_required_columns(self, tmp_path):
        """process() phải trả về DataFrame có đủ 2 cột bắt buộc: frame_id, imu_t_ms."""
        t_cam_ms = np.arange(0, 1000, 100, dtype=float)
        t_imu_ms = np.arange(0, 1000, 10, dtype=float)

        ctx = _make_ctx(tmp_path)
        data = _make_data(len(t_cam_ms), t_cam_ms, t_imu_ms)
        result = process(ctx, data)

        required_cols = {"frame_id", "imu_t_ms"}
        missing = required_cols - set(result.columns)
        assert not missing, f"Thiếu cột bắt buộc: {missing}"
        assert len(result) == len(t_cam_ms), (
            f"Số dòng output phải bằng số camera frames. "
            f"Kỳ vọng {len(t_cam_ms)}, thực tế {len(result)}"
        )


# ===========================================================================
# TEST 2: Dropout detection — IMU có gap lớn
# ===========================================================================

class TestImuDropout:
    """T04-UT-02: Khi IMU có gap lớn (> dropout_tol_ms), camera frames
    rơi vào vùng gap phải được mark là NaN (HOLD)."""

    def test_imu_gap_becomes_hold(self, tmp_path):
        """
        Camera: 30 frame đều nhau từ 0→2900ms (100ms mỗi frame)
        IMU: Có gap từ 1000ms đến 1500ms (gap 500ms, lớn hơn dropout_tol_ms=30ms)

        Kỳ vọng:
        - Frame camera có t_cam ≈ 1100ms đến 1400ms → imu_t_ms = NaN
        - Frame camera ngoài gap → imu_t_ms không phải NaN
        """
        # --- Setup ---
        t_cam_ms = np.arange(0, 3000, 100, dtype=float)  # 0, 100, ..., 2900
        n_cam = len(t_cam_ms)

        # IMU với gap 1000-1500ms
        t_imu_before_gap = np.arange(0, 1000, 10, dtype=float)    # 0 → 990ms
        t_imu_after_gap  = np.arange(1500, 3000, 10, dtype=float)  # 1500 → 2990ms
        t_imu_ms = np.concatenate([t_imu_before_gap, t_imu_after_gap])

        ctx = _make_ctx(tmp_path, params={"dropout_tol_ms": 30})
        data = _make_data(n_cam, t_cam_ms, t_imu_ms)

        # --- Run ---
        result = process(ctx, data)

        # --- Identify frames in the gap ---
        # Camera frames trong khoảng gap IMU (1100ms - 1400ms)
        # Nearest IMU sample sẽ có residual lớn:
        #   t_cam=1100 → nearest IMU = 1500 (residual=400) → NaN
        #   t_cam=1200 → nearest IMU = 1500 (residual=300) → NaN
        #   t_cam=1300 → nearest IMU = 1500 (residual=200) → NaN
        #   t_cam=1400 → nearest IMU = 1500 (residual=100) → NaN
        gap_frame_ids = set(np.where((t_cam_ms >= 1100) & (t_cam_ms <= 1400))[0])

        result_indexed = result.set_index("frame_id")

        n_gap_null = 0
        n_gap_paired = 0
        for fid in gap_frame_ids:
            if fid in result_indexed.index:
                val = result_indexed.loc[fid, "imu_t_ms"]
                if pd.isna(val):
                    n_gap_null += 1
                else:
                    n_gap_paired += 1

        assert n_gap_null > 0, (
            f"Camera frames trong gap IMU (1100-1400ms) phải có imu_t_ms=NaN. "
            f"Thực tế: {n_gap_null} NaN, {n_gap_paired} paired trong tổng {len(gap_frame_ids)} gap frames"
        )

    def test_no_gap_no_hold(self, tmp_path):
        """
        Khi IMU LIÊN TỤC (không có gap) VÀ timestamps khớp nhau,
        không frame nào bị HOLD do dropout.

        Lưu ý: dùng tín hiệu có bump rõ ràng để cross-correlation không bị lệch offset
        nhiều hơn dropout_tol_ms. Camera và IMU phải có cùng epoch để residual nhỏ.
        """
        t_cam_ms = np.arange(0, 2000, 100, dtype=float)   # 20 frames
        t_imu_ms = np.arange(0, 2000, 10, dtype=float)    # 200 samples liên tục

        # Tạo tín hiệu bump rõ ràng (cả camera và IMU đều có spike cùng thời điểm)
        # → cross-correlation sẽ ước lượng offset ≈ 0 (không bị lệch sai)
        motion = 1.0 + np.abs(np.sin(2 * np.pi * t_cam_ms / 400.0))  # bump rõ
        az = 9.81 + np.interp(t_imu_ms, t_cam_ms, motion) * 3.0

        ctx = _make_ctx(tmp_path, params={"dropout_tol_ms": 30})
        data = _make_data(len(t_cam_ms), t_cam_ms, t_imu_ms,
                          motion_score=motion, az_values=az)
        result = process(ctx, data)

        n_dropout = result["imu_t_ms"].isna().sum()
        assert n_dropout == 0, (
            f"Khi IMU liên tục và timestamps đồng bộ, không được có dropout. "
            f"Thực tế {n_dropout}/{len(result)} frames bị dropout"
        )


# ===========================================================================
# TEST 3: decide() — kiểm tra logic GO/HOLD
# ===========================================================================

class TestDecidePolicy:
    """T04-UT-03: Hàm decide() phải trả GO khi accuracy >= 90%, HOLD khi dưới ngưỡng."""

    def _make_scored(self, n_total: int, n_paired: int) -> pd.DataFrame:
        """Tạo DataFrame giả với n_paired frame paired và phần còn lại dropout."""
        imu_t = [float(i * 10) for i in range(n_paired)] + [float("nan")] * (n_total - n_paired)
        scores = [5.0] * n_paired + [999.0] * (n_total - n_paired)
        return pd.DataFrame({
            "frame_id": range(n_total),
            "imu_t_ms": imu_t,
            "score": scores,
            "reason": ["paired"] * n_paired + ["dropout_hold"] * (n_total - n_paired),
        })

    def test_high_accuracy_gives_go(self, tmp_path):
        """95/100 frame paired → accuracy=0.95 >= 0.90 → GO."""
        ctx = _make_ctx(tmp_path)
        final = self._make_scored(100, 95)
        decision = decide(ctx, final)
        assert decision.status == "GO", (
            f"95% accuracy phải cho GO. Nhận được: {decision.status} ({decision.reason_code})"
        )
        assert decision.reason_code == "SYNC_OK"

    def test_low_accuracy_gives_hold(self, tmp_path):
        """50/100 frame paired → accuracy=0.50 < 0.90 → HOLD."""
        ctx = _make_ctx(tmp_path)
        final = self._make_scored(100, 50)
        decision = decide(ctx, final)
        assert decision.status == "HOLD", (
            f"50% accuracy phải cho HOLD. Nhận được: {decision.status} ({decision.reason_code})"
        )
        assert decision.reason_code == "SYNC_ACCURACY_BELOW_THRESHOLD"


class TestInputAndSplitGuards:
    def test_missing_required_input_column_fails_loudly(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        data = _make_data(10, np.arange(0, 1000, 100), np.arange(0, 1000, 10))
        data["camera"] = data["camera"].drop(columns="motion_score")
        with pytest.raises(ValueError, match="camera is missing required columns"):
            process(ctx, data)

    def test_duplicate_timestamp_fails_loudly(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        data = _make_data(10, np.arange(0, 1000, 100), np.arange(0, 1000, 10))
        data["imu"].loc[1, "t_imu_ms"] = data["imu"].loc[0, "t_imu_ms"]
        with pytest.raises(ValueError, match="strictly increasing"):
            process(ctx, data)

    def test_timestamp_reset_fails_loudly(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        data = _make_data(10, np.arange(0, 1000, 100), np.arange(0, 1000, 10))
        data["camera"].loc[5:, "t_cam_s"] -= 0.5
        with pytest.raises(ValueError, match="possible clock reset"):
            process(ctx, data)

    def test_clock_offset_sign_is_corrected_using_dev_data(self, tmp_path):
        imu_t = np.arange(0, 5000, 10, dtype=float)
        cam_recorded_t = np.arange(100, 5000, 100, dtype=float)
        def signal_at(t):
            return (np.exp(-((t - 900) / 180) ** 2) + 0.8 * np.exp(-((t - 2300) / 260) ** 2)
                    + 1.2 * np.exp(-((t - 3900) / 140) ** 2))
        camera_motion = signal_at(cam_recorded_t - 60.0)
        imu_accel = 9.81 + signal_at(imu_t)
        ctx = _make_ctx(tmp_path, params={"max_offset_ms": 150, "dropout_tol_ms": 30})
        data = _make_data(len(cam_recorded_t), cam_recorded_t, imu_t,
                          motion_score=camera_motion, az_values=imu_accel)
        result = process(ctx, data)
        assert ctx.stats["offset_ms"] == pytest.approx(-60, abs=20)
        assert result["imu_t_ms"].notna().mean() > 0.95

    def test_offset_fit_receives_dev_imu_blocks_only(self, tmp_path, monkeypatch):
        import src.pipeline as pipeline
        seen = []
        def fake_estimator(cam, imu, max_offset_ms, step_ms):
            seen.append(imu.t_imu_ms.to_numpy())
            return 0.0, {"status": "estimated", "peak_correlation": 1.0}
        monkeypatch.setattr(pipeline, "_estimate_offset", fake_estimator)
        t_cam = np.arange(0, 2000, 100, dtype=float)
        t_imu = np.arange(0, 2000, 10, dtype=float)
        data = _make_data(len(t_cam), t_cam, t_imu)
        data["segments"] = pd.DataFrame({"segment": [0, 1], "split": ["dev", "eval"]})
        ctx = _make_ctx(tmp_path, params={"segment_duration_ms": 1000})
        process(ctx, data)
        assert len(seen) == 1
        assert seen[0].max() < 1000

    def test_linear_drift_fit_uses_dev_window_centers(self, tmp_path, monkeypatch):
        import src.pipeline as pipeline
        estimates = []
        def known_offset(cam, imu, max_offset_ms, step_ms):
            center_s = cam.t_cam_ms.mean() / 1000
            offset = -2.0 - 0.4 * (center_s - 10.0)
            estimates.append((center_s, offset))
            return offset, {"status": "estimated", "peak_correlation": 1.0}
        monkeypatch.setattr(pipeline, "_estimate_offset", known_offset)
        cam_t = np.arange(0, 80000, 100, dtype=float)
        imu_t = np.arange(0, 80000, 10, dtype=float)
        data = _make_data(len(cam_t), cam_t, imu_t)
        data["segments"] = pd.DataFrame({"segment": range(4), "split": ["dev", "eval", "dev", "eval"]})
        ctx = _make_ctx(tmp_path)
        process(ctx, data)
        assert len(estimates) == 2
        assert ctx.stats["drift_ms_per_s"] == pytest.approx(-0.4)

    def test_inconsistent_dev_offsets_force_hold(self, tmp_path, monkeypatch):
        import src.pipeline as pipeline
        offsets = iter([-40.0, -80.0, 20.0])
        monkeypatch.setattr(pipeline, "_estimate_offset",
                            lambda *args: (next(offsets), {"status": "estimated", "peak_correlation": 0.9}))
        cam_t = np.arange(0, 120000, 100, dtype=float)
        imu_t = np.arange(0, 120000, 10, dtype=float)
        data = _make_data(len(cam_t), cam_t, imu_t,
                          motion_score=np.sin(cam_t / 700) + 1.2,
                          az_values=9.81 + np.sin(imu_t / 700) + 1.2)
        data["segments"] = pd.DataFrame({"segment": range(6), "split": ["dev", "eval"] * 3})
        ctx = _make_ctx(tmp_path)
        result = process(ctx, data)
        assert ctx.stats["offset_fit_status"] == "inconsistent_dev_drift"
        assert decide(ctx, result).status == "HOLD"

    def test_exact_sample_at_right_edge_of_dropout_is_pairable(self, tmp_path, monkeypatch):
        import src.pipeline as pipeline
        monkeypatch.setattr(pipeline, "_estimate_offset", lambda *args: (0.0, {"status": "estimated", "peak_correlation": 1.0}))
        cam_t = np.array([950, 1000, 1490, 1500, 1510], dtype=float)
        imu_t = np.concatenate([np.arange(0, 1000, 10), np.arange(1500, 2000, 10)]).astype(float)
        data = _make_data(len(cam_t), cam_t, imu_t)
        ctx = _make_ctx(tmp_path, params={"max_imu_gap_ms": 30})
        result = process(ctx, data).set_index("frame_id")
        assert result.loc[3, "imu_t_ms"] == 1500
        assert result.loc[3, "reason"] == "paired"

    def test_dev_decision_ignores_eval_rows(self, tmp_path):
        ctx = _make_ctx(tmp_path, params={"tol_ms": 50, "go_threshold": 0.9})
        ctx.stats["offset_fit_status"] = "estimated"
        final = pd.DataFrame({
            "frame_id": range(10),
            "imu_t_ms": [100.0] * 9 + [np.nan],
            "score": [5.0] * 9 + [999.0],
            "split": ["dev"] * 9 + ["eval"],
        })
        decision = decide(ctx, final)
        assert decision.status == "GO"
        assert ctx.stats["accuracy_proxy"] == 1.0

    def test_sparse_imu_gap_holds_frame_even_when_nearest_sample_is_close(self, tmp_path):
        t_cam = np.arange(0, 2000, 100, dtype=float)
        t_imu = np.concatenate([np.arange(0, 1000, 10), np.arange(1500, 2000, 10)]).astype(float)
        ctx = _make_ctx(tmp_path, params={"dropout_tol_ms": 30, "max_imu_gap_ms": 30})
        data = _make_data(len(t_cam), t_cam, t_imu)
        # A clear, repeatable signal allows the offset estimator to run; the first
        # frame in the dropout interval is only 10 ms from the left boundary sample.
        motion = np.sin(t_cam / 180.0) + 1.5
        data["camera"]["motion_score"] = motion
        data["imu"]["az"] = 9.81 + np.interp(t_imu, t_cam, motion)
        result = process(ctx, data).set_index("frame_id")
        assert pd.isna(result.loc[10, "imu_t_ms"])
        assert result.loc[10, "reason"] == "imu_gap_hold"

    def test_flat_signal_makes_decision_hold(self, tmp_path):
        t_cam = np.arange(0, 1000, 100, dtype=float)
        t_imu = np.arange(0, 1000, 10, dtype=float)
        ctx = _make_ctx(tmp_path, params={"dropout_tol_ms": 30})
        result = process(ctx, _make_data(len(t_cam), t_cam, t_imu))
        assert ctx.stats["offset_fit_status"] == "flat_signal_hold"
        assert decide(ctx, result).status == "HOLD"
