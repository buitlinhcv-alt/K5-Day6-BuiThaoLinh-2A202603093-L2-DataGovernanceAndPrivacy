# Kiểm tra trước khi nộp — T04 — 2026-11-14

**Kết luận: chưa đạt đầy đủ checklist.** Repo chạy được và đã có trang xem kết quả; còn lỗi protocol và bằng chứng nộp bài. Phạm vi sửa lượt này: `src/`, `tests/`, `reports/`; `out/t04_demo/` chỉ là output do lệnh pipeline sinh theo README.

## Checklist đối chiếu

| Mục | Kết quả | Bằng chứng / việc còn lại |
|---|---|---|
| Chọn một track và đọc hết đề | Đạt | Config là T04; đã đọc T04.md và README_units.md. T02 trong ví dụ không phải track hiện tại. |
| group_id, owner, track | Đạt | 2A202603046; Vu Van Dang; t04; track_config tracks/t04.yaml. |
| Setup + test | Đạt trên máy hiện có, có giới hạn | Python 3.14.0 khác khoảng 3.11–3.13 README khuyến nghị. Preflight 0 FAIL/2 WARN tùy chọn; không cài lại môi trường. |
| PoC ra contract out/poc | Chưa nghiệm thu chính thức | `out/t04_release/poc/pairs.csv` được sinh offline; đây là phiên release chưa review. `out/poc/` cũ có review stale, không dùng làm bằng chứng. |
| Baseline + PoC + giải thích | Có implementation; chưa chứng minh cải thiện | Cùng fixture/seed/offline; baseline có cutoff hữu hạn theo tolerance; không có GT để xác minh accuracy. |
| Review người thật / queue / audit | Đạt cho phiên demo cũ; release chưa review | `out/t04_demo/poc` có 20 approve, 20 ghi chú và audit; danh tính reviewer được ẩn danh trong bằng chứng công bố. Release `out/t04_release/poc` có queue mới và chưa review. Review metadata không thay thế kiểm chứng GT. |
| group_report + contributions | Có; còn danh tính Git | Đã sửa report sai, sửa CSV từ một dòng 9 cột về đúng 8 cột. Git author là Test User, chưa chứng minh mapping thành viên. |
| Tag demo / final đúng mốc | Chưa đủ | demo-180m → f76780c, 16:25:46 +07:00; chưa có final-24h. Không có giờ bắt đầu để xác minh đúng phút 180. |

## Các điểm phải xử lý trước khi chốt protocol

1. Split đã chuyển sang cửa sổ thời gian 20 giây neo theo thời gian IMU; dev fit trim biên. Regression tests kiểm tra phân đoạn, drift và điểm biên.
2. Offset và drift tuyến tính fit trên cửa sổ dev. Fit residual vượt `dropout_tol_ms=30` gắn `inconsistent_dev_drift` và ép decision HOLD.
3. Release hiện dừng an toàn: dev offsets −40/−80/+20 ms có residual tối đa 46,67 ms. Đây không chứng minh accuracy; thiếu GT.
4. Ca biên IMU, tính hữu hạn tham số, gap và tính duy nhất segment có regression tests.
5. Baseline dùng cutoff hữu hạn theo tolerance; không tune trên eval. Thiếu GT nên chưa thể kết luận so sánh accuracy.

## Sai lệch report/bằng chứng đã sửa cách trình bày

- Offset thực chạy −40 ms, không phải +60 ms.
- Hai JSON metric cũ có primary denominator khác nhau (3570 và 3521), config/extra rỗng; thiếu GT để tái lập. `compare` không phải công cụ xác thực metric. Không còn kết luận accuracy=100% hoặc PoC tốt hơn baseline từ các artifact đó.
- License fixture là CC0-1.0, không phải CC-BY-4.0.
- Review 20 approve thuộc phiên `out/t04_demo/poc`; [bằng chứng công bố](review_evidence_anonymized.csv) dùng reviewer ẩn danh. Raw CSV có tên cá nhân được giữ ngoài commit. Review không áp dụng cho queue mới `out/t04_release/poc`. File cũ ở `out/poc/` có ID ngoài queue; không dùng làm bằng chứng.
- Author thật trong Git: Test User <test@example.com>; commit f800cdf có code thực chất. Không sửa lịch sử Git hoặc tự nhận danh tính tác giả.
- final-24h chưa tồn tại; tag demo trỏ f76780c, không phải f800cdf như report cũ.

## Việc đã bổ sung cho người dùng quan sát kết quả

- `python -m src.t04_demo --output out/t04_release --html reports/t04_release.html` chạy baseline/PoC offline với seed 42, sinh HTML/JSON từ input và prediction, không đọc GT hoặc metric cũ.
- HTML có biểu đồ residual, offset theo thời gian block, lọc/tìm frame, metadata queue, nhập tên/quyết định/ghi chú và xuất CSV.
- Giao diện luôn bắt đầu review trống. Pipeline kiểm độ khớp ID/score/reason, audit cả approve/reject; chưa hỗ trợ edit an toàn nên từ chối rõ.
- Release sạch: 3570 frame; baseline ghép 3523; PoC ghép 3518, HOLD 52; offset tham chiếu −33,33 ms; fit `inconsistent_dev_drift`, decision HOLD. Chỉ là diagnostic, không dùng thay đánh giá chính thức.

## Kiểm tra thực thi

- Preflight: 0 FAIL, 2 WARN về thành phần tùy chọn không dùng cho T04.
- Suite trước sửa: 61 passed, 39 skipped (thiếu GT overlay).
- Test T04 và review sau sửa: 20 passed.
- Toàn suite: **70 passed, 39 skipped in 19.27s**, `OPENBLAS_NUM_THREADS=1`, `PYTHONDONTWRITEBYTECODE=1`; 39 test cần GT overlay được skip vì overlay không có.
- Review cũ áp dụng thành công cho phiên demo; release mới chưa review. Review phải khớp đúng queue và output tương ứng.
- Edge headless đã kiểm tra render, queue 20 hàng, lọc HOLD 49 frame, tìm f01368, chặn export khi thiếu reviewer, cấu trúc CSV 20 hàng. Test export dùng dữ liệu ghi rõ SYNTHETIC BROWSER TEST, chặn download; không lưu file review hoặc giả thao tác người dùng. Kết quả: `t04_browser_check.txt`; ảnh: `t04_demo.png`.
- Công cụ CUA/Windows REPL không khởi tạo được (kernel assets); dùng Edge headless cục bộ để kiểm tra HTML. Review mới được người dùng xuất riêng và pipeline áp dụng thành công sau đó.

Hướng dẫn PowerShell đầy đủ: [t04_run_guide.md](t04_run_guide.md). Trang kết quả: [t04_demo.html](t04_demo.html).
