# Chạy và xem T04 trên Windows

Mở PowerShell tại thư mục gốc dự án. Các lệnh dưới dùng môi trường `.venv` đã có; không tải thêm package hoặc model. README khuyến nghị Python 3.11–3.13; máy hiện đang dùng 3.14.0 và đã qua kiểm tra thực tế, chưa xác nhận setup mới trên phiên bản khuyến nghị.

## 1. Chạy baseline + PoC và mở kết quả

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m src.t04_demo
Start-Process .\reports\t04_demo.html
```

Hai pipeline chạy `--offline --seed 42`, đọc input gốc trong `fixtures/t04_sensor_sync`. Demo không dùng review cũ ở `out/poc/`; output mới ở `out/t04_demo/baseline/` và `out/t04_demo/poc/`. HTML/JSON báo cáo được sinh trong `reports/`. Khi thư mục mặc định đã có review, dùng thư mục phiên bản mới:

```powershell
.\.venv\Scripts\python.exe -m src.t04_demo --output out/t04_release --html reports/t04_release.html
Start-Process .\reports\t04_release.html
```

Tránh gọi `make run` hoặc `make baseline` trần cho bài này: Makefile mặc định TRACK=t02 và baseline target dùng random baseline không hỗ trợ T04. Các lệnh Python trong hướng dẫn chọn đúng T04 và baseline riêng.

Trong trang HTML:

1. Xem số frame paired/HOLD, offset và runtime.
2. Xem biểu đồ residual theo thời gian và offset của các block được code gắn dev.
3. Chọn “Chỉ HOLD”, nhập ID như `f01368` để xem metadata.
4. Xem 20 frame trong queue: timestamp đã chỉnh, hai mẫu IMU kẹp frame, độ rộng gap.

HTML mở trực tiếp từ đĩa, không cần server hoặc Internet. **Các số liệu là chẩn đoán, chưa phải accuracy** vì thiếu ground truth. Bản release mới đang HOLD do fit drift trên dev không nhất quán; đây là hành vi an toàn, chưa phải nghiệm thu chất lượng.

## 2. Thực hiện review người thật

Nhập tên bạn ở mục 4. Với từng frame đã xem, chọn “Giữ kết quả hiện tại” hoặc “Buộc HOLD”, ghi bằng chứng. Không điền quyết định cho frame chưa xem. Giữ kết quả của một frame đang HOLD sẽ giữ HOLD.

Bấm **Xuất CSV review**. Chép file tải về `review_queue_reviewed.csv` vào:

```text
out/t04_demo/poc/review_queue_reviewed.csv
```

Sau đó chạy:

```powershell
.\.venv\Scripts\python.exe -m src.main --track t04 --input fixtures/t04_sensor_sync --output out/t04_demo/poc --run-name poc --offline --seed 42
.\.venv\Scripts\python.exe -m src.t04_demo --refresh-only
Start-Process .\reports\t04_demo.html
```

Kiểm tra `out/t04_demo/poc/audit_log.jsonl`: approve được ghi `review:approve`; reject được ghi `override:reject`. Pipeline từ chối ID/score/reason không khớp queue hiện tại. Edit bị từ chối cho tới khi có bước kiểm lại timestamp/residual.

Đừng chạy lại chế độ demo sạch vào thư mục đã có review: script sẽ báo lỗi bảo vệ. Nếu cần phiên demo mới, dùng `--output out/t04_demo_next --html reports/t04_demo_next.html`; khi refresh truyền lại `--output out/t04_demo_next --html reports/t04_demo_next.html`.

## 3. Lệnh contract và kiểm tra

Lệnh nộp PoC theo contract:

```powershell
.\.venv\Scripts\python.exe -m src.main --track t04 --input fixtures/t04_sensor_sync --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
```

**Trong workspace hiện tại, lệnh này sẽ báo stale review** vì `out/poc/review_queue_reviewed.csv` cũ không khớp queue và người dùng chưa thực hiện review thật. Hãy dùng phiên demo mới ở trên để review. Khi nộp, chỉ chép CSV review thực sự hợp lệ vào đúng thư mục output tương ứng; không dùng CSV cũ để chứng minh human review.

Kiểm tra môi trường/test:

```powershell
.\.venv\Scripts\python.exe -m tools.preflight
.\.venv\Scripts\python.exe -m pytest -o addopts='' -p no:cacheprovider -q
```

Đánh giá chính thức khi giảng viên đã cấp GT overlay, sau khi sửa/freeze protocol:

```powershell
.\.venv\Scripts\python.exe -m src.evaluate --track t04 --pred out/t04_demo/baseline/pairs.csv --split eval --run-name baseline --out reports/metrics_baseline.json
.\.venv\Scripts\python.exe -m src.evaluate --track t04 --pred out/t04_demo/poc/pairs.csv --split eval --run-name poc --out reports/metrics_poc.json
.\.venv\Scripts\python.exe -m src.compare reports/metrics_baseline.json reports/metrics_poc.json --out reports/table1_baseline_vs_poc.md
```

Thiếu GT: evaluator báo lỗi rõ và exit 2. Không tạo GT giả, không suy primary metric từ proxy, không sửa tay JSON metric.

## 4. Thứ tự hoàn tất bài

Split cửa sổ thời gian, fit drift dev, HOLD khi fit không nhất quán, baseline có cutoff và regression tests đã được triển khai. Việc còn lại: nhận GT overlay để đánh giá chính thức → review queue của bản release hiện hành → xác minh Git author theo quy định nhóm → tag final đúng mốc. Tag demo hiện tại trỏ commit cũ.
