# Nghiệm thu similar theo SKU — 11/11/2026

## Phạm vi đã chốt

Similar ưu tiên **cấu hình gần SKU đang xem**, không phải máy mạnh nhất.
Chatbot sẽ xử lý **cấu hình phù hợp trong ngân sách** ở bước riêng. Không lấy
policy similar áp cho mọi câu tư vấn. Chưa so hiệu năng CPU/GPU hoặc tương thích OS.

## Contract local, không đổi field BE

`GET /api/v1/ai/recommendations/similar/{product_id}` nhận thêm:

- `target_variant_id`: ID biến thể thuộc đúng sản phẩm, active, stock > 0,
  sale > 0. SKU sai/hết khả dụng/snapshot không chứa SKU trả 422, không tự đổi SKU.
- `ranking_policy`: `hybrid` (mặc định, baseline) hoặc `capacity_first` (thử nghiệm).
- `limit`: giữ contract 1..20.

Không truyền target_variant_id vẫn dùng SKU đại diện rẻ nhất đủ điều kiện để
tương thích caller cũ. Response khai báo `target_selection_policy` và `target_variant`;
không được nói đại diện là lựa chọn người dùng. Cache tách catalog fingerprint,
target variant và policy. Unknown không được suy ra từ cấu hình sản phẩm cha.

FE đọc `?variant=9` từ URL, kiểm tra variant thuộc product hiện tại trước khi
hiển thị lựa chọn. ProductInfo và RelatedProducts dùng chung lựa chọn này.
Chưa chọn cấu hình thì yêu cầu chọn, không ngầm so SKU khác. Thẻ similar mở URL
có variant được gợi ý; checkout vẫn do BE xác nhận, không phải bảo đảm giữ giá/stock.

## Hai policy và giới hạn

Baseline giữ công thức hybrid cũ để đối chiếu, không tuyên bố tối ưu.
Experimental dùng trung bình min/max của RAM và storage trên những chiều target
đã biết; candidate thiếu chiều đó nhận 0. Không biết cả hai chiều target thì
trả rỗng với `unknown_target_capacities`, không tự gọi hybrid là config-first.

Thứ tự experimental: độ gần cấu hình giảm dần → sale cùng SKU tăng dần →
hybrid giảm dần → ID ổn định. Mỗi sản phẩm chỉ có một SKU trong kết quả.
Đây là policy dễ kiểm tra, không phải trọng số đã học hay xác suất phù hợp.
Một lợi thế cấu hình nhỏ có thể thắng chênh lệch giá lớn. Không tự đặt budget
cho similar. Nếu cần giá gần target hoặc giới hạn ngân sách, phải chốt yêu cầu
riêng; không âm thầm thêm threshold rồi tune để Acer/HP thắng.

Mặc định chưa promote experimental. Bộ chọn hai policy chỉ hiện khi URL có
`recommendation_demo=1`, ví dụ `/product/4?variant=9&recommendation_demo=1`.
Không có flag demo thì `similar_policy` trong URL không bật thử nghiệm; dùng hybrid.
Đây là chế độ UI kiểm thử, không phải auth/security boundary hoặc A/B test có
randomization hay causal proof. Thẻ mặc định chỉ hiện cấu hình/lý do ngắn và dấu
chưa xác minh; SKU, nguồn, cảnh báo snapshot và reason đầy đủ ở “Chi tiết gợi ý”.
Mở disclosure không điều hướng sang sản phẩm; bấm thẻ vẫn giữ variant trong URL.

## Review bằng người thật, không tạo nhãn random

Chạy offline, không HTTP/DB/training/provider:

```powershell
python -m scripts.review_similar --catalog artifacts/catalog-snapshot.json --target-product 4 --target-variant 9
python -m scripts.review_similar --catalog artifacts/catalog-snapshot.json --review artifacts/human-review.json --k 4
```

Catalog là array Product snapshot hoặc object `products`; giữ nguồn/technical
evidence trong snapshot. CLI in JSON ra stdout, không tự sửa DB hoặc ghi đè file.
`prepare_review` tạo phiếu trống có fingerprint catalog, target cụ thể và tất cả
SKU candidate đủ điều kiện. Không đưa score/thứ hạng model vào phiếu chuẩn bị.

Người chấm giữ nguyên dữ kiện, điền relevance 0..3 hoặc null (unknown), reason,
reviewer, reviewed_at có timezone, origin=human_review và split development/holdout.
Nhãn: 3 gần/hữu ích; 2 có ích nhưng có đánh đổi; 1 liên quan yếu; 0 không phù hợp
theo dữ kiện. Null là thiếu cơ sở, không phải 0. Thống nhất rubric trước khi chấm;
đánh giá cả giá đáng ngờ và nguồn cấu hình, không dùng công thức model tạo nhãn.
Nên hai người chấm độc lập, khai báo đã xem model chưa. Cùng product/các SKU
không chia sang cả dev và holdout; không tune trên holdout.

Evaluator so hybrid, capacity_first và category-popularity (cùng universe có SKU
khả dụng; baseline popularity chọn SKU rẻ nhất). Metrics phụ thuộc chính SKU được
chọn: graded NDCG@k, precision relevance>=2@k, với IDCG không lặp sản phẩm.
Chưa đủ nhãn cho mọi SKU thì metrics=null, báo coverage; không bỏ unknown để làm
đẹp điểm. Reject fingerprint sai, sửa dữ kiện, duplicate/foreign SKU và nhãn sai.
Chỉ metadata khai human không chứng minh người chấm độc lập; report ghi rõ điều này.
Một phiếu không đủ quyết định promote (`promotion_allowed=false` luôn ở công cụ pilot).

## Điều kiện đóng phase

1. Browser: chọn 8GB/16GB đổi đúng target và card; link thẻ giữ SKU; SKU sai không
   tự chuyển cấu hình. Dịch vụ lỗi rồi phục hồi có card trở lại không reload.
2. Sourced pilot có nhãn người thật, báo dev/holdout, coverage, bất đồng reviewer
   và so ba baseline trên cùng snapshot/target. Không coi số unit test là chất lượng.
3. Xác minh dữ liệu giá đáng ngờ, không tự sửa seed cho thứ hạng đẹp. Dataset nguồn
   07/11 chỉ 4 listing dùng ranking/1 nhà bán, không đại diện thị trường hiện tại.
4. Sau khi nhóm review chốt policy phục vụ, mới chuyển trọng tâm hội thoại nhiều
   lượt. Không gọi phase hoàn tất khi chỉ mới thêm endpoint hoặc đổi sort.
