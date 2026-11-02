import json
import unittest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class ChatAPITest(unittest.TestCase):
    def test_chat_api_success_with_fallback(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Tư vấn cho mình laptop 16GB RAM học lập trình", "limit": 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("recommended_products", data)
        self.assertIsInstance(data["reply"], str)
        self.assertGreater(len(data["reply"]), 10)
        self.assertIsInstance(data["recommended_products"], list)

        if len(data["recommended_products"]) > 0:
            prod = data["recommended_products"][0]
            self.assertIn("product_id", prod)
            self.assertIn("title", prod)

    def test_chat_api_mouse_consultation(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={
                "message": "Cho mình hỏi chuột không dây dùng văn phòng loại nào tốt?",
                "limit": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertLessEqual(len(data["recommended_products"]), 2)

    def test_chat_api_greeting(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "xin chao shop", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertEqual(len(data["recommended_products"]), 0)

    def test_chat_api_store_qa(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "chính sách bảo hành như thế nào?", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("bảo hành", data["reply"].lower())
        self.assertIn("chưa có nguồn", data["reply"])
        self.assertEqual(data["recommended_products"], [])
        for unsupported_claim in ("12 - 24", "12-24", "30 ngày", "2h"):
            self.assertNotIn(unsupported_claim, data["reply"])

    def test_chat_api_store_policies_json_and_sse_without_unverified_promises(self) -> None:
        for message, topic in (
            ("giao hàng bao lâu?", "giao hàng"),
            ("đổi trả như thế nào?", "đổi trả"),
            ("địa chỉ của cửa hàng ở đâu?", "địa chỉ"),
        ):
            with self.subTest(message=message):
                payload = {"message": message, "user_id": 0, "limit": 2}
                normal = client.post("/api/v1/ai/chat", json=payload)
                streaming = client.post("/api/v1/ai/chat/stream", json=payload)
                self.assertEqual(normal.status_code, 200)
                self.assertEqual(streaming.status_code, 200)
                data = normal.json()
                self.assertEqual(data["recommended_products"], [])
                self.assertIn(topic, data["reply"])
                self.assertIn("chưa có nguồn", data["reply"])
                frames = [
                    json.loads(line[6:])
                    for line in streaming.text.splitlines()
                    if line.startswith("data: ")
                ]
                self.assertEqual(frames[0]["recommended_products"], [])
                self.assertEqual(
                    "".join(f["content"] for f in frames if f["type"] == "chunk"),
                    data["reply"],
                )
                self.assertEqual(frames[-1], {"type": "done"})

    def test_chat_api_category_mismatch(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "shop có bán tủ lạnh không?", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)

    def test_chat_api_invalid_limit(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Cần mua tai nghe", "limit": 0},
        )
        # Should return 422 Unprocessable Entity due to Pydantic validation (ge=1)
        self.assertEqual(response.status_code, 422)

    def test_chat_api_offtopic_math_question(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "1+1 bằng bao nhiêu?", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("2", data["reply"])

    def test_chat_stream_api_success(self) -> None:
        response = client.post(
            "/api/v1/ai/chat/stream",
            json={"message": "Tư vấn cho mình laptop 16GB RAM", "limit": 3},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        content = response.text
        self.assertIn("data: ", content)
        self.assertIn('"type": "metadata"', content)
        self.assertIn('"type": "done"', content)

    def test_chat_feedback_api_success(self) -> None:
        response = client.post(
            "/api/v1/ai/chat/feedback",
            json={"user_id": 1, "message_text": "Tư vấn laptop 16GB RAM", "feedback": "thumbs_up"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")


if __name__ == "__main__":
    unittest.main()
