# Phiếu pilot similar — cấu hình và giá

Ngày chuẩn bị: 05/11/2026. Đây là phiếu CHƯA CHẤM, không phải kết quả chất lượng.

Người chấm: __________

Ngày chấm: __________

Phiếu của người chấm số: __________

## Dữ liệu và phạm vi

- Nguồn: snapshot DB local, truy vấn read-only; 22 sản phẩm active, 7 laptop.
- Fingerprint toàn bộ snapshot: `9d60aa3c6ae7f1a343475bc3418af0a3d04d355513dc23b226e03090492a50c8`.
- 42 cặp có hướng: mỗi laptop gốc đối chiếu 6 laptop còn lại.
- Thứ tự target/ứng viên được xáo cố định (seed 20261105), không phải thứ hạng model.
- Không hiển thị điểm ranker hoặc SKU do ranker chọn. Các ô điểm đều để trống.
- Mục tiêu chỉ là gần RAM/storage và giá snapshot trong cùng nhóm laptop. Không chấm tính tương đương gaming, GPU/CPU, hệ điều hành hay ứng dụng.
- RAM/storage dưới đây đều lấy từ tên SKU bằng parser hiện tại, `verified=False`; chưa có cấu hình xác minh độc lập. Đây là đánh giá có điều kiện theo catalog, không xác nhận phần cứng thực tế.
- Giá/tồn kho là snapshot, không phải cam kết checkout. Các SKU cùng sản phẩm đang có giá giống nhau dù cấu hình khác; bộ pilot không kiểm chứng đầy đủ tradeoff tăng cấu hình/tăng giá.
- Giá HP Spectre khoảng 3,5 triệu cần xác minh. Không tự sửa giá khi chấm; dùng unknown nếu không đủ cơ sở và ghi lý do.
- SKU 11 của ASUS active nhưng stock=0, không nằm trong danh sách khả dụng. Không chọn SKU này.
- Ai đã xem kết quả model trước đó cần khai báo: __________. Nên nhờ thành viên khác chưa xem kết quả chấm trước, rồi mới đối chiếu.
- Phiếu này do trợ lý chuẩn bị từ catalog, không có nhãn do người đánh giá độc lập cung cấp. Nhãn pilot dùng để phát triển không được tuyên bố là holdout cuối cùng.

## Cách chấm vòng A — chưa xem kết quả model

1. Đọc SKU gốc cố định ở đầu mỗi mục. Đây là SKU đại diện theo chính sách, KHÔNG phải SKU người dùng đã chọn trên FE.
2. Với mỗi ứng viên, tự chọn một SKU khả dụng trong bảng catalog làm căn cứ. Không ghép giá/cấu hình của nhiều SKU.
3. Chấm mức liên quan của SẢN PHẨM ứng viên so với SKU gốc, ghi SKU làm căn cứ và lý do. Không tính điểm theo công thức model hoặc theo tên thương hiệu.
4. Nếu thiếu dữ liệu để phán đoán, ghi unknown; không đổi unknown thành 0 và không tự suy thông số.
5. Không xem score/thứ hạng model cho đến khi hoàn tất, lưu riêng phiếu và các bất đồng giữa người chấm.

| Nhãn | Ý nghĩa |
|---|---|
| 3 | Gần trên các chiều đã nêu, là lựa chọn thay thế hợp lý trong phạm vi catalog |
| 2 | Có ích nhưng có đánh đổi đáng kể về cấu hình hoặc giá; nêu rõ đánh đổi |
| 1 | Liên quan yếu, không nên ưu tiên |
| 0 | Không phù hợp theo dữ kiện đã biết, không phải chỉ vì thiếu dữ liệu |
| unknown | Chưa đủ cơ sở chấm; ghi phần còn thiếu hoặc không đáng tin |

Không có ngưỡng phần trăm/điểm tự động ở đây. Nếu hai người hiểu khác “đánh đổi đáng kể”, ghi nhận khác biệt và thống nhất rubric trước khi tính metrics; không âm thầm sửa nhãn cho khớp model.

## Catalog SKU khả dụng

Tên SKU được giữ làm nguồn; CPU/GPU trong tên không được coi là thông số xác minh và không dùng để đổi mục tiêu chấm vòng này.

| Product ID / sản phẩm | Variant ID / SKU | Tên SKU nguồn | RAM GB | Storage GB | Giá gốc VND | Giá sale VND | Stock snapshot |
|---|---|---|---:|---:|---:|---:|---:|
| 4 — Laptop Acer Nitro 5 Eagle | 8 / KYRO-P4-V8 | i5/8GB/512GB SSD/RTX 3050 | 8 | 512 | 21.990.000 | 18.911.400 | 14 |
| 4 — Laptop Acer Nitro 5 Eagle | 9 / KYRO-P4-V9 | i7/16GB/1TB SSD/RTX 3050Ti | 16 | 1000 | 21.990.000 | 18.911.400 | 2 |
| 5 — Laptop ASUS ROG Strix SCAR 17 | 10 / KYRO-P5-V10 | Ryzen 9/32GB/2TB SSD/RTX 4090 | 32 | 2000 | 69.990.000 | 60.191.400 | 7 |
| 6 — Laptop Dell XPS 13 | 12 / KYRO-P6-V12 | i5/8GB/512GB SSD | 8 | 512 | 32.990.000 | 30.020.900 | 20 |
| 6 — Laptop Dell XPS 13 | 13 / KYRO-P6-V13 | i7/16GB/1TB SSD | 16 | 1000 | 32.990.000 | 30.020.900 | 9 |
| 7 — Laptop HP Spectre x360 14 | 14 / KYRO-P7-V14 | i7/16GB/1TB SSD | 16 | 1000 | 3.799.000 | 3.495.080 | 9 |
| 7 — Laptop HP Spectre x360 14 | 15 / KYRO-P7-V15 | i5/8GB/512GB SSD | 8 | 512 | 3.799.000 | 3.495.080 | 8 |
| 14 — MacBook Air M2 | 27 / KYRO-P14-V27 | 8GB/256GB | 8 | 256 | 30.990.000 | 29.130.600 | 20 |
| 14 — MacBook Air M2 | 28 / KYRO-P14-V28 | 16GB/512GB | 16 | 512 | 30.990.000 | 29.130.600 | 15 |
| 14 — MacBook Air M2 | 29 / KYRO-P14-V29 | 16GB/1TB | 16 | 1000 | 30.990.000 | 29.130.600 | 8 |
| 16 — Surface Laptop Studio 2 | 32 / KYRO-P16-V32 | i7/16GB/512GB/RTX 4050 | 16 | 512 | 52.990.000 | 49.810.600 | 12 |
| 16 — Surface Laptop Studio 2 | 33 / KYRO-P16-V33 | i7/32GB/1TB/RTX 4060 | 32 | 1000 | 52.990.000 | 49.810.600 | 8 |
| 20 — ROG Zephyrus G14 | 39 / KYRO-P20-V39 | Ryzen 9/16GB/1TB/RTX 4060 | 16 | 1000 | 38.990.000 | 35.091.000 | 10 |
| 20 — ROG Zephyrus G14 | 40 / KYRO-P20-V40 | Ryzen 9/32GB/1TB/RTX 4070 | 32 | 1000 | 38.990.000 | 35.091.000 | 8 |

## Phiếu chấm 42 cặp

### Mục 1 — MacBook Air M2 (P14)

SKU gốc cố định: **KYRO-P14-V27**, variant 27; RAM 8 GB / storage 256 GB; sale 29.130.600 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P14→P16 | Surface Laptop Studio 2 |  |  |  |
| P14→P7 | Laptop HP Spectre x360 14 |  |  |  |
| P14→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |
| P14→P20 | ROG Zephyrus G14 |  |  |  |
| P14→P6 | Laptop Dell XPS 13 |  |  |  |
| P14→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |

### Mục 2 — Laptop HP Spectre x360 14 (P7)

SKU gốc cố định: **KYRO-P7-V14**, variant 14; RAM 16 GB / storage 1000 GB; sale 3.495.080 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P7→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |
| P7→P6 | Laptop Dell XPS 13 |  |  |  |
| P7→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |
| P7→P20 | ROG Zephyrus G14 |  |  |  |
| P7→P16 | Surface Laptop Studio 2 |  |  |  |
| P7→P14 | MacBook Air M2 |  |  |  |

### Mục 3 — Laptop Dell XPS 13 (P6)

SKU gốc cố định: **KYRO-P6-V12**, variant 12; RAM 8 GB / storage 512 GB; sale 30.020.900 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P6→P14 | MacBook Air M2 |  |  |  |
| P6→P20 | ROG Zephyrus G14 |  |  |  |
| P6→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |
| P6→P7 | Laptop HP Spectre x360 14 |  |  |  |
| P6→P16 | Surface Laptop Studio 2 |  |  |  |
| P6→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |

### Mục 4 — ROG Zephyrus G14 (P20)

SKU gốc cố định: **KYRO-P20-V39**, variant 39; RAM 16 GB / storage 1000 GB; sale 35.091.000 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P20→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |
| P20→P16 | Surface Laptop Studio 2 |  |  |  |
| P20→P6 | Laptop Dell XPS 13 |  |  |  |
| P20→P14 | MacBook Air M2 |  |  |  |
| P20→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |
| P20→P7 | Laptop HP Spectre x360 14 |  |  |  |

### Mục 5 — Laptop Acer Nitro 5 Eagle (P4)

SKU gốc cố định: **KYRO-P4-V8**, variant 8; RAM 8 GB / storage 512 GB; sale 18.911.400 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P4→P6 | Laptop Dell XPS 13 |  |  |  |
| P4→P16 | Surface Laptop Studio 2 |  |  |  |
| P4→P14 | MacBook Air M2 |  |  |  |
| P4→P20 | ROG Zephyrus G14 |  |  |  |
| P4→P7 | Laptop HP Spectre x360 14 |  |  |  |
| P4→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |

### Mục 6 — Laptop ASUS ROG Strix SCAR 17 (P5)

SKU gốc cố định: **KYRO-P5-V10**, variant 10; RAM 32 GB / storage 2000 GB; sale 60.191.400 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P5→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |
| P5→P20 | ROG Zephyrus G14 |  |  |  |
| P5→P16 | Surface Laptop Studio 2 |  |  |  |
| P5→P14 | MacBook Air M2 |  |  |  |
| P5→P7 | Laptop HP Spectre x360 14 |  |  |  |
| P5→P6 | Laptop Dell XPS 13 |  |  |  |

### Mục 7 — Surface Laptop Studio 2 (P16)

SKU gốc cố định: **KYRO-P16-V32**, variant 32; RAM 16 GB / storage 512 GB; sale 49.810.600 VND. Nguồn RAM/storage: tên SKU, chưa xác minh.

| Mã cặp | Ứng viên | Variant ứng viên tự chọn | Nhãn 0–3 / unknown | Lý do / dữ liệu thiếu |
|---|---|---|---|---|
| P16→P20 | ROG Zephyrus G14 |  |  |  |
| P16→P4 | Laptop Acer Nitro 5 Eagle |  |  |  |
| P16→P7 | Laptop HP Spectre x360 14 |  |  |  |
| P16→P6 | Laptop Dell XPS 13 |  |  |  |
| P16→P5 | Laptop ASUS ROG Strix SCAR 17 |  |  |  |
| P16→P14 | MacBook Air M2 |  |  |  |

## Vòng B — chỉ mở sau khi khóa nhãn vòng A

Người tổng hợp mới chạy hai phiên bản trên cùng fingerprint và đối chiếu:

- Thứ hạng sản phẩm: dùng nhãn vòng A; không giả định bản cũ đã chọn SKU nào.
- Lựa chọn SKU của bản mới: có nằm trong các lựa chọn người chấm chấp nhận không? Nếu khác, ghi lý do; một sản phẩm liên quan không tự động làm mọi SKU của nó đúng.
- Bằng chứng trả về: giá gốc/sale/discount/RAM/storage có thuộc đúng SKU không, nguồn có được nêu trung thực không?
- Bản cũ thiếu bằng chứng SKU phải được ghi là thiếu; không bổ sung ngược thông tin rồi coi đó là output cũ.
- Báo riêng số unknown, số đã chấm, số trả rỗng và vi phạm giá/cấu hình. Không bỏ unknown âm thầm để làm đẹp metrics.
- Chưa có đủ nhãn thì chưa tính nDCG@5 hay tuyên bố bản mới tốt hơn.

| Nội dung sau khi đối chiếu | Kết quả / ví dụ / mã cặp |
|---|---|
| SKU bản mới chấp nhận được |  |
| SKU bản mới không chấp nhận được |  |
| Sai cặp giá/cấu hình/discount |  |
| Thiếu bằng chứng nguồn |  |
| Bất đồng giữa người chấm |  |
| Cần bổ sung dữ liệu |  |

Đây là pilot nhỏ trên catalog local, không đo hiệu quả cá nhân hóa hoặc chứng minh nhu cầu sử dụng thực tế. Chưa có điểm chất lượng nào được điền.
