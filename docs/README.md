# Tài liệu AI service

| Tài liệu | Dùng khi |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Cần hiểu hệ thống: thành phần, dữ liệu, strategy gợi ý, pipeline chat, cấu hình |
| [API_CONTRACT.md](API_CONTRACT.md) | Tích hợp FE/gateway: endpoint, xác thực, request/response, SSE |
| [evaluation-report.md](evaluation-report.md) | Xem chất lượng và hiệu năng đo được, giới hạn, việc cần backend xử lý |
| [RABBITMQ_EVENT_CONTRACT.md](RABBITMQ_EVENT_CONTRACT.md) | Event `product.*` và `order.created` |
| [PRODUCT_DATA_CONTRACT.md](PRODUCT_DATA_CONTRACT.md), [catalog-sku-contract-v1.md](catalog-sku-contract-v1.md) | Định dạng dữ liệu sản phẩm và SKU |
| [MIGRATIONS.md](MIGRATIONS.md), [DATABASE_SCHEMA_DRAFT.sql](DATABASE_SCHEMA_DRAFT.sql) | Thay đổi schema ai_db |
| [similar-acceptance.md](similar-acceptance.md), [similar-pilot-review.md](similar-pilot-review.md) | Nghiệm thu similar theo SKU; phiếu chấm bằng người thật |
| [ENTERPRISE_SECURITY_AND_AUDIT.md](ENTERPRISE_SECURITY_AND_AUDIT.md) | Rủi ro bảo mật và checklist |

`archive/` chứa báo cáo tuần, checkpoint 11/11 và tài liệu của các luồng đã
gỡ: pipeline chat một lượt, tác tử cũ. Các file này chỉ để tra lịch sử.
