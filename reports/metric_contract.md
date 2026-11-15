# Metric contract — T04 (chưa nghiệm thu chính thức)

> Định nghĩa metric chuẩn giữ nguyên. Pipeline đã chuyển sang split theo cửa sổ thời gian 20 giây và fit offset/drift chỉ trên các đoạn dev. Chưa có GT overlay nên chưa thể nghiệm thu metric.

| Mục | Khai báo / hiện trạng |
|---|---|
| Track | T04 Sensor Sync Guard |
| Primary | Pair Accuracy@50ms, higher is better |
| Đơn vị | Frame |
| Numerator | Frame có GT partner và sai số prediction so với GT ≤ 50 ms |
| Denominator | Toàn bộ frame có GT partner trên eval, giống nhau cho baseline/PoC |
| Matching | frame_id; HOLD trên frame có GT partner tính là sai |
| Candidate pool | 3570 frame input camera.csv |
| Seed / budget | 42 / 20 frame |
| Tham số hiện có | dropout_tol_ms=30; max_imu_gap_ms=30; max_offset_ms=250; xcorr_step_ms=10; go_threshold=0.90; tol_ms=50 |
| GO/HOLD hiện tại | Proxy từ residual và paired coverage trên các block gắn dev; chưa phải primary metric |
| Split yêu cầu | Sáu đoạn 20 giây; nhãn dev/eval lấy từ segments.csv |
| Split code hiện tại | Cửa sổ 20 giây neo theo thời gian IMU; fit dev trim biên ±margin; frame ở vùng biên được gắn boundary và không dùng làm proxy dev |
| Secondary | Median residual với GT; Bad-pair recall (dropout held); % frames HOLD (valid frames) |
| Slices cần báo | segment + drift; dropout/continuous với GT được cấp |
| Độ bất định | Chỉ tính CI sau khi có numerator/denominator đáng tin cậy |
| Freeze khai báo trong bản trước | 2026-11-14 16:20 +07:00, Vũ Văn Đăng; chưa xác minh được thứ tự trước đánh giá |

## Kiểm tra ngày 2026-11-14

- Kiểm thử mới xác nhận split theo thời gian thay cho array split theo số hàng; dev fit dùng margin để tránh mẫu sát biên eval. Test regression kiểm tra mapping và fit drift.
- Không tune threshold trong lượt kiểm tra này. Demo không đọc GT. Bản release hiện có offsets dev −40/−80/+20 ms, fit tuyến tính residual tối đa 46,67 ms; vượt ngưỡng dropout_tol_ms=30 nên fit status `inconsistent_dev_drift` và quyết định HOLD. Đây là guard bảo thủ, không phải kết luận accuracy.
- Phiên demo cũ có 20 quyết định approve; ID/score/reason khớp queue và audit ghi 20 `review:approve`. Bằng chứng công bố đã ẩn danh reviewer. Review kiểm tra timestamp/IMU metadata, không xác minh GT hoặc sự kiện bằng ảnh/video; release mới chưa review.
- CSV/audit cũ trong `out/poc/` không còn thuộc queue hiện tại; ID không khớp queue mới.
- JSON metric cũ có mẫu số baseline/PoC khác nhau, config/extra rỗng; chưa tái lập được vì thiếu GT. `src.compare` không xác thực metric.
- Có fit drift tuyến tính trên các cửa sổ dev; các ca offset không nhất quán buộc HOLD. Baseline áp dụng cutoff hữu hạn dùng cùng cấu hình tolerance. Chưa kết luận PoC tốt hơn baseline do thiếu GT.

Xem [submission_check.md](submission_check.md) và [t04_run_guide.md](t04_run_guide.md).
