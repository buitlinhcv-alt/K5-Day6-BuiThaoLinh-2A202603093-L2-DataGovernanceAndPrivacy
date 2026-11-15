# Group Report — T04 Sensor Sync Guard

> Kiểm tra ngày 2026-11-14: **chưa đủ điều kiện chốt bài**. Pipeline đã chuyển sang split theo thời gian và fit drift trên dev; guard hiện buộc HOLD khi fit không nhất quán. Metric chính thức vẫn bị chặn do thiếu GT, và bản release mới chưa được review.

## 00 · Header

| Mục | Giá trị đã kiểm tra |
|---|---|
| Group ID | 2A202603046, 2A202603093 |
| Track | T04 — Sensor Sync Guard |
| Thành viên khai báo | Vũ Văn Đăng — Core Algorithm & Pipeline Lead<br>Bùi Thảo Linh — Phân tích & Viết Báo Cáo |
| Owner cấu hình | Vu Van Dang, Bui Thao Linh |
| Commit nền trước thay đổi | f76780c; code/report hiện tại sẽ nằm trong commit mới sau khi kiểm tra |
| Tag demo-180m | Commit f76780c; tagger Test User; 2026-11-14 16:25:46 +07:00 |
| Tag final-24h | Chưa tồn tại |
| Deadline đã khai báo trước đó | 2026-11-15 17:00 GMT+7; cần đối chiếu lịch lớp |
## 01 · Bài toán và contract

Ghép camera khoảng 30 Hz với IMU 100 Hz sau khi chuyển giây sang mili-giây, ước lượng lệch clock và giữ frame nghi vấn ở trạng thái HOLD. Sai timestamp có thể làm sensor fusion sử dụng sai sự kiện.

Đã đọc toàn bộ `docs/tracks/T04.md` và `fixtures/t04_sensor_sync/README_units.md`. Đầu ra bắt buộc là `pairs.csv` với `frame_id,imu_t_ms`; ô IMU trống biểu thị HOLD. Đề yêu cầu plot offset theo thời gian, năm cặp nghi vấn để người vận hành xác nhận và drift tuyến tính ở mốc final.

## 02 · Dữ liệu và provenance

Demo chỉ đọc input `fixtures/t04_sensor_sync/`: 3.570 camera frames, 11.850 IMU samples và sáu nhãn segment. Manifest ghi **CC0-1.0**, synthetic. Khẳng định CC-BY-4.0/CARLA/nuScenes trong báo cáo cũ không được manifest hỗ trợ.

Preflight xác minh checksum của 14 fixture manifests và guard offline: 0 FAIL, 2 WARN về dữ liệu/model tùy chọn không cần cho T04. Seed 42; không tải model hoặc dùng ảnh người/biển số thật.

## 03 · Thiết kế hiện tại và điểm chưa đạt

Load + checksum → chia cửa sổ thời gian 20 giây → fit cross-correlation và offset/drift tuyến tính trên cửa sổ dev → nearest IMU + gap filtering → queue → review → decision.

Đã sửa lỗi chia theo số hàng: cửa sổ 20 giây neo theo thời gian IMU, fit dev trim vùng biên để hạn chế lẫn mẫu eval; frame vùng biên được đánh dấu boundary. Fit drift tuyến tính dùng tâm các cửa sổ dev. Nếu residual vượt `dropout_tol_ms=30`, fit bị đánh dấu không đáng tin và decision chuyển HOLD.

Giới hạn còn lại: peak tương quan cần đánh giá thêm độ mơ hồ trên dữ liệu được phép; không có GT để khẳng định offset ước lượng đúng sự kiện. Regression tests bao phủ split, drift, fit không nhất quán, ca biên IMU và tham số không hợp lệ.

## 04 · Baseline và PoC

Baseline `src.examples.t04_naive_baseline` ghép nearest timestamp và áp cutoff hữu hạn theo tolerance đã cấu hình. Baseline và PoC dùng chung input/seed/offline; thiếu GT nên chưa thể kết luận PoC cải thiện.

Phiên demo đã review: [t04_demo.json](t04_demo.json), [t04_demo.html](t04_demo.html). Bản release sạch, chưa review: [t04_release.json](t04_release.json), [t04_release.html](t04_release.html):

| Chẩn đoán toàn bộ input | Baseline | PoC |
|---|---:|---:|
| Frame có cặp IMU | 3523 | 3518 |
| Frame HOLD | 47 | 52 |
| Hiệu chỉnh clock | 0 ms | −33,33 ms tại mốc tham chiếu |
| Pair Accuracy@50ms trên GT eval | Chưa xác minh | Chưa xác minh |

HOLD là quyết định của thuật toán, không phải số dropout đúng theo GT. Không suy ra accuracy/recall/cải thiện từ số frame ghép được. Runtime của từng lần chạy nằm trong JSON/HTML.

## 05 · Đánh giá chính thức

Evaluator lấy mẫu số là frame có GT partner trên eval; HOLD ở frame có GT partner tính là sai. Thiếu `fixtures/t04_sensor_sync/gt/gt_pairs.csv`, lệnh evaluate trả exit 2 rõ ràng.

Các `metrics_baseline.json`, `metrics_poc.json`, `table1_baseline_vs_poc.md` cũ được giữ để đối chiếu, **không được coi là kết quả đã xác thực**. Baseline dùng mẫu số 3570, PoC dùng 3521; config/extra rỗng, thiếu đường dẫn GT/split mà evaluator hiện tại ghi. `src.compare` chỉ trình bày JSON, không xác minh metric. Các tuyên bố “100%”, Wilson CI và hard slices trong bản trước được rút khỏi kết luận.

## 06 · Các trường hợp cần xem

HTML hiển thị residual theo thời gian, offset các block được code gắn dev và bảng tra cứu toàn bộ frame. Queue 20 frame có timestamp đã hiệu chỉnh, IMU trái/phải, gap và lý do HOLD. Fixture T04 chỉ có metadata cảm biến, không chứa ảnh camera.

Các ca cần hoàn tất: sai đơn vị s/ms; nối qua dropout; đúng timestamp nhưng sai sự kiện; reset và drift liên tục. Code phát hiện bước lùi lớn hơn 200 ms; bước lùi nhỏ được sort và log warning. Chưa xử lý toàn bộ reset.

## 07 · Human review và quyết định

Phiên `out/t04_demo/poc` có 20/20 dòng approve và 20 ghi chú; tên reviewer được ẩn danh trong [bằng chứng công bố](review_evidence_anonymized.csv). ID/score/reason khớp queue của phiên đó, pipeline chạy offline và audit ghi 20 sự kiện `review:approve`. Vì các quyết định giữ trạng thái hiện tại nên output cặp/HOLD không đổi. Review dựa trên metadata timestamp/IMU; không có ảnh hoặc GT để xác minh sự kiện. Release mới tại `out/t04_release/poc` chưa được review và queue đã thay đổi; không dùng review cũ cho phiên này.

File cũ `out/poc/review_queue_reviewed.csv` không còn thuộc queue hiện tại và không dùng làm bằng chứng. Review edit timestamp vẫn bị từ chối vì chưa có kiểm lại timestamp/residual an toàn.

Decision release hiện HOLD do `inconsistent_dev_drift`; proxy dev 97,43% không phải accuracy và không được dùng làm metric chính thức.

## 08 · Cách chạy và xem kết quả

Xem [t04_run_guide.md](t04_run_guide.md). Chạy `python -m src.t04_demo`, mở `reports/t04_demo.html` bằng trình duyệt. Trang không dùng CDN/API/máy chủ.

Máy hiện có Python 3.14.0; README khuyến nghị 3.11–3.13, nên chưa xác nhận setup mới trên phiên bản khuyến nghị. Preflight: 0 FAIL, 2 WARN. Sau thay đổi: **70 passed, 39 skipped in 19.27s** với `OPENBLAS_NUM_THREADS=1`, `PYTHONDONTWRITEBYTECODE=1`; test cần GT được skip do thiếu overlay. Edge headless đã kiểm tra hiển thị, bộ lọc và CSV export; chi tiết ở [submission_check.md](submission_check.md).

## 09 · Giới hạn và quản trị

Không dùng PII thật, không tuyên bố đạt GDPR/luật Việt Nam. Threshold hiện có được giữ nguyên trong lượt kiểm tra; chưa dùng GT eval để tune. Thời điểm freeze ghi trong contract cũ là lời khai cần bằng chứng, không chứng minh được thứ tự freeze trước đánh giá.

Các blocker còn lại (GT chính thức, tag theo mốc, xác minh Git author) ở [submission_check.md](submission_check.md).

## 10 · Đóng góp và Git evidence

Các commit lịch sử có Git author `Test User <test@example.com>`, chưa khớp thành viên khai báo. Không sửa lịch sử hoặc giả nhận danh tính; commit mới giữ author cấu hình hiện tại. `team_contributions.csv` ghi đóng góp khai báo, không thay thế Git evidence. Tag demo cũ vẫn trỏ f76780c; chưa tạo final-24h.

## 11 · Nguồn đối chiếu

- `docs/tracks/T04.md`: contract, split thời gian, baseline, drift, review.
- `fixtures/t04_sensor_sync/README_units.md`, `manifest.csv`: đơn vị, nguồn, license.
- `src/metrics/tracks.py::eval_t04`, `src/metrics/timing.py`: metric chính thức.
- `README.md`: offline, GT guard, Git evidence và nộp report.
