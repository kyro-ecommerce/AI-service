"""AI Chatbot RAG Knowledge Store Indexer Pipeline.

Indexes Store QA policies (warranty, delivery, returns, store info) and Product Catalog summaries,
computes 384-dimensional dense vector embeddings, and exports pre-indexed Knowledge Store (`data/chatbot_knowledge_store.json`)
for sub-5ms semantic RAG retrieval during AI Assistant conversations.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.embedding_service import generate_embedding

PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"
KNOWLEDGE_STORE_FILE = PROJECT_ROOT / "data" / "chatbot_knowledge_store.json"

STORE_POLICIES = [
    # =========================================================================
    # NHÓM A: GIAO TIẾP & THÔNG TIN CỬA HÀNG (STORE PERSONA & IDENTITY)
    # =========================================================================
    {
        "id": "qa_about_kyro",
        "topic": "about_store",
        "question": "Kyro Store là gì? Bạn là ai?",
        "keywords": ["ban la ai", "shop la ai", "kyro store", "gioi thieu", "cua hang", "kyro la gi"],
        "answer": "Kyro Store là hệ thống bán lẻ công nghệ hàng đầu chuyên cung cấp Laptop, Điện thoại, Màn hình máy tính, Tai nghe và Phụ kiện chính hãng. Mình là Trợ lý AI Tư vấn Mua sắm thông minh của Kyro Store!",
    },
    {
        "id": "qa_authentic_guarantee",
        "topic": "policy_authenticity",
        "question": "Sản phẩm tại Kyro Store có chính hãng 100% không?",
        "keywords": ["chinh hang", "hang gia", "cam ket", "xuat xu", "fake", "fullbox", "seal", "nguyen seal"],
        "answer": "100% sản phẩm tại Kyro Store là hàng chính hãng mới fullbox nguyên seal, phân phối chính thức tại Việt Nam (Apple VN/A, Asus, Lenovo, Dell, MSI, Samsung, Sony, Logitech...). Kyro Store cam kết đền bù 200% giá trị nếu phát hiện hàng giả!",
    },
    {
        "id": "qa_address",
        "topic": "policy_address",
        "question": "Địa chỉ showroom và thời gian hoạt động của Kyro Store?",
        "keywords": ["dia chi", "showroom", "cua hang", "gio mo cua", "lien he", "hotline", "chi nhanh"],
        "answer": "Kyro Store phục vụ mua sắm trực tuyến 24/7 toàn quốc. Hotline hỗ trợ kỹ thuật và mua hàng 1900 8888 (mở cửa từ 8h00 - 21h30 tất cả các ngày trong tuần, kể cả Thứ 7 & Chủ Nhật).",
    },
    {
        "id": "qa_support_channels",
        "topic": "contact_channels",
        "question": "Các kênh liên hệ tư vấn và hỗ trợ trực tuyến của Kyro Store?",
        "keywords": ["fanpage", "zalo", "email", "kenh ho tro", "tong dai", "chat online"],
        "answer": "Bạn có thể liên hệ Kyro Store qua Hotline 1900 8888, Zalo Official 'Kyro Store Official', Fanpage Facebook 'Kyro Store Công Nghệ' hoặc gửi email tới support@kyrostore.vn để được hỗ trợ 24/7!",
    },

    # =========================================================================
    # NHÓM B: BẢO HÀNH, ĐỔI TRẢ & HỖ TRỢ KỸ THUẬT (WARRANTY & TECH SUPPORT)
    # =========================================================================
    {
        "id": "qa_warranty",
        "topic": "policy_warranty",
        "question": "Chính sách bảo hành sản phẩm tại Kyro Store như thế nào?",
        "keywords": ["bao hanh", "doi tra", "loi nha san xuat", "1 doi 1", "chinh sach bao hanh", "thoi gian bao hanh"],
        "answer": "Tất cả sản phẩm tại Kyro Store đều được bảo hành chính hãng từ 12 - 24 tháng. Đặc biệt, Kyro Store áp dụng chính sách **1 ĐỔI 1 TRONG 30 NGÀY ĐẦU** nếu sản phẩm xuất hiện lỗi phần cứng từ nhà sản xuất!",
    },
    {
        "id": "qa_screen_dead_pixel_warranty",
        "topic": "policy_warranty_screen",
        "question": "Chính sách bảo hành điểm chết màn hình Laptop và Monitor?",
        "keywords": ["diem chet", "dead pixel", "bao hanh man hinh", "hieu diem chet", "soc man hinh", "dom sang"],
        "answer": "Kyro Store áp dụng chính sách bảo hành màn hình tiêu chuẩn chính hãng: Đổi mới hoặc thay thế màn hình nếu phát sinh từ 3 - 5 điểm chết trở lên hoặc màn hình bị sọc, đốm sáng lỗi nhà sản xuất!",
    },
    {
        "id": "qa_battery_warranty",
        "topic": "policy_warranty_battery",
        "question": "Chính sách bảo hành Pin và Sạc adapter đi kèm Laptop/Điện thoại?",
        "keywords": ["bao hanh pin", "chai pin", "phong pin", "bao hanh sac", "cuc sac"],
        "answer": "Pin và củ sạc đi kèm máy được bảo hành chính hãng 12 tháng. Nếu pin bị chai trên 30% trong thời gian bảo hành hoặc bị phồng pin, Kyro Store hỗ trợ thay pin mới miễn phí!",
    },
    {
        "id": "qa_tech_support",
        "topic": "service_support",
        "question": "Kyro Store có hỗ trợ cài đặt phần mềm và vệ sinh laptop không?",
        "keywords": ["cai win", "ve sinh laptop", "ho tro ky thuat", "cai phan mem", "tra keo tan nhiệt", "free cai win"],
        "answer": "Kyro Store hỗ trợ **MIỄN PHÍ** cài đặt Windows bản quyền, Office, các phần mềm đồ họa/văn phòng cơ bản và vệ sinh tra keo tản nhiệt định kỳ trọn đời cho mọi khách hàng mua máy tại Kyro Store!",
    },
    {
        "id": "qa_thermal_paste",
        "topic": "service_thermal_paste",
        "question": "Kyro Store dùng loại keo tản nhiệt nào khi vệ sinh laptop?",
        "keywords": ["keo tan nhiệt", "mx4", "phobya", "tra keo", "nhiet do cpu"],
        "answer": "Kyro Store sử dụng các dòng keo tản nhiệt gốm/kim loại cao cấp như Arctic MX-4 và Thermal Grizzly khi vệ sinh máy, giúp giảm nhiệt độ CPU/GPU từ 5 - 10°C mượt mà mát mẻ!",
    },
    {
        "id": "qa_ram_ssd_upgrade",
        "topic": "service_upgrade",
        "question": "Tôi muốn nâng cấp RAM hoặc ổ cứng SSD thì có tính phí công không?",
        "keywords": ["nang cap ram", "nang cap ssd", "phi cong", "thay ram", "them o cung"],
        "answer": "Kyro Store hỗ trợ **MIỄN PHÍ 100% công lắp đặt và cài đặt lại hệ điều hành** khi khách hàng mua linh kiện RAM/SSD nâng cấp tại cửa hàng!",
    },
    {
        "id": "qa_remote_warranty",
        "topic": "service_remote_warranty",
        "question": "Khách hàng ở tỉnh xa gửi máy bảo hành tại Kyro Store thế nào?",
        "keywords": ["bao hanh o tinh", "gui bao hanh", "chuyen phat bao hanh", "buu dien"],
        "answer": "Khách hàng ở tỉnh xa chỉ cần đóng gói máy cẩn thận và gửi qua bưu điện/Viettel Post về địa chỉ showroom Kyro Store. Kyro Store sẽ chịu 50% phí vận chuyển bảo hành 2 chiều cho khách hàng!",
    },

    # =========================================================================
    # NHÓM C: VẬN CHUYỂN, GIAO HÀNG & TRA CỨU ĐƠN (LOGISTICS & TRACKING)
    # =========================================================================
    {
        "id": "qa_shipping",
        "topic": "policy_shipping",
        "question": "Kyro Store hỗ trợ giao hàng thế nào và thời gian bao lâu?",
        "keywords": ["giao hang", "ship", "van chuyen", "phi ship", "giao hoa toc", "dong kiem", "bao lau nhan duoc"],
        "answer": "Kyro Store giao hàng **HỎA TỐC NỘI THÀNH TRONG 2 GIỜ** và giao hàng toàn quốc từ 1 - 3 ngày làm việc. Miễn phí vận chuyển cho đơn hàng từ 500.000 VNĐ và hỗ trợ **ĐỒNG KIỂM** trước khi thanh toán!",
    },
    {
        "id": "qa_express_shipping_condition",
        "topic": "policy_express_shipping",
        "question": "Điều kiện để áp dụng Giao hàng hỏa tốc 2 giờ?",
        "keywords": ["dieu kien hoa toc", "ship 2h", "giao trong ngay", "noi thanh"],
        "answer": "Giao hỏa tốc 2h áp dụng cho các đơn hàng nội thành (bán kính dưới 15km từ showroom Kyro Store) chốt đơn trước 18h00 hàng ngày!",
    },
    {
        "id": "qa_shipping_fee",
        "topic": "policy_shipping_fee",
        "question": "Phí vận chuyển giao hàng tại Kyro Store tính như thế nào?",
        "keywords": ["phi van chuyen", "cuoc ship", "mien phi ship", "freeship"],
        "answer": "Đơn hàng từ 500.000 VNĐ được **FREESHIP TOÀN QUỐC**. Đơn dưới 500.000 VNĐ áp dụng phí ship đồng giá 25.000 VNĐ toàn quốc!",
    },
    {
        "id": "qa_inspection_on_delivery",
        "topic": "policy_inspection",
        "question": "Khách hàng có được mở thùng kiểm tra máy khi nhận hàng không?",
        "keywords": ["dong kiem", "kiem hang", "boc hang", "mo hop kiem tra"],
        "answer": "Có! Kyro Store luôn cho phép khách hàng **ĐỒNG KIỂM (Mở thùng kiểm tra ngoại quan máy và phụ kiện)** trước khi ký nhận và thanh toán cho shipper!",
    },
    {
        "id": "qa_order_tracking",
        "topic": "service_order_tracking",
        "question": "Làm thế nào để tra cứu tình trạng đơn hàng của tôi?",
        "keywords": ["don hang", "tra cuu don hang", "tinh trang don hang", "kiem tra don", "ma don hang"],
        "answer": "Bạn có thể đăng nhập tài khoản vào mục 'Đơn hàng của tôi' trên website Kyro Store hoặc nhắn Mã đơn hàng / Số điện thoại đặt hàng cho AI Chatbot để tra cứu tiến độ ngay lập tức!",
    },

    # =========================================================================
    # NHÓM D: THANH TOÁN, TRẢ GÓP & HÓA ĐƠN VAT (PAYMENT, FINANCING & VAT)
    # =========================================================================
    {
        "id": "qa_payment",
        "topic": "policy_payment",
        "question": "Các hình thức thanh toán được chấp nhận tại Kyro Store?",
        "keywords": ["thanh toan", "chuyen khoan", "cod", "vnpay", "momo", "the tin dung", "quet ma qr"],
        "answer": "Kyro Store chấp nhận: Thanh toán tiền mặt COD khi nhận hàng, Chuyển khoản ngân hàng, Quét mã VNPAY-QR, Ví MoMo, ZaloPay và thẻ tín dụng/ghi nợ Visa, Mastercard, JCB!",
    },
    {
        "id": "qa_installment_credit_card",
        "topic": "policy_installment_card",
        "question": "Chính sách mua hàng trả góp 0% qua Thẻ tín dụng?",
        "keywords": ["tra gop 0%", "the tin dung", "tra gop qua the", "kỳ han tra gop"],
        "answer": "Kyro Store hỗ trợ Trả góp 0% lãi suất qua thẻ tín dụng hơn 25 ngân hàng liên kết (kỳ hạn linh hoạt 3, 6, 9, 12 tháng), thủ tục làm online trong 5 phút không cần duyệt hồ sơ!",
    },
    {
        "id": "qa_installment_finance_company",
        "topic": "policy_installment_finance",
        "question": "Chính sách mua trả góp chỉ cần CCCD qua Công ty tài chính?",
        "keywords": ["tra gop cccd", "home credit", "fe credit", "hd saison", "khong the tin dung"],
        "answer": "Không có thẻ tín dụng bạn vẫn mua trả góp được! Chỉ cần CCCD gắn chip (từ 18 tuổi trở lên) làm hồ sơ qua Home Credit, FE Credit, HD SAISON. Duyệt hồ sơ nhanh 15 phút, trả trước từ 10 - 20%!",
    },
    {
        "id": "qa_vat_invoice",
        "topic": "policy_invoice",
        "question": "Kyro Store có xuất hóa đơn VAT cho doanh nghiệp không?",
        "keywords": ["vat", "hoa don", "doanh nghiep", "xuat hoa don", "cong ty", "hoa don dien tu"],
        "answer": "Kyro Store hỗ trợ xuất hóa đơn điện tử VAT đầy đủ (đã bao gồm thuế GTGT) cho khách hàng cá nhân và doanh nghiệp ngay trong ngày mua hàng!",
    },

    # =========================================================================
    # NHÓM E: ƯU ĐÃI, THU CŨ ĐỔI MỚI & QUÀ TẶNG (PROMOTIONS & TRADE-IN)
    # =========================================================================
    {
        "id": "qa_student_discount",
        "topic": "promotion_student",
        "question": "Kyro Store có ưu đãi cho Học sinh - Sinh viên không?",
        "keywords": ["sinh vien", "hoc sinh", "giam gia sinh vien", "uu dai sinh vien", "back to school", "the sinh vien"],
        "answer": "Có! Chương trình **'Back to School'** tại Kyro Store giảm thêm đến 5% (tối đa 500.000 VNĐ) cho Học sinh - Sinh viên khi mua Laptop/Tablet khi xuất trình thẻ học sinh/sinh viên hợp lệ!",
    },
    {
        "id": "qa_trade_in",
        "topic": "service_trade_in",
        "question": "Chính sách Thu cũ đổi mới (Trade-in) lên đời máy tại Kyro Store?",
        "keywords": ["thu cu doi moi", "trade in", "doi may cu", "len doi", "tro gia thu cu"],
        "answer": "Kyro Store áp dụng chính sách **Thu cũ Đổi mới trợ giá lên đến 2.000.000 VNĐ** cho Laptop, Điện thoại cũ. Kỹ thuật viên định giá máy công khai, nhanh chóng trong 15 phút!",
    },
    {
        "id": "qa_laptop_free_gifts",
        "topic": "promotion_gifts",
        "question": "Mua Laptop tại Kyro Store được tặng kèm những quà tặng gì?",
        "keywords": ["qua tang laptop", "tang gi", "balo", "chuot khong day", "lot chuot"],
        "answer": "100% Laptop bán ra tại Kyro Store được tặng kèm: Balo laptop chống sốc cao cấp, Chuột không dây chính hãng, Lót chuột Gaming và Bộ vệ sinh màn hình chuyên dụng!",
    },
    {
        "id": "qa_loyalty_voucher",
        "topic": "promotion_loyalty",
        "question": "Ưu đãi dành cho Khách hàng thân thiết và sinh nhật?",
        "keywords": ["voucher sinh nhat", "khach hang than thiet", "tich diem", "vip"],
        "answer": "Khách hàng cũ mua lần 2 hoặc có ngày sinh nhật trong tháng được tặng Voucher giảm trực tiếp 200.000 - 500.000 VNĐ và giảm 10% khi mua phụ kiện kèm theo!",
    },

    # =========================================================================
    # NHÓM F: TƯ VẤN CẤU HÌNH & CHỌN MUA THEO NGÀNH NGHỀ (USE-CASES)
    # =========================================================================
    {
        "id": "consult_coder_it",
        "topic": "consultation_usecase",
        "question": "Tư vấn cấu hình Laptop cho Sinh viên IT, Lập trình viên?",
        "keywords": ["lap trinh", "coder", "cong nghe thong tin", "it", "chay virtual machine", "docker", "code"],
        "answer": "Đối với nhu cầu Lập trình / IT: Nên chọn Laptop có tối thiểu **RAM 16GB** (để chạy Docker, VSCode, Android Studio mượt mà), CPU từ Intel Core i5/i7 thế hệ 12 trở lên hoặc AMD Ryzen 5/7, hoặc các dòng MacBook M1/M2/M3 RAM 16GB trở lên!",
    },
    {
        "id": "consult_graphic_editor",
        "topic": "consultation_usecase",
        "question": "Tư vấn Laptop cho Sinh viên Thiết kế đồ họa, Photoshop, Edit Video?",
        "keywords": ["do hoa", "thiet ke", "photoshop", "premiere", "edit video", "render 3d", "chuan mau", "srgb"],
        "answer": "Dành cho Thiết kế đồ họa & Edit Video: Ưu tiên màn hình độ phủ màu cao **100% sRGB / DCI-P3** để không bị lệch màu, RAM từ 16GB đến 32GB và trang bị Card màn hình rời NVIDIA RTX (RTX 3050 / RTX 4050 trở lên) để tăng tốc Render!",
    },
    {
        "id": "consult_gaming_esport",
        "topic": "consultation_usecase",
        "question": "Tư vấn chọn Laptop Gaming chơi mượt Valorant, LOL, FO4, AAA?",
        "keywords": ["gaming", "choi game", "valorant", "lol", "lien minh", "fo4", "game aaa", "tan so quet", "144hz"],
        "answer": "Dành cho chơi Game Gaming: Nên chọn Laptop có màn hình tần số quét từ **144Hz - 240Hz** chống xé hình, trang bị CPU dòng H (Performance) kết hợp Card đồ họa NVIDIA GeForce RTX 40-series và tản nhiệt 2 quạt để duy trì FPS ổn định!",
    },
    {
        "id": "consult_office_business",
        "topic": "consultation_usecase",
        "question": "Tư vấn Laptop mỏng nhẹ cho Dân văn phòng, Doanh nhân, Khối kinh tế?",
        "keywords": ["van phong", "mong nghe", "doanh nhan", "sang trong", "pin lau", "excel", "kinh te"],
        "answer": "Dành cho Văn phòng & Doanh nhân: Ưu tiên Laptop trọng lượng mỏng nhẹ **duới 1.3kg**, vỏ hợp kim nhôm sang trọng, thời lượng pin từ 8 - 12 tiếng, bàn phím gõ êm (như Lenovo ThinkPad, Asus Zenbook, MacBook Air M2/M3)!",
    },
    {
        "id": "consult_phone_camera",
        "topic": "consultation_usecase",
        "question": "Tư vấn Điện thoại chụp ảnh đẹp, Quay video 4K, Livestream mượt?",
        "keywords": ["dien thoai chup anh", "camera dep", "quay video 4k", "livestream", "chong rung ois"],
        "answer": "Dành cho Chụp ảnh & Quay phim: Ưu tiên các flagship có chống rung quang học OIS, cảm biến lớn như iPhone 15 Pro / 15 Pro Max (màu sắc điện ảnh, mic thu âm chuẩn) hoặc Samsung Galaxy S24 Ultra (Camera 200MP, zoom quang 100x)!",
    },
    {
        "id": "consult_phone_gaming",
        "topic": "consultation_usecase",
        "question": "Tư vấn Điện thoại chơi game mượt, Pin trâu, Sạc nhanh?",
        "keywords": ["dien thoai choi game", "pin trau", "sac nhanh", "snapdragon 8 gen", "120hz"],
        "answer": "Dành cho Game thủ di động: Chọn smartphone trang bị chip Snapdragon 8 Gen 2 / 8 Gen 3 hoặc Apple A17 Pro, màn hình OLED 120Hz mượt mà và pin dung lượng từ 5000mAh hỗ trợ sạc nhanh từ 45W - 67W trở lên!",
    },
    {
        "id": "consult_studio_monitor",
        "topic": "consultation_usecase",
        "question": "Tư vấn Màn hình máy tính Đồ họa chuẩn màu chuyên nghiệp?",
        "keywords": ["man hinh do hoa", "chuan mau", "4k", "type c 65w", "ips 4k"],
        "answer": "Dành cho Màn hình Đồ họa: Chọn tấm nền IPS chuẩn màu **100% sRGB / 98% DCI-P3**, độ phân giải 2K hoặc 4K (như dòng Dell UltraSharp U2723QE, Asus ProArt), có cổng Type-C hỗ trợ truyền hình ảnh và sạc ngược 65W cho laptop!",
    },
    {
        "id": "consult_gaming_monitor",
        "topic": "consultation_usecase",
        "question": "Tư vấn Màn hình máy tính chơi Game tần số quét cao 144Hz - 240Hz?",
        "keywords": ["man hinh gaming", "144hz", "180hz", "240hz", "0.5ms", "freesync"],
        "answer": "Dành cho Màn hình Gaming: Chọn màn hình có tần số quét **144Hz, 180Hz hoặc 240Hz**, thời gian phản hồi siêu nhanh 1ms (thậm chí 0.5ms) và hỗ trợ công nghệ chống xé hình AMD FreeSync / NVIDIA G-Sync!",
    },
    {
        "id": "consult_anc_earbuds",
        "topic": "consultation_usecase",
        "question": "Tư vấn Tai nghe chống ồn ANC tốt nhất để đi làm, học tập?",
        "keywords": ["tai nghe chong on", "anc", "sony wh1000xm5", "airpods pro 2", "am bass"],
        "answer": "Dành cho Tai nghe chống ồn: Lựa chọn hàng đầu là Sony WH-1000XM5 (chống ồn trùm đầu số 1 thế giới) hoặc Apple AirPods Pro 2 (nhỏ gọn, chống ồn thông minh). Giúp triệt tiêu tiếng ồn máy bay, xe cộ mượt mà!",
    },
    {
        "id": "consult_silent_gear",
        "topic": "consultation_usecase",
        "question": "Tư vấn Combo Bàn phím cơ & Chuột không dây yên tĩnh cho văn phòng?",
        "keywords": ["ban phim silent", "chuot khong tieng dong", "combo yen tinh", "logitech mx master"],
        "answer": "Dành cho Không gian yên tĩnh: Chọn Chuột Silent không tiếng click (như Logitech MX Master 3S / Anywhere 3S) kết hợp Bàn phím cơ dùng Silent Switch (như AKKO, Keychron) để không làm phiền đồng nghiệp xung quanh!",
    },

    # =========================================================================
    # NHÓM G: MẸO CÔNG NGHỆ & GIẢI ĐÁP KỸ THUẬT (HARDWARE TIPS & TROUBLESHOOTING)
    # =========================================================================
    {
        "id": "tip_ram_8gb_vs_16gb",
        "topic": "tech_advice",
        "question": "Nên mua Laptop RAM 8GB hay RAM 16GB ở thời điểm hiện tại?",
        "keywords": ["ram 8gb", "ram 16gb", "nen mua ram bao nhieu", "co nen nang ram"],
        "answer": "Ở thời điểm hiện tại, **RAM 16GB là tiêu chuẩn tối thiểu đề xuất**. RAM 8GB dễ bị tràn bộ nhớ khi mở nhiều tab Chrome và làm việc đa nhiệm. Chọn 16GB RAM giúp máy chạy mượt mà lâu dài 3-5 năm tới!",
    },
    {
        "id": "tip_charging_laptop",
        "topic": "tech_advice",
        "question": "Có nên vừa cắm sạc vừa dùng Laptop không? Có sợ chai pin không?",
        "keywords": ["cam sac khi dung", "vua sac vua dung", "chai pin laptop", "cai phan mem ngat sac"],
        "answer": "Bạn **HOÀN TOÀN NÊN vừa cắm sạc vừa dùng Laptop**! Các dòng Laptop hiện đại có mạch tự động ngắt sạc khi pin đầy và dùng nguồn điện trực tiếp từ adapter. Cắm sạc giúp máy phát huy 100% hiệu năng CPU/GPU!",
    },
    {
        "id": "tip_oled_vs_ips",
        "topic": "tech_advice",
        "question": "Sự khác biệt giữa màn hình OLED và màn hình IPS?",
        "keywords": ["oled", "ips", "so sanh oled va ips", "man hinh nao tot hon"],
        "answer": "Màn hình OLED có màu đen tuyệt đối, độ tương phản cực cao và màu sắc sống động rực rỡ. Trong khi màn hình IPS có độ bền cao, hiển thị góc nhìn rộng và trung thực. Dùng đồ họa/giải trí phim ảnh thì OLED là lựa chọn số 1!",
    },
    {
        "id": "tip_nvme_vs_sata",
        "topic": "tech_advice",
        "question": "Ổ cứng SSD NVMe PCIe khác gì ổ cứng SSD SATA thông thường?",
        "keywords": ["nvme", "sata", "ssd nvme", "toc do ssd", "so sanh ssd"],
        "answer": "SSD NVMe PCIe có tốc độ đọc ghi siêu nhanh từ 3.500MB/s - 7.000MB/s (gấp 6 - 12 lần SSD SATA thông thường 550MB/s), giúp khởi động Windows trong 5 giây và mở phần mềm ứng dụng tức thì!",
    },
    {
        "id": "tip_intel_vs_ryzen",
        "topic": "tech_advice",
        "question": "Nên chọn Laptop CPU Intel Core i5/i7 hay AMD Ryzen 5/7?",
        "keywords": ["intel vs ryzen", "so sanh intel va amd", "i5 vs ryzen 5", "i7 vs ryzen 7"],
        "answer": "Intel Core có ưu thế về hiệu năng đơn nhân cao và tính tương thích phần mềm tối ưu. Trong khi AMD Ryzen có ưu thế về số nhân/luồng vượt trội, mát máy và tiết kiệm pin. Cả 2 dòng chip đều rất mạnh mẽ cho nhu cầu hiện đại!",
    },
    {
        "id": "tip_iris_xe_gaming",
        "topic": "tech_advice",
        "question": "Card màn hình tích hợp Intel Iris Xe Graphics có chơi được game không?",
        "keywords": ["iris xe", "card on", "choi game card on", "iris xe choi duoc game gi"],
        "answer": "Intel Iris Xe Graphics chơi tốt các game eSports nhẹ như Liên Minh Huyền Thoại (LOL), FIFA Online 4 (FO4), CS:GO ở cài đặt Medium/High mượt mà. Tuy nhiên với game nặng 3D AAA thì cần mua Laptop có Card rời NVIDIA RTX!",
    },
    {
        "id": "tip_dpi_mouse",
        "topic": "tech_advice",
        "question": "Chỉ số DPI trên chuột máy tính là gì? DPI bao nhiêu là đủ?",
        "keywords": ["dpi chuot", "chiso dpi", "chinh dpi", "dpi gaming"],
        "answer": "DPI (Dots Per Inch) là chỉ số đo độ nhạy di chuyển của con trỏ chuột. Dùng làm việc văn phòng thường để 800 - 1200 DPI. Dùng chơi game FPS hoặc thiết kế màn hình 4K thì nên dùng chuột có DPI từ 1600 - 3200+!",
    },
    {
        "id": "tip_keyboard_switches",
        "topic": "tech_advice",
        "question": "Bàn phím cơ Switch Red, Blue, Brown khác nhau như thế nào?",
        "keywords": ["red switch", "blue switch", "brown switch", "cong tac ban phim co"],
        "answer": "• **Blue Switch**: Có tiếng clicky gõ rất giòn tai (thích hợp dùng 1 mình).\n• **Red Switch**: Gõ êm trơn nhẹ (Linear), không tiếng ồn, thích hợp gõ nhanh và chơi game nocturne.\n• **Brown Switch**: Dung hòa có khấc phản hồi nhẹ (Tactile) vừa gõ sướng vừa không quá ồn!",
    },
    {
        "id": "tip_battery_health_check",
        "topic": "tech_advice",
        "question": "Cách tự kiểm tra độ chai pin Laptop bằng lệnh Windows?",
        "keywords": ["kieu tra chai pin", "battery report", "do dung luong pin", "check pin laptop"],
        "answer": "Bạn mở phần mềm `Command Prompt (cmd)` gõ lệnh: `powercfg /batteryreport` rồi nhấn Enter. Mở file HTML tạo ra để xem thông số 'FULL CHARGE CAPACITY' so với 'DESIGN CAPACITY' để biết chính xác độ chai pin!",
    },
    {
        "id": "tip_wifi_troubleshooting",
        "topic": "tech_advice",
        "question": "Cách khắc phục nhanh sự cố Laptop bắt sóng Wi-Fi chập chờn?",
        "keywords": ["wifi chap chon", "mat wifi", "loi wifi laptop", "doi dns google"],
        "answer": "Cách xử lý nhanh: 1. Khởi động lại Router Wi-Fi. 2. Vào Device Manager xóa bớt Driver Network Card rồi nhấn 'Scan for hardware changes'. 3. Đổi DNS sang Google (8.8.8.8 và 8.8.4.4) trong cài đặt mạng!",
    },

    # =========================================================================
    # NHÓM H: TƯ VẤN THEO PHÂN KHÚC NGÂN SÁCH (BUDGET SEGMENT CONSULTATION)
    # =========================================================================
    {
        "id": "budget_laptop_under_10m",
        "topic": "budget_consultation",
        "question": "Tư vấn Laptop giá rẻ dưới 10 triệu đồng cho học sinh, văn phòng nhẹ?",
        "keywords": ["duoi 10 trieu", "duoi 10tr", "laptop gia re", "10 triệu"],
        "answer": "Phân khúc dưới 10 triệu: Ưu tiên các dòng Laptop trang bị CPU Intel Core i3 / AMD Ryzen 3, RAM 8GB, ổ cứng SSD 256GB từ các hãng Asus, HP, Lenovo. Đáp ứng tốt nhu cầu lướt web, học online Zoom/Teams, Word/Excel!",
    },
    {
        "id": "budget_laptop_10m_15m",
        "topic": "budget_consultation",
        "question": "Tư vấn Laptop từ 10 đến 15 triệu cho Sinh viên?",
        "keywords": ["10 den 15 trieu", "10-15tr", "laptop sinh vien 15tr", "15 triệu"],
        "answer": "Phân khúc 10 - 15 triệu (Phân khúc Quốc dân): Chọn các dòng Laptop trang bị CPU Intel Core i5 Gen 12/13 hoặc Ryzen 5, RAM 8GB - 16GB, SSD 512GB. Máy chạy mượt đa nhiệm, chỉnh sửa ảnh Photoshop nhẹ và chơi mượt LOL/FO4!",
    },
    {
        "id": "budget_laptop_15m_20m",
        "topic": "budget_consultation",
        "question": "Tư vấn Laptop từ 15 đến 20 triệu cho Coder, Đồ họa nhẹ, Gaming tầm trung?",
        "keywords": ["15 den 20 trieu", "15-20tr", "laptop 20tr", "20 triệu"],
        "answer": "Phân khúc 15 - 20 triệu: Lựa chọn mỏng nhẹ cao cấp (MacBook Air M1, Asus Zenbook) hoặc Laptop Gaming giá rẻ (Asus TUF, Lenovo IdeaPad Gaming, MSI GF63) có Card rời GTX 1650 / RTX 2050 / RTX 3050!",
    },
    {
        "id": "budget_laptop_20m_30m",
        "topic": "budget_consultation",
        "question": "Tư vấn Laptop từ 20 đến 30 triệu cao cấp cho Chuyên gia & Game thủ?",
        "keywords": ["20 den 30 trieu", "20-30tr", "laptop 30tr", "30 triệu"],
        "answer": "Phân khúc 20 - 30 triệu: Bạn có thể sở hữu MacBook Air M2/M3, ThinkPad X1 Carbon mỏng nhẹ doanh nhân hoặc Laptop Gaming hiệu năng cao màn 165Hz trang bị Card đồ họa NVIDIA GeForce RTX 4050 / RTX 4060!",
    },
    {
        "id": "budget_laptop_over_30m",
        "topic": "budget_consultation",
        "question": "Tư vấn Laptop Flagship siêu cấp trên 30 triệu đồng?",
        "keywords": ["tren 30 trieu", "laptop khung", "macbook pro m3", "rtx 4070", "rtx 4080"],
        "answer": "Phân khúc trên 30 triệu Flagship: Dành cho công việc nặng chuyên nghiệp (MacBook Pro 14/16 M3 Pro/Max, ROG Strix, Legion Pro 7, Dell XPS). Đạt đỉnh cao về màn hình mini-LED / OLED và sức mạnh đồ họa 3D!",
    },
    {
        "id": "budget_phone_under_5m",
        "topic": "budget_consultation",
        "question": "Tư vấn Điện thoại dưới 5 triệu pin trâu, màn hình to?",
        "keywords": ["dien thoai duoi 5tr", "phone 5 triệu", "dien thoai gia re"],
        "answer": "Dưới 5 triệu: Ưu tiên các dòng Samsung Galaxy A-series hoặc Xiaomi Redmi có màn hình AMOLED 90Hz/120Hz rộng rãi, pin dung lượng lớn 5000mAh và sạc nhanh 33W!",
    },
    {
        "id": "budget_phone_15m_plus",
        "topic": "budget_consultation",
        "question": "Tư vấn Điện thoại Cao cấp / Flagship từ 15 đến 30 triệu?",
        "keywords": ["flagship", "iphone 15 pro max", "s24 ultra", "dien thoai cao cap"],
        "answer": "Phân khúc 15 - 30 triệu Flagship: iPhone 15 / 15 Pro Max (khung Titan, chip A17 Pro, quay phim 4K ProRes) hoặc Samsung Galaxy S24 Ultra (bút S-Pen, AI Zoom 100x, khung Titanium) là 2 đại diện xuất sắc nhất!",
    },

    # =========================================================================
    # NHÓM I: CÁC CHUẨN PHẦN CỨNG & CỔNG KẾT NỐI (STANDARDS & CONNECTIVITY)
    # =========================================================================
    {
        "id": "standard_thunderbolt_4",
        "topic": "hardware_standard",
        "question": "Cổng Thunderbolt 4 / USB4 trên Laptop có tác dụng gì?",
        "keywords": ["thunderbolt 4", "usb4", "type c thunderbolt", "egpu"],
        "answer": "Thunderbolt 4 có băng thông siêu khủng **40Gbps**. Giúp bạn xuất ra 2 màn hình 4K cùng lúc, sạc siêu nhanh PD 100W, truyền file dung lượng lớn và cắm Card đồ họa rời eGPU bên ngoài!",
    },
    {
        "id": "standard_wifi_6e_7",
        "topic": "hardware_standard",
        "question": "Wi-Fi 6 / Wi-Fi 6E / Wi-Fi 7 có nhanh hơn Wi-Fi 5 không?",
        "keywords": ["wifi 6", "wifi 6e", "wifi 7", "so sanh wifi"],
        "answer": "Wi-Fi 6 / 6E nhanh hơn Wi-Fi 5 gấp 3 lần, giảm độ trễ 75% và hỗ trợ kết nối nhiều thiết bị cùng lúc không bị nghẽn mạng. Giúp tải file nhanh và chơi game online không bị giật lag ping!",
    },
    {
        "id": "standard_military_durability",
        "topic": "hardware_standard",
        "question": "Tiêu chuẩn độ bền Quân đội Mỹ MIL-STD-810H trên Laptop là gì?",
        "keywords": ["mil std 810h", "do ben quan doi", "laptop va đap", "chong rơi vỡ"],
        "answer": "Tiêu chuẩn MIL-STD-810H bao gồm 12 - 20 bài kiểm tra khắc nghiệt: Chịu va đập khi rơi từ độ cao 1m, chịu nhiệt độ nóng/lạnh cực đoan, chống sương muối và độ ẩm. Giúp máy bền bỉ suốt nhiều năm!",
    },
    {
        "id": "standard_gan_charger",
        "topic": "hardware_standard",
        "question": "Củ sạc công nghệ GaN (Gallium Nitride) là gì? Có nên mua không?",
        "keywords": ["sac gan", "gallium nitride", "cuc sac gan", "sac nhanh gan 65w"],
        "answer": "Củ sạc GaN sử dụng vật liệu Gallium Nitride giúp củ sạc có kích thước **nhỏ gọn hơn 40%** so với sạc thường nhưng đạt công suất cao 65W - 100W - 140W, ít tỏa nhiệt và sạc được cho cả Laptop, iPad lẫn Điện thoại!",
    },

    # =========================================================================
    # NHÓM J: NGÀNH NGHỀ ĐẶC THÙ (SPECIALIZED PROFESSIONS)
    # =========================================================================
    {
        "id": "prof_medical_students",
        "topic": "prof_consultation",
        "question": "Tư vấn Laptop cho Sinh viên Y Dược, Bác sĩ?",
        "keywords": ["sinh vien y", "y duoc", "bac si", "di lam sang", "benh vien"],
        "answer": "Dành cho Sinh viên Y Dược / Bác sĩ: Ưu tiên Laptop mỏng nhẹ dưới 1.2kg, màn hình sắc nét đọc tài liệu y khoa không mỏi mắt, thời lượng pin từ 10-12 tiếng để đi trực bệnh viện và vỏ máy dễ lau chùi khử khuẩn (như LG Gram, MacBook Air)!",
    },
    {
        "id": "prof_architecture_construction",
        "topic": "prof_consultation",
        "question": "Tư vấn Laptop cho Sinh viên Kiến trúc, Xây dựng, AutoCAD, Revit, SketchUp?",
        "keywords": ["kien truc", "xay dung", "autocad", "revit", "sketchup", "3ds max", "lumion"],
        "answer": "Dành cho Kiến trúc & Xây dựng: Bắt buộc chọn Laptop có CPU hiệu năng cao (Core i7/i9 hoặc Ryzen 7/9 dòng H/HX), RAM tối thiểu 32GB, SSD NVMe 1TB và Card rời NVIDIA RTX 4060 trở lên để Dựng hình 3D & Render Lumion/3ds Max không bị giật đơ!",
    },
    {
        "id": "prof_accountant_excel",
        "topic": "prof_consultation",
        "question": "Tư vấn Laptop cho Kế toán, Khối Tài chính, Xử lý file Excel nặng?",
        "keywords": ["ke toan", "tai chinh", "excel nang", "ban phim so", "numpad"],
        "answer": "Dành cho Kế toán & Tài chính: Bắt buộc chọn Laptop có **Bàn phím số riêng (Numpad phụ)** để nhập liệu số nhanh, màn hình lớn 15.6 inch / 16 inch chống chói, RAM 16GB để mở mượt các file Excel hàng trăm ngàn dòng!",
    },
    {
        "id": "prof_streamer_content_creator",
        "topic": "prof_consultation",
        "question": "Tư vấn Laptop cho Streamer, TikToker, YouTuber quay dựng nội dung?",
        "keywords": ["streamer", "tiktoker", "youtuber", "obs", "stream game", "quay dung video"],
        "answer": "Dành cho Streamer & Creator: Ưu tiên Laptop trang bị GPU NVIDIA RTX 40-series có nhân **NVENC Encoder** giúp mã hóa luồng Stream 4K không tụt FPS game, Webcam 1080p sắc nét và kết nối Wi-Fi 6E tốc độ cao!",
    },

    # =========================================================================
    # NHÓM K: XỬ LÝ SỰ CỐ KHẨN CẤP & PHỤ KIỆN KÈM THEO (EMERGENCY & ACCESSORIES)
    # =========================================================================
    {
        "id": "emergency_water_damage",
        "topic": "emergency_fix",
        "question": "Laptop bị dính nước / đổ nước vào bàn phím thì phải xử lý thế nào khẩn cấp?",
        "keywords": ["laptop vo nuoc", "do nuoc vao ban phim", "laptop uot", "xu ly vo nuoc"],
        "answer": "Xử lý khẩn cấp khi tràn nước: 1. RÚT SẠC & NHẤN GIỮ NÚT NGUỒN TẮT MÁY NGAY LẬP TỨC! 2. Úp ngược bàn phím laptop xuống khăn bông mịn. 3. KHÔNG DÙNG MÁY SẤY TÓC sấy vì nhiệt cao làm hỏng phím. 4. Mang ngay đến showroom Kyro Store sấy khô xử lý chống chập!",
    },
    {
        "id": "accessory_laptop_stand",
        "topic": "accessory_consultation",
        "question": "Giá đỡ Laptop nhôm tản nhiệt có thực sự hiệu quả không?",
        "keywords": ["gia do laptop", "laptop stand", "de tan nhiet", "ke laptop"],
        "answer": "Giá đỡ Laptop hợp kim nhôm rất hiệu quả! Giúp nâng độ cao màn hình ngang tầm mắt bảo vệ đốt sống cổ, đồng thời nâng thoáng đáy máy giúp quạt tản nhiệt hút gió mát nạp vào, giảm từ 3 - 7°C nhiệt độ CPU!",
    },
    {
        "id": "accessory_usb_hub",
        "topic": "accessory_consultation",
        "question": "Nên mua Hub chuyển đổi Type-C sang HDMI / USB nào cho MacBook, Ultrabook?",
        "keywords": ["hub type c", "cong chuyen doi", "hub macbook", "usb c to hdmi"],
        "answer": "Dành cho Ultrabook/MacBook thiếu cổng: Nên chọn Hub chuyển đổi cổng nhôm từ các thương hiệu Anker, Baseus, Ugreen có hỗ trợ xuất hình HDMI 4K@60Hz, truyền dữ liệu USB 3.0 và cổng sạc Type-C PD 100W tiện lợi!",
    },
]



def run_chatbot_knowledge_indexing():
    print("=" * 70)
    print("       AI CHATBOT RAG KNOWLEDGE INDEXER & VECTOR STORE ENGINE       ")
    print("=" * 70)

    # 1. Index Store Policy QA
    print(f"Indexing {len(STORE_POLICIES)} store QA policies...")
    indexed_qa = []
    for item in STORE_POLICIES:
        content_text = f"{item['question']} {item['answer']} {' '.join(item['keywords'])}"
        embedding = generate_embedding(content_text)
        indexed_qa.append({
            "id": item["id"],
            "topic": item["topic"],
            "question": item["question"],
            "answer": item["answer"],
            "keywords": item["keywords"],
            "embedding": embedding,
        })

    # 2. Index Products Summary Knowledge
    indexed_products = []
    if PRODUCTS_FILE.exists():
        products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
        print(f"Indexing semantic knowledge for {len(products)} products...")
        for p in products:
            pid = p.get("product_id")           
            title = p.get("title") or p.get("name") or ""
            cat = p.get("category_name") or p.get("category") or ""
            price = p.get("discounted_price") or p.get("original_price") or p.get("price") or 0
            brand = p.get("brand") or ""
            desc = p.get("description") or ""

            summary_text = f"{title} | Danh mục: {cat} | Thương hiệu: {brand} | Giá: {price:,} VNĐ | Mô tả: {desc[:100]}"
            embedding = p.get("embedding") or generate_embedding(summary_text)

            indexed_products.append({
                "product_id": pid,
                "title": title,
                "category_name": cat,
                "brand": brand,
                "price": price,
                "summary": summary_text,
                "embedding": embedding,
            })

    store_payload = {
        "metadata": {
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "total_qa_items": len(indexed_qa),
            "total_products_indexed": len(indexed_products),
            "embedding_dim": 384,
        },
        "qa_items": indexed_qa,
        "product_items": indexed_products,
    }

    KNOWLEDGE_STORE_FILE.write_text(json.dumps(store_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Successfully generated Chatbot RAG Knowledge Store in {KNOWLEDGE_STORE_FILE}!")
    return store_payload


if __name__ == "__main__":
    run_chatbot_knowledge_indexing()
