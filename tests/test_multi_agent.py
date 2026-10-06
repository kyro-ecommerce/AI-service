import unittest
from langchain_core.messages import HumanMessage

from app.agents.graph import build_multi_agent_graph
from app.agents.tools.cart_tools import tool_apply_voucher, tool_create_order_draft
from app.agents.tools.compare_tools import tool_compare_products
from app.agents.tools.inventory_tools import tool_check_stock
from app.agents.tools.search_tools import tool_search_hybrid


class MultiAgentTest(unittest.TestCase):
    def test_compare_tool(self) -> None:
        res = tool_compare_products.invoke({"product_queries": ["MacBook Pro", "Dell XPS"]})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["total_compared"], 2)
        self.assertGreater(len(res["compared_products"]), 0)

    def test_search_tool(self) -> None:
        # @tool decorated functions must be called via .invoke()
        res = tool_search_hybrid.invoke({"query": "laptop", "limit": 3})
        self.assertIn("status", res)
        self.assertIn("products", res)
        self.assertIn("total_found", res)
        self.assertIn(res["status"], ["success", "error"])

    def test_inventory_tool(self) -> None:
        res = tool_check_stock.invoke({"product_name": "MacBook", "color": "Bạc"})
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["in_stock"])
        self.assertGreater(res["available_quantity"], 0)

    def test_cart_voucher_tool(self) -> None:
        res = tool_apply_voucher.invoke({"voucher_code": "KYRO10", "order_amount": 10000000.0})
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["is_valid"])
        self.assertEqual(res["discount_amount"], 500000)

    def test_cart_order_draft_tool(self) -> None:
        res = tool_create_order_draft.invoke({
            "user_id": 1,
            "product_id": 101,
            "voucher_code": "WELCOME50",
            "quantity": 1,
            "product_title": "Sản phẩm test",
            "unit_price": 500000.0,
        })
        self.assertEqual(res["status"], "success")
        self.assertIn("DRAFT-KYRO-", res["draft_order_id"])
        self.assertEqual(res["voucher_applied"], "WELCOME50")

    def test_langgraph_consultant_flow(self) -> None:
        graph = build_multi_agent_graph(db=None)
        initial_state = {
            "messages": [HumanMessage(content="Tư vấn cho mình laptop 16GB RAM")],
            "user_id": 1,
            "user_message": "Tư vấn cho mình laptop 16GB RAM",
            "recommended_products": [],
            "final_reply": "",
        }
        result = graph.invoke(initial_state)
        self.assertIn("final_reply", result)
        self.assertGreater(len(result["final_reply"]), 10)

    def test_langgraph_inventory_flow(self) -> None:
        graph = build_multi_agent_graph(db=None)
        initial_state = {
            "messages": [HumanMessage(content="Kiểm tra tồn kho MacBook Pro màu bạc")],
            "user_id": 1,
            "user_message": "Kiểm tra tồn kho MacBook Pro màu bạc",
            "recommended_products": [],
            "final_reply": "",
        }
        result = graph.invoke(initial_state)
        # ReAct agent: no next_agent field, check final_reply contains stock info
        self.assertIn("final_reply", result)
        self.assertGreater(len(result["final_reply"]), 10)

    def test_langgraph_cart_flow(self) -> None:
        graph = build_multi_agent_graph(db=None)
        initial_state = {
            "messages": [HumanMessage(content="Áp mã giảm giá KYRO10 cho đơn hàng")],
            "user_id": 1,
            "user_message": "Áp mã giảm giá KYRO10 cho đơn hàng",
            "recommended_products": [],
            "final_reply": "",
        }
        result = graph.invoke(initial_state)
        # ReAct agent: check final_reply contains KYRO10 info
        self.assertIn("final_reply", result)
        self.assertGreater(len(result["final_reply"]), 10)

    def test_langgraph_fast_path_greeting(self) -> None:
        graph = build_multi_agent_graph(db=None)
        initial_state = {
            "messages": [HumanMessage(content="Chào shop")],
            "user_id": 1,
            "user_message": "Chào shop",
            "recommended_products": [],
            "final_reply": "",
        }
        result = graph.invoke(initial_state)
        self.assertIn("final_reply", result)
        self.assertGreater(len(result["final_reply"]), 5)

    def test_langgraph_checkpointer_flow(self) -> None:
        graph = build_multi_agent_graph(db=None, with_checkpointer=True)
        initial_state = {
            "messages": [HumanMessage(content="Chào shop")],
            "user_id": 1,
            "user_message": "Chào shop",
            "recommended_products": [],
            "final_reply": "",
        }
        config = {"configurable": {"thread_id": "test_user_session_1"}}
        result = graph.invoke(initial_state, config=config)
        self.assertIn("final_reply", result)
        self.assertGreater(len(result["final_reply"]), 5)


if __name__ == "__main__":
    unittest.main()
