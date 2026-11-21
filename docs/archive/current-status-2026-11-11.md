# Trạng thái và điểm nghiệm thu

Rà soát: **11/11/2026**. Đây là điểm vào để theo dõi công việc; tài liệu thiết kế
cũ giải thích chi tiết, không thay thế trạng thái nghiệm thu ở đây.

## 1. Mục tiêu và phạm vi

Nhu cầu tự nhiên → hiểu điều kiện → lọc đúng SKU → xếp hạng → giải thích có
nguồn → ghi hành vi hợp lệ → đánh giá. Chatbot là giao diện; LLM hỗ trợ hiểu/
diễn đạt, không tự quyết giá/cấu hình. Local trước, deploy sau. Không viết lại
toàn bộ source. Không tự sửa BE/DB, đổi ranking hoặc train trên dữ liệu random.

## 2. Bản source nào?

### Checkpoint nhánh làm việc ngày 11/11

Người dùng yêu cầu lưu code hiện tại lên nhánh riêng rồi tiếp tục tại đó.
Tên nhánh thống nhất ở ba repo AI-Service/Frontend/backend:
`work/recommendation-chatbot-20261111`. Đây là checkpoint WIP để review, không
phải bản nghiệm thu hoặc yêu cầu merge main. Bảng HEAD/ahead bên dưới ghi mốc
trước khi tạo checkpoint; không phải số commit cuối cùng sau khi push.
Main AI gốc có commit JSON lớn được giữ nguyên, không đưa vào nhánh này.
`.env`, artifact/dataset sinh thử và local-runtime không được đưa vào commit.

Rà soát sau recovery xác định còn cần thu gọn/nghiệm thu: hook chung đang bắt
cả recommendation công khai chờ AuthContext; timeout recommendation chưa chặn
được chờ refresh-token bên interceptor; refresh sau thành công khi quay lại tab
là phần mở rộng ngoài sửa outage tối thiểu. Retry từng component có thể tăng
request đồng thời. BE Compose thêm internal-token bắt buộc; caller PURCHASE cũ
ở FE vẫn gọi generic record bị gateway chặn, không được coi là tracking hoàn tất.
Không âm thầm sửa thêm các điểm này trong bước xuất bản checkpoint.

Trước khi chuyển trọng tâm chatbot: chốt SKU target đúng lựa chọn FE, định nghĩa/
đánh giá policy ưu tiên cấu hình, đánh giá bằng catalog có nguồn và người thật,
nghiệm thu đăng nhập/đổi sản phẩm/phục hồi trên browser. Các endpoint public trả
200 không chứng minh chất lượng thứ hạng hoặc khả năng mua hàng ở checkout.

| Vị trí local | Vai trò / trạng thái kiểm tra |
|---|---|
| `D:\Kyro\publish-worktrees\ai-service-20261102` | Source AI refactor đang phát triển; branch `refactor/ai-consultation-cleanup-20261102`, HEAD `a8b721c`, hơn upstream local 4 commit; chưa fetch/push trong lượt rà soát |
| `D:\Kyro\ai-service` | Main cũ, HEAD `00d0745`, hơn upstream local 1 commit dữ liệu; giữ nguyên, không reset hoặc push toàn bộ |
| `D:\Kyro\Frontend` | FE main có thay đổi chưa commit, gồm thẻ recommendation, giá/rating và VIEW tracking |
| `D:\Kyro\kyro_backend` | Phase 1B đã được duyệt: sửa riêng gateway filter/test, route config và env gateway trong Compose; không đổi DTO/schema sản phẩm hoặc service nghiệp vụ |
| `D:\Kyro\local-runtime` | Cấu hình tích hợp riêng máy, không nằm trong repo AI; giữ `.env` gốc, DB/broker hiện có |

Ngày 07/11 đã xác nhận container AI mount worktree refactor vào `/app`, mount
`.env` gốc read-only, chạy Uvicorn không seed/migration. Liveness, readiness,
trending/similar qua gateway đã trả 200. Đầu lượt audit 11/11 Docker chưa khả dụng;
sau khi người dùng bật Docker, Phase 1B đã xác nhận lại đúng mount, AI readiness
200, product/trending qua gateway 200 và gateway healthy. Đây là kiểm tra từng
endpoint, không phải chứng nhận toàn bộ luồng đăng nhập/VIEW/browser.
Không tự bật/restart Docker khi audit.

### Chạy lại tích hợp local khi Docker đã được người dùng bật

Lệnh sau recreate riêng AI bằng image có sẵn, không build/pull/seed/migration.
Chỉ chạy khi đang thực hiện bước tích hợp đã được duyệt, không phải để audit:

```powershell
docker compose --env-file D:/Kyro/kyro_backend/.env -p ai-service -f D:/Kyro/ai-service/docker-compose.yml -f D:/Kyro/local-runtime/ai-refactor.override.yml up -d --no-deps --no-build --pull never web
```

FE dùng **http://localhost:5173/**, không dùng origin `127.0.0.1:5173`: ở lần
kiểm tra 07/11 gateway cho localhost, từ chối 127.0.0.1 bằng 403. Vite có thể bind
127.0.0.1 nhưng địa chỉ trên thanh URL vẫn cần localhost. Không đổi CORS BE chỉ
để lách vấn đề này. File override phụ thuộc đường dẫn máy; clone mới không tự có.

## 3. Đã làm và còn thiếu

| Mảng | Đã có trong source/local | Chưa nghiệm thu |
|---|---|---|
| Chat | Parser + LLM interpretation có kiểm tra evidence; lọc SKU; JSON/SSE chung quyết định; FE gửi access token/session, có refresh SSE một lần sau 401 | Bộ nhớ nhiều lượt, grounding văn bản LLM; identity đăng nhập/browser còn cần nghiệm thu thực tế |
| Similar | Cùng category, SKU active/stock>0/sale>0, giá/cấu hình cùng SKU, cache gắn fingerprint, FE có evidence | SKU target đang chọn trên FE; chất lượng thứ hạng; độ mới snapshot/checkout |
| Mua kèm | Policy quan hệ category bảo thủ ở AI; FE chỉ fallback trending khi mua kèm trả rỗng hợp lệ, đổi tiêu đề thành “Bạn có thể quan tâm” và ghi rõ gợi ý chung | Lỗi/sai dạng không fallback; chưa kiểm tra live/browser hoặc xác minh tương thích thiết bị |
| Trending/personalized | Có thuật toán/fallback; gateway đã có access-token bridge và kiểm tra personalized đúng tài khoản | Chưa nghiệm thu phiên đăng nhập thật; thiếu dữ liệu hành vi/model hợp lệ, không gọi trending là cá nhân hóa |
| FE giá/rating | Sửa field giá ba mục trang chủ; shared card không tự gán 5 sao; similar có SKU/reason | Toàn bộ FE chưa được audit; SSR/mapping test không chứng minh giao diện thật |
| Dữ liệu | Snapshot SKU và nguồn name proposal được giữ, không bịa thông số thiếu | Seed giá đáng ngờ; cấu hình tên chưa xác minh; chưa có tập đánh giá đủ rộng |

Kiểm tra DB ngày 07/11: 7 laptop/15 SKU khớp AI–BE ở identity, tên, giá, stock,
active; 14 SKU đủ stock cho similar. Không có collaborative artifact hợp lệ tại
thời điểm đó. Ngày 11/11 đã đọc lại: **95 tương tác, 0 có product_id**; chưa kiểm
tra lại toàn bộ catalog hoặc artifact collaborative trong lượt chẩn đoán này.
Đồng bộ đúng không chứng minh dữ liệu thị trường đúng.

### Phase 1B — chẩn đoán ban đầu ngày 11/11 (trước khi sửa)

| Request qua gateway (không dùng tài khoản/JWT thật) | Kết quả |
|---|---|
| Guest search `user_id=0`, không session | 200, source database, có kết quả |
| Search/chat/SSE với claimed `user_id=1`, không identity đáng tin | 403, đúng guard chống giả danh; không chứng minh test phiên đăng nhập thật |
| VIEW không backend credential | 503: Internal authentication is not configured |
| Personalized không backend credential | 503: Internal authentication is not configured |

Internal token AI được kiểm tra chỉ dưới dạng boolean và đang thiếu; không in
credential. Source gateway AI routes chưa có identity bridge. FE SSE dùng fetch
chưa gửi Authorization; search dùng Axios có interceptor nhưng gateway AI route
chưa chuyển identity đã xác thực theo contract AI. Chỉ sửa user_id ở FE không
giải quyết được identity đăng nhập đáng tin.

`CATALOG_SERVICE_URL` runtime đang là `http://localhost:8080`. GET từ bên trong
AI container tới URL đó lỗi ConnectError; cùng request qua
`http://host.docker.internal:8080` trả 200. VIEW cần xác minh product từ BE nên
ngay cả sau khi sửa auth vẫn cần chỉnh địa chỉ local này.

Các probe chat dùng claimed user bị từ chối trước business handler; không gọi
LLM. VIEW probe bị chặn trước ghi DB. Đối chiếu trước/sau vẫn 95 tương tác,
0 product_id, 0 dòng cho event chẩn đoán. Chưa kiểm tra VIEW thành công, retry/
idempotency thật hoặc giao diện. Muốn khép kín cần duyệt phạm vi gateway trust
bridge + cấu hình local AI + transport FE; không bỏ auth hoặc đưa token lên FE.

### Phase 1B — bản sửa đã duyệt và kết quả hiện tại

- Local AI: internal credential lấy từ `.env` BE qua Compose interpolation;
  không copy giá trị vào source/FE. Catalog URL đã là host.docker.internal:8080;
  GET từ container trả 200. DB, broker và `.env` gốc giữ nguyên.
- Gateway: thêm riêng `AiIdentityFilter`, không sửa AuthenticationFilter chung.
  Allowlist endpoint/method, strip header identity/internal do client gửi,
  xác thực access JWT, không chấp nhận refresh token theo claim contract hiện có.
  Personalized phải đúng tài khoản; mutation/sync/generic record không được mở.
- FE: chat/search/feedback dùng session chung với VIEW và không tự khai user_id;
  SSE gửi bearer và chỉ refresh/retry một lần sau 401, không hạ xuống guest.
  Personalized URL vẫn dùng ID làm routing hint; gateway mới là nơi xác thực.
- Gateway/config-server đang chạy JAR local Java 21 qua overlay
  `D:\Kyro\local-runtime\backend-ai.override.yml`. Docker build mặc định bị hủy
  vì kéo lại base image lớn; không đổi Dockerfile. Lệnh package/recreate ở README
  local-runtime. Không recreate các service dữ liệu/nghiệp vụ BE khác.

Live HTTP đã đạt: guest search 200, header identity giả bị loại bỏ, claimed user
không khớp 403, bearer sai 401, personalized guest 401, sync nội bộ qua gateway
403, VIEW thiếu field 422, preflight SSE 200; không lộ internal token ở response.

Probe dương tính một lần: VIEW event đầu recorded, retry duplicate, event mới
recorded; guest greeting SSE trả metadata/chunk/done, không gọi LLM; direct AI
thiếu credential vẫn 401. DB 95→98, đúng 3 dòng thử (ID 124–126), session
`demo:phase1b-20261111-v1`; 2 VIEW product 4, 1 CHAT. Retry sau khi tag giữ count=98.
Ba dòng đã được gắn model_version diagnostic-synthetic và context.source
demo_synthetic_integration; provenance nhận synthetic. Bước tag đầu gặp lỗi SQL
cast, đã sửa/tag riêng đúng 3 dòng, không chạy lại các request tạo dòng.
Không sửa/xóa sản phẩm; count catalog 5095. Không dùng các dòng test để train
hoặc làm bằng chứng chất lượng. Training loader chưa có bộ lọc tự động mọi marker;
phải chốt admission policy trước khi train. Không diễn giải 2 VIEW thử là hành vi
người dùng thực. Chi tiết và script one-shot ở local-runtime, không tự replay.

**Chưa nghiệm thu:** phiên đăng nhập thật qua auth-service và browser E2E. 8 test
gateway dùng JWT ký bằng key fixture thật, không giả token production để thay
thế tài khoản người dùng. FE dev đã mở http://localhost:5173 cho người dùng tự
đăng nhập tài khoản test; không gửi mật khẩu/token qua chat.

### Phase 1B — FE tự phục hồi sau gián đoạn

Chẩn đoán local: AI direct đã trả 200 trong lúc gateway còn khởi động; sau khi
gateway sẵn sàng, kết quả direct/gateway khớp mà không restart AI. FE trước đó
fetch một lần rồi ẩn sau lỗi, không tự tải lại. Không kết luận mọi lỗi đều do
nguyên nhân này hoặc coi restart AI là cách sửa.

Đã nối cơ chế recovery chung vào home, similar, mua kèm, giỏ hàng và fallback
không có kết quả tìm kiếm. Timeout mỗi request recommendation là 15 giây;
network/timeout/429/5xx retry sau 1/2/4/8 giây rồi mỗi 30 giây, tôn trọng
Retry-After nếu dài hơn. Tab ẩn/offline dừng lịch retry, khi hoạt động trở lại
sẽ tiếp tục; online/focus/đổi tab không vượt thời gian Retry-After. Kết quả thành
công không polling; quay lại tab/focus có thể refresh snapshot sau ít nhất 60 giây.
Giữ kết quả cũ cùng scope khi refresh lỗi, kèm cảnh báo giá/tồn kho chưa cập nhật.
Đổi sản phẩm/giỏ hàng/tài khoản hủy request cũ và không hiển thị kết quả scope cũ.
Home đọc AuthContext thay vì localStorage; lỗi personalized không bị che bằng
trending. 401 cuối cùng sau interceptor refresh hiện có, 403/404 và lỗi contract
dừng retry; kết quả rỗng hợp lệ là thành công, không phải outage.

Chỉ sửa FE và tài liệu trạng thái, không sửa ranking/BE/DB hoặc restart container.
Test có fake clock/hook harness/SSR và một HTTP server fixture + Axios thật
(503→200 trên cùng controller); không ghi dữ liệu AI. Đây chưa phải browser E2E
với phiên đăng nhập và dịch vụ Docker thật. Nghiệm thu tiếp: giữ nguyên trang,
gây lỗi tạm thời rồi phục hồi dịch vụ và quan sát card tự xuất hiện; kiểm tra
đổi tài khoản/sản phẩm trong lúc request đang chờ.

### Cập nhật sau checkpoint — đúng SKU và policy so sánh

Bản sửa tối thiểu tiếp theo: trending và cả hai nhánh personalized dùng chung
kiểm tra snapshot SKU hợp lệ/active/stock>0/sale>0 trước ranking; thiếu/sai
snapshot không được coi là khả dụng, không bù kết quả bằng sản phẩm chưa đủ dữ
kiện. Dùng variants_from_product hiện có, không phân tích cấu hình lại hoặc đổi
ranking. Trending cache thêm fingerprint catalog, dùng lại helper hash similar.
Chỉ hai file code AI đổi ở bước này; bổ sung 15 test và cập nhật fixture SKU cho
7 regression cũ, giữ nguyên assertions ranking. 1.053 tests đạt, Ruff phần liên
quan đạt. Không sửa FE/BE, không migration/train/restart hay nghiệm thu live cho
bản sửa này; giá card product-level vẫn chưa bảo đảm là giá SKU khả dụng đã chọn.

UI đã được người dùng duyệt thu gọn: bằng chứng đầy đủ vào disclosure đóng mặc
định “Chi tiết gợi ý”, vẫn giữ dấu chưa xác minh cạnh cấu hình. Chặn bubbling
click disclosure để không mở trang sản phẩm ngoài ý muốn. Bộ chọn policy chỉ
hiện với recommendation_demo=1; URL normal luôn hybrid dù có similar_policy.
14 test card/selection/UI wiring đạt; không đổi thuật toán hoặc BE trong bước này.

Rà tiếp ngày 12/11: chặn fallback khi URL yêu cầu SKU nhưng catalog FE không có
biến thể; giữ loader theo scope để retry cũ không dùng closure SKU/tài khoản mới.
17 test FE liên quan và 66 test AI SKU/policy/evaluation đạt; Ruff đạt. Không chạy
lại toàn bộ suite/build ở lượt sửa nhỏ này, không sửa BE hoặc tạo folder tạm mới.
Thay đổi sau checkpoint vẫn chưa commit/push; không nhầm với nhánh remote đã lưu.
Probe gateway ngày12/11 không kết nối được localhost8080 (0/2 kiểm tra live);
không coi kết quả HTTP/browser ngày11/11 là trạng thái runtime hôm nay.

Source AI/FE trên nhánh work đã thêm explicit target_variant_id, query variant ở
trang sản phẩm, card link giữ SKU và kiểm tra SKU sai/hết khả dụng không fallback.
Cache tách target SKU/policy. Policy hybrid giữ mặc định; capacity_first là lựa
chọn thử nghiệm gần RAM/storage rồi giá thấp hơn, không phải so hiệu năng.
Người dùng đã chốt similar gần SKU đang xem, chatbot cấu hình phù hợp ngân sách.
Xem [contract, rubric và nghiệm thu](similar-acceptance.md).

Recovery đã thu gọn: không refresh sau thành công khi quay lại tab; public hook
không chờ AuthContext, riêng home tự cung cấp identity/readiness. Request timeout
15s retry được; deadline tổng 35s dừng khi chuỗi/auth queue treo, không đẩy thêm
retry vô hạn vào queue. Không sửa auth interceptor dùng chung hoặc BE.

Kiểm tra code: 1.038 AI tests đạt, Ruff đạt; FE 57 tests đạt và build thành công.
GET qua gateway local thật: target 8→Dell variant12/HP15, target9→Dell13/HP14;
invalid SKU trả422; capacity_first chạy riêng. AI reload trên bind Windows không
nạp thay đổi nên đã restart riêng ai_service_app (command Uvicorn, không migration/
seed); FE dev localhost5173 đã bật lại. Không restart BE/DB, không gọi LLM.

Pilot sourced 4 listing/4 target: thứ tự hybrid/capacity_first đổi1/4; không nhãn
thật nên quality_evaluation_allowed=false, metrics=null, promotion_allowed=false.
Fingerprint Product snapshot chuẩn hóa:
`282a01e739ecdae642b117891e75883cb4790174ace82f497bcc12b2c242a9c6`.
Khác SHA raw capture là bình thường; không nhầm hai representation.

Browser: người dùng tự mở trang variant9; đã quan sát tài khoản hiển thị và
card16GB, đổi variant8 hiển thị card8GB. Phát hiện/fix câu reason cũ sai về SKU
đại diện, thêm regression; không coi UI account là bằng chứng personalized end-to-end.
Kiểm tra gián đoạn riêng AI đang được nghiệm thu; chưa tự ghi là pass khi mới
thấy trạng thái reconnect. Các bảng/test-count phía trên giữ mốc checkpoint cũ.

## 4. Bằng chứng kiểm tra và giới hạn

Kết quả chạy offline ngày 11/11, Python local Windows (không phải CI Linux):

| Kiểm tra | Kết quả | Ý nghĩa / không chứng minh |
|---|---|---|
| AI pytest mặc định | 1.019 passed, 1 cảnh báo Starlette/AnyIO | Regression/contract với nhiều dependency double; không chứng minh hệ thống thật |
| Ruff `app scripts tests migrations tools` | Passed | Các quy tắc lint hiện cấu hình, không phải xác minh nghiệp vụ |
| Gateway Maven test, Java 21 offline | 8 test filter mới + test error handler hiện có đạt | JWT fixture có chữ ký thật, không phải phiên đăng nhập auth-service thật |
| `pytest --collect-only -m integration` | 0 selected, 1.019 deselected; pytest exit 5 do không chọn test | Khoảng trống integration thật, không phải pass E2E |
| FE Node tests (VIEW/card/trending/mua kèm/identity/recovery) | 53 passed sau bản sửa recovery | Mapping, hook harness, SSR, transport và HTTP fixture local; không phải browser E2E |

Phân loại test mới làm một phần: `mocked_contract` đánh dấu 4 test API/handler.
Phần còn lại chưa phân loại hết. `tests/conftest.py` autouse thay catalog, LLM,
DB/auth/rate limit ở nhiều đường; service containers trong CI không tự biến các
test đó thành integration. Không chỉ thêm marker rồi tuyên bố có integration.
FE build thành công sau Phase 1B ngày 11/11, còn cảnh báo bundle lớn/import hỗn hợp;
lint FE trước đó bị thiếu `typescript-eslint`, chưa sửa/cài thêm trong Phase 0.

Benchmark local trong `artifacts/sourced-pilot-20261107/`: 9 listing ghi nguồn,
4 dùng để so sánh, 5 cách ly. Cả 4 listing ranking cùng một nhà bán lẻ, một SKU/
sản phẩm. Nút mua chỉ là proxy đủ điều kiện offline, không xác nhận tồn kho.
Hai ranker đổi thứ tự 1/4 target, top 1 không đổi; không có nhãn thật để kết luận
chất lượng. `review.random.md` chỉ có nhãn synthetic để thử xử lý; tuyệt đối không
train, tune, tính thành kết quả đồ án hoặc quyết định promote từ những nhãn này.
`artifacts/` đang được Git ignore; không force-add dataset/random/secrets.

## 5. Nhóm thay đổi để review/commit sau

Đây là nhóm review, **chưa stage/commit/push**. Không dùng `git add .`. Một số file
chứa nhiều nhóm thay đổi; cần xem từng diff/hunk để không đưa nhầm vào commit.

| Nhóm | File / nội dung chính | Điều kiện review |
|---|---|---|
| AI checkpoint đã commit | 4 commit local: `92c6300`, `47c4f46`, `1781d90`, `a8b721c` | Review correctness, interaction guard, SKU serving và preview riêng; không push main dữ liệu |
| AI test classification | `pytest.ini`, đổi `tests/test_refactored_production_suite.py` thành `tests/test_mocked_api_contracts.py` | Giữ đủ 4 test, tên/marker phản ánh mocked; không coi deleted file là bỏ coverage |
| AI trạng thái | `README.md`, `docs/current-status.md`, `docs/catalog-sku-contract-v1.md` | Không lẫn kết quả lịch sử với runtime hôm nay; không nhận FE là đã publish |
| FE VIEW | `productViewTracking.js`, `useProductDetailPage.js`, phần VIEW trong `ai.service.js`, `package.json`, VIEW test | Giữ event/session/idempotency; đường gateway/auth vẫn chưa nghiệm thu |
| FE recommendation | `recommendationCard.js`, `ProductCard.jsx`, `RelatedProducts.jsx`, các caller rating, trending method/ZeroResults, 2 file test card/trending | Kiểm tra field API, SKU/giá/rating; review hunk của `ai.service.js` riêng với VIEW |
| FE giá trang chủ | `FlashSale.jsx`, `TopSale.jsx`, `LatestTop.jsx`, phần test tương ứng | Giá bán/minPrice từ BE, không tự tính lại sale hoặc hiện N/A do sai field |
| Bridge Phase 1B | BE `AiIdentityFilter.java` + test, route `api-gateway.yml`, env gateway trong `compose.yml`; FE transport/session + identity test | Giữ tách biệt với ranking; không expose mutation nội bộ; cần test signed-in/browser trước khi tuyên bố hoàn chỉnh |
| FE recovery | `recommendationRecovery.js`, `useRecoveringRecommendations.js`, `RecommendationStatus.jsx`, 5 caller và options timeout/signal trong `ai.service.js`; test recovery/trending | Không che lỗi bằng trending; tách hunk transport/VIEW; chưa nghiệm thu browser Docker thật |

Không tạo script stage tự động từ bảng này: cùng một file test/service có thể
chứa nhiều bước. Giữ nguyên thay đổi người dùng; commit chỉ khi được yêu cầu.

## 6. Thứ tự nghiệm thu tiếp theo

| Phase | Phạm vi | Xong khi |
|---|---|---|
| 0 — trạng thái | Điểm vào tài liệu, bản source, nhóm review, ranh giới bằng chứng | Tài liệu khớp source và phân biệt rõ offline/historical/live; không đòi test thật khi Docker chưa chạy |
| 1A — sửa hiểu sai | Đã sửa: mua kèm rỗng hợp lệ mới fallback trending dưới tiêu đề/chú thích gợi ý chung; lỗi/sai dạng không fallback, hiện trạng thái lỗi/recovery; loại sản phẩm đang xem và bỏ qua response cũ | Đạt test offline cho hai nguồn, tiêu đề, rỗng/lỗi và fallback đến muộn; chưa nghiệm thu live/browser |
| 1B — luồng thật | Đã sửa và đạt live guest/VIEW/retry/SSE lời chào cùng các negative probe; dữ liệu test có marker synthetic | Còn nghiệm thu signed-in qua auth-service và browser; không coi JWT unit test là phiên thật |
| 2 — core recommendation | Tách tư vấn điều kiện/similar/mua kèm/trending; SKU target; ranking có lý do | Không trộn SKU, không nói tối ưu CPU/GPU khi chỉ so RAM/storage; policy mới chưa promote nếu chưa có đánh giá |
| 3 — hội thoại | Giữ/sửa/xóa điều kiện theo session; giới hạn văn bản LLM vào dữ kiện | Chuỗi nhiều lượt không mất/giữ nhầm điều kiện, không âm thầm nới bắt buộc |
| 4 — evaluation | Làm song song: nguồn dữ liệu, nhãn thật, tập phát triển/kiểm tra tách biệt, chất lượng + latency | Kết quả tái tạo được, báo unknown/giới hạn; không cần chờ nhãn để sửa lỗi kỹ thuật đã rõ |
| 5 — cá nhân hóa/đóng gói | Sau khi hành vi đáng tin: đánh giá collaborative; tài liệu, demo local, báo cáo | Không biến fallback/random thành kết quả thực nghiệm; deploy để sau |

**Bước kế tiếp: chốt nghiệm thu signed-in/browser của Phase 1B với tài khoản test.** Trước mỗi
bước báo bốn mục: sửa gì, file nào, kiểm tra bằng gì, điều gì vẫn chưa chứng minh.
Không tự mở rộng sang BE, DB/crawl/training/provider hoặc publish. Thiếu Docker
chặn live integration nhưng không chặn review source/test offline.
