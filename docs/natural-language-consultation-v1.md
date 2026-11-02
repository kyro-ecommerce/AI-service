# Hiểu yêu cầu tự nhiên — phiên bản một lượt hỏi

Ngày cập nhật: 01/11/2026. Phạm vi AI-service, không thay API/BE/FE/DB.

## Bổ sung: gợi ý trước, hỏi thêm sau

Thiếu ngân sách/hãng/mục đích không phải một lỗi ngữ pháp và không mặc nhiên
chặn gợi ý. Nếu các điều kiện đã nêu được hiểu rõ, dùng đúng các điều kiện đó
để chọn SKU. Chưa có ngân sách thì không tự thêm trần giá; có thể trả lựa chọn
ban đầu rồi hỏi ngân sách để thu hẹp. Với giá “tầm”, nói rõ chưa dùng nó làm
giới hạn bắt buộc. Sở thích được ghi nhận chưa đồng nghĩa ranker đã tối ưu nó.

Ba câu regression thực tế:

- “Có con laptop dưới 20 củ RAM ít nhất 16 GB không shop?”: giá <20 triệu,
  RAM >=16 GB; không chặn vì từ đệm.
- “Cho mình con laptop dưới 20 triệu RAM 16GB nhé”: giá <20 triệu, RAM =16 GB.
- “Cho mình con laptop 16GB RAM nhé”: RAM =16 GB, không thêm ngân sách;
  gợi ý rồi hỏi mức chi tối đa.

Trước khi gọi LLM, lớp hiểu thử tái sử dụng các điều kiện parser đã đọc và các
đoạn alias có bằng chứng nguyên văn (`20 củ`, `16GB RAM`). Cùng validator kiểm
tra nghĩa, phủ định và toàn bộ nội dung còn lại, chỉ bỏ từ đệm được cho phép.
Không sửa câu rồi mất liên kết về câu gốc. Nếu đã đủ rõ thì không gọi LLM chỉ
để xác nhận lại điều kiện. Test cho các câu này phải pass khi provider không
khả dụng, không chỉ khi mock trả đúng JSON mong muốn.

Phân biệt rõ: **chưa nêu** điều kiện tùy chọn khác với **đã nêu nhưng chưa hiểu**
hoặc **chưa có bằng chứng**. “Laptop bảo hành 3 năm” không được biến thành
“laptop bất kỳ” và giới thiệu như đã đáp ứng. Điều kiện mâu thuẫn/thiếu đơn vị
vẫn cần làm rõ; không có SKU đáp ứng thì nói không có, không tự nới điều kiện.
Chưa triển khai phương án gần nhất/không đạt một số điều kiện thành thẻ riêng.

Fallback nhận diện `16GB`/`1TB` có đơn vị dù dính số, không hỏi nhầm ngân sách.
Chat thường/SSE dùng chung lựa chọn và câu hỏi thu hẹp được thêm bằng code,
không phụ thuộc LLM có nhớ hỏi thêm hay không. Chỉ thêm câu hỏi khi thực sự có
sản phẩm trả về, không nói đang gợi ý khi danh sách rỗng.

## Mục tiêu và luồng

Người dùng không phải học cú pháp. Parser cùng bước kiểm tra từ đệm/alias xử lý câu đã hiểu đủ;
nếu còn phần chưa hiểu, LLM đề xuất điều kiện có cấu trúc. Core vẫn kiểm tra
điều kiện và chọn đúng SKU từ snapshot. LLM diễn giải kết quả sau khi chọn.

`câu nhập → parser + bằng chứng/từ đệm → [LLM khi cần] → kiểm tra → lọc SKU → trả lời + hỏi thu hẹp nếu hữu ích`

Lớp hiểu và lớp viết câu trả lời khác nhiệm vụ. Không dùng câu trả lời tự do
của LLM làm yêu cầu, thông số hay bằng chứng sản phẩm. Không khôi phục agent
SSE cũ; chat thường và SSE tiếp tục chung một quyết định.

## Hợp đồng và kiểm tra

- LLM chỉ đề xuất `constraints`, `preferences`, `unresolved_spans`.
- Các điều kiện gồm loại, thương hiệu, giá VND, RAM/storage GB; không thêm SKU.
- Mỗi điều kiện/sở thích phải gắn đoạn nguyên văn của câu nhập. Không nhận
  nguồn `history`/`confirmed_context` vì chưa triển khai bộ nhớ nhiều lượt.
- Các điều kiện rõ đã được parser nhận không được bỏ, đảo hay nới lỏng.
- Kiểm tra nghĩa của đoạn bằng parser và một số alias rõ: `20 củ` = 20 triệu;
  `16GB RAM` = `RAM 16GB`. Các alias này cũng được nhận trước LLM. RAM 16GB
  vẫn là đúng 16GB, không tự đổi thành tối thiểu.
- Phần ngoài bằng chứng chỉ được là từ đệm giới hạn; không được bỏ mệnh đề khác.
- Không hạ điều kiện cứng xuống sở thích. Không suy RAM/CPU từ học lập trình.
- Kiểm tra mâu thuẫn số và tập thương hiệu/loại được dùng chung với parser.
- CPU/GPU, pin, màu, màn hình, chính sách, phương án `hoặc`, đổi yêu cầu nhiều lượt
  vẫn cần làm rõ; không coi chúng đã được hỗ trợ chỉ vì LLM viết JSON đúng.
- Khi hỏi lại, giữ các phần đã nhận và không hiển thị lỗi kỹ thuật “ngoài ngữ pháp”.

Đây không phải bộ hiểu ngôn ngữ tổng quát. Có đoạn trích khớp không chứng minh
nghĩa đã đúng. Guardrails có thể từ chối câu hợp lý; cần đo tỉ lệ hiểu sai VÀ
tỉ lệ hỏi lại trên câu người dùng độc lập, không chỉ test các ví dụ phát triển.
Các alias mới vẫn cần kiểm tra, không dùng LLM tự diễn giải lại thành câu truy vấn
rồi mặc nhiên tin mọi điều kiện trong câu mới.

## Chi phí, dữ liệu và lỗi

- Dùng provider/model đã cấu hình: Gemini nếu có key, nếu không thì OpenRouter.
  Mỗi lượt hiểu chỉ một attempt, không cascade sang model khác.
- Không gửi catalog/lịch sử; đầu vào được che PII trước khi gửi provider.
  Che PII là biện pháp heuristic, không cam kết loại bỏ mọi dữ liệu nhạy cảm.
- Không log nội dung câu, response body, credential hoặc exception thô từ lớp mới.
- Timeout hiểu tối đa `min(6 giây, LLM_REQUEST_TIMEOUT_SECONDS, thời gian còn lại)`.
  Hiểu và viết chia sẻ `LLM_TOTAL_BUDGET_SECONDS`, không reset sau bước hiểu.
  Đây là budget LLM, không bảo đảm toàn request/DB/SSE dưới một ngưỡng latency.
- Không có key, JSON lỗi, output không hợp lệ, timeout hoặc hết budget: giữ
  yêu cầu đã biết và hỏi lại; không lựa sản phẩm bằng điều kiện bị bỏ sót.
- Không thêm embedding, framework, DB bảng mới, crawler hoặc migration.

## Giới hạn không được che giấu

Chưa nhớ câu trả lời của lượt trước; `session_id` chưa biến thành bộ nhớ hội thoại.
Ghi nhận mục đích/sở thích không đồng nghĩa đã xếp hạng theo mọi mục đích đó.
Keyword ranker và dữ liệu SKU giữ nguyên; không chứng minh hiệu năng thực tế,
chất lượng dài hạn, bảo hành, tồn kho hay khả năng checkout.

## Kiểm chứng và hoàn tác

`tests/test_requirement_interpreter.py` dùng output giả lập/HTTP MockTransport;
`tests/test_chat_consultation_flow.py` đi qua validator và bộ lọc SKU thật với
catalog fixture. Kiểm tra phủ định, thiếu/đổi điều kiện, điều kiện tự bịa, JSON
lỗi, che PII, timeout, deadline chung và chat/SSE. Đây là test phát triển, không
phải kết quả benchmark LLM thật hoặc dữ liệu kiểm thử độc lập.

Chạy test với credential provider bị vô hiệu hóa trong process test; không gọi
model thật nếu chưa được duyệt. Bước sau cần bộ câu/nhãn do thành viên khác lập,
đo hiểu đúng điều kiện, mất/thêm điều kiện, hỏi lại, vi phạm khi chọn SKU, chi phí
và latency. Không suy chất lượng từ số lượng test pass.

Hoàn tác chỉ các thay đổi của bước này: tháo adapter khỏi chat, phục hồi parser
và deadline trước bước này, gỡ helper/test/doc mới. Không reset cả file về HEAD
vì có các thay đổi từ bước trước. Không có dữ liệu DB cần rollback.
