"""Review evidence must match the current queue; demo must not reuse human decisions."""
from types import SimpleNamespace

import pandas as pd
import pytest

from src.common.review import AuditLog, make_review_queue
from src.pipeline import review
from src.t04_demo import main


def review_case(tmp_path):
    scored = pd.DataFrame({"frame_id": ["f1", "f2"], "imu_t_ms": [100., float("nan")],
                           "score": [2., 500.], "reason": ["paired", "imu_gap_hold"]})
    queue = make_review_queue(scored, "frame_id", "score", 2, reason_col="reason")
    ctx = SimpleNamespace(output_dir=tmp_path, stats={}, audit=AuditLog(tmp_path / "audit.jsonl"))
    reviewed = queue.copy()
    reviewed["reviewer"] = "SYNTHETIC UNIT TEST"
    reviewed["reviewer_decision"] = "approve"
    reviewed["review_note"] = "Synthetic evidence in a unit test; not a real human review"
    return ctx, scored, queue, reviewed


def test_approve_is_audited_without_changing_hold(tmp_path):
    ctx, scored, queue, reviewed = review_case(tmp_path)
    reviewed.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)
    final = review(ctx, scored, queue)
    pd.testing.assert_frame_equal(final, scored)
    events = ctx.audit.read()
    assert len(events) == 2 and all(e["action"] == "review:approve" for e in events)
    assert next(e for e in events if e["item_id"] == "f2")["after"] is None


@pytest.mark.parametrize("field,value", [("item_id", "old-frame"), ("score", 3.0), ("reason", "old-reason")])
def test_stale_review_rejected_before_any_audit(tmp_path, field, value):
    ctx, scored, queue, reviewed = review_case(tmp_path)
    reviewed.loc[0, field] = value
    reviewed.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)
    with pytest.raises(ValueError, match="Stale review"):
        review(ctx, scored, queue)
    assert ctx.audit.read() == []


def test_unsafe_edit_is_explicitly_rejected(tmp_path):
    ctx, scored, queue, reviewed = review_case(tmp_path)
    reviewed.loc[0, "reviewer_decision"] = "edit"
    reviewed.to_csv(tmp_path / "review_queue_reviewed.csv", index=False)
    with pytest.raises(ValueError, match="unknown reviewer_decision"):
        review(ctx, scored, queue)


def test_demo_does_not_rerun_a_reviewed_directory(tmp_path, capsys):
    folder = tmp_path / "poc"
    folder.mkdir()
    saved = folder / "review_queue_reviewed.csv"
    saved.write_text("preserve existing reviewer decisions", encoding="utf-8")
    assert main(["--output", str(tmp_path)]) == 2
    assert "Reviewed CSV exists" in capsys.readouterr().err
    assert saved.read_text(encoding="utf-8") == "preserve existing reviewer decisions"
