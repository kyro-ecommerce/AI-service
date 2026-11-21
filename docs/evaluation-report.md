# Báo cáo đánh giá AI service (GĐ5)

Ngày đo: 2026-11-21, nhánh `feat/gd5-hardening`, catalog 22 sản phẩm đang bán.
Chạy lại toàn bộ phần offline bằng một lệnh:

```powershell
docker exec ai_service_app python scripts/build_evaluation_report.py --output artifacts/evaluation/latest
```

Các con số mô tả hệ thống trên catalog và dữ liệu hiện có. Chúng không phải
benchmark và không nên so sánh với các bài báo.

## 1. Tóm tắt

| Mảng | Kết quả | Mức tin cậy |
|---|---|---|
| Tìm kiếm (50 câu tiếng Việt có nhãn) | Hybrid RRF recall@5 0,993, nDCG@5 0,960 | Khá. Bộ câu do nhóm viết cho đúng catalog này. |
| Chatbot (20 hội thoại, 38 lượt) | 100% intent, slot, ràng buộc, câu trả lời có căn cứ | Thấp–vừa. Kịch bản viết tay, chạy parser, không LLM. |
| Gợi ý, dữ liệu thật | Chỉ 2 người đủ điều kiện đánh giá | Không đủ để kết luận. |
| Gợi ý, 200 người mô phỏng | profile_affinity_v1 nDCG@5 0,461, popularity 0,234 | Thấp. Người dùng mô phỏng thích theo loại, nên mô hình theo sở thích được lợi sẵn. |
| Online (30 ngày) | 24 danh sách, 27 lượt hiển thị, 2 click | Chưa có ý nghĩa thống kê. |
| Tải (1 tiến trình uvicorn) | ~55 req/s: p95 21 ms (gợi ý), 24 ms (search), 0% lỗi. Trần ~130 req/s. | Đo trên máy dev, chỉ mang tính tham khảo. |

## 2. Tìm kiếm

| Chế độ | recall@5 | nDCG@5 | MRR |
|---|---|---|---|
| keyword | 0,950 | 0,939 | 0,960 |
| vector (multilingual-e5-small) | 0,967 | 0,926 | 0,918 |
| **hybrid RRF** (đang dùng) | **0,993** | **0,960** | 0,957 |

Keyword yếu ở câu tiếng Anh (recall 0,74). Vector yếu ở câu nêu nhu cầu như
"máy cho sinh viên" (recall 0,83). Hybrid bù được cả hai nên được giữ làm
mặc định.

## 3. Chatbot

Chạy `evaluate_chat.py` ở chế độ parser, không gọi LLM. Bộ hội thoại đã được
cập nhật theo các quy tắc nhóm B:
- "tầm X" là mức giá mong muốn;
- nhắc tên hãng là ưu tiên;
- "rẻ hơn" là so với mẫu được nhắc.

Mọi chỉ số đều đạt 1,0. Latency p95 là 111 ms, chưa tính thời gian LLM.
Ở chế độ `--live` (có LLM), các câu nói sai giá, sai link hoặc nêu thuộc tính
catalog không có đều bị loại trước khi gửi cho khách. Bộ này chỉ chứng minh
các quy tắc chạy đúng như mô tả, chưa nói gì về chất lượng tư vấn với khách
thật.

## 4. Gợi ý sản phẩm

Giao thức: theo từng người, giữ lại lượt tương tác cuối làm đáp án; món đó
phải chưa từng xem; khoảng tin cậy 95% bằng bootstrap theo người.

**Dữ liệu thật**: 66 dòng, chỉ 2 người đủ điều kiện, nên khoảng tin cậy trải
từ 0 đến 0,5. Không thể kết luận mô hình nào tốt hơn.

**200 người mô phỏng** (21 người đủ điều kiện):

| Mô hình | nDCG@5 (KTC 95%) | hit@5 | độ phủ catalog |
|---|---|---|---|
| popularity_baseline | 0,234 (0,085–0,400) | 0,333 | 0,455 |
| interaction_trending_v1 | 0,353 (0,200–0,504) | 0,571 | 0,500 |
| item_knn | 0,411 (0,273–0,552) | 0,667 | 0,909 |
| hybrid_item_knn_popularity | 0,424 (0,263–0,595) | 0,619 | 0,773 |
| profile_affinity_v1 | 0,461 (0,287–0,634) | 0,619 | 0,909 |

Các khoảng tin cậy chồng lên nhau. Thứ tự trên chỉ là xu hướng, chưa đủ chứng
minh mô hình nào hơn.

## 5. Chỉ số online

Đã có đủ đường đo: mỗi danh sách gợi ý có `recommendation_id` riêng, ghi
nhận hiển thị và click, quy đổi về đơn theo click cuối trong 7 ngày. Lượng
dữ liệu thì chưa đủ: 2 click trong 30 ngày.

Cần ít nhất vài trăm lượt hiển thị cho mỗi strategy thì CTR mới đáng đọc.
Với vài nghìn lượt hiển thị có thể bắt đầu A/B giữa `profile_affinity_v1` và
`interaction_trending_v1`.

## 6. Kiểm thử tải (k6)

Script `loadtest/k6/ai_service.js` gồm:
- người dùng duyệt: trending, similar, accessories;
- search ở tốc độ cố định.

Script không ghi dữ liệu hành vi và không gọi LLM. Danh sách gợi ý vẫn được
ghi vào `recommendation_served` với `session_id = k6-*` và cần xoá sau mỗi lần
chạy.

| Kịch bản | Thông lượng | p95 gợi ý | p95 search | Lỗi |
|---|---|---|---|---|
| 30 VU + 10 search/s, gọi thẳng service | 56 req/s | 21 ms | 24 ms | 0% |
| 100 VU + 30 search/s, trước khi tối ưu | 84 req/s | 1 430 ms | 1 460 ms | 0% |
| 100 VU + 30 search/s, sau khi tối ưu | 129 req/s | 587 ms | 676 ms | 0% |
| Qua gateway, 1 IP | – | – | – | 82% bị chặn (429) |

Phát hiện và xử lý:

1. **Fingerprint catalog** (JSON + SHA-256 trên toàn bộ sản phẩm, ~5 ms) bị
   tính lại ở mọi request. Giờ chỉ tính một lần cho mỗi snapshot 30 giây.
   Thông lượng tăng 84 → 129 req/s, p95 giảm 1,4 s → 0,6 s.
2. **Search đầu tiên sau khi khởi động mất 8,7 s** vì phải nạp model
   embedding. Giờ model được nạp nền lúc khởi động; tắt bằng
   `EMBEDDING_WARMUP=false`. Search đầu tiên còn 0,1 s.
3. **Gateway giới hạn 10 req/s cho mỗi IP** (burst 20). Đây là chủ ý, vì người
   dùng thật có IP riêng. Muốn đo sức chịu của service thì gọi thẳng cổng
   8000.

Các điểm còn lại:
- Service chạy **một tiến trình uvicorn với `--reload`** (chế độ dev), nên trần
  khoảng 130 req/s. Khi triển khai thật nên chạy 2–4 worker (mỗi worker
  khoảng 800 MB RAM vì model embedding) và chạy consumer RabbitMQ thành tiến
  trình riêng.
- Mỗi request gợi ý ghi đồng bộ một dòng `recommendation_served` (~4 ms). Nếu
  cần nhanh hơn thì chuyển sang ghi theo lô ở nền.

## 7. Việc cần đội catalog/backend xử lý

| Vấn đề | Ảnh hưởng | Đề xuất |
|---|---|---|
| HP Spectre x360 (id 7) giá 3.495.080 trong khi trung vị laptop là 30.020.900 | Đang bị loại khỏi gợi ý | Sửa giá trong catalog |
| 7 máy chưa có dữ liệu RAM (id 1, 2, 3, 12, 13, 15, 19) | Lọc theo RAM sẽ loại các máy này | Bổ sung thông số |
| `order.delivered` không có `userId` | Không gán được sự kiện giao hàng cho người dùng | Thêm trường vào event |
| Chưa có event huỷ đơn | Đơn đã huỷ vẫn tính là đã mua | Phát `order.cancelled` |

## 8. Giới hạn của báo cáo

- Catalog chỉ có 22 sản phẩm. Mọi chỉ số xếp hạng đều dễ đạt điểm cao.
- Bộ câu search và bộ hội thoại do nhóm viết, có nguy cơ khớp quá sát với cách
  hệ thống được làm.
- Chưa có đánh giá của người thật về chất lượng câu tư vấn từ LLM.
