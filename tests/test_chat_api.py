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
            json={"message": "Cho mình hỏi chuột không dây dùng văn phòng loại nào tốt?", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertLessEqual(len(data["recommended_products"]), 2)

    def test_chat_api_greeting(self) -> None:
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "xin chào shop", "limit": 2},
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


if __name__ == "__main__":
    unittest.main()



