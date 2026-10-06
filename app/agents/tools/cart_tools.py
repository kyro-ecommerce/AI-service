import logging
import random
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger("ai-service.agents.tools.cart")

VALID_VOUCHERS = {
    "KYRO10": {"discount_percent": 10, "max_discount": 500000, "min_order": 1000000, "desc": "Giảm 10% tối đa 500k cho đơn từ 1tr"},
    "KYRO20": {"discount_percent": 20, "max_discount": 1000000, "min_order": 5000000, "desc": "Giảm 20% tối đa 1tr cho đơn từ 5tr"},
    "WELCOME50": {"discount_amount": 50000, "min_order": 200000, "desc": "Giảm 50k cho khách hàng mới"},
    "GIAM10K": {"discount_amount": 10000, "min_order": 50000, "desc": "Giảm 10k cho đơn từ 50k"},
}


def _calc_voucher(voucher_code: str, order_amount: float) -> dict:
    """Pure Python voucher calculation — callable by both @tool functions internally."""
    code_upper = voucher_code.strip().upper()
    if code_upper not in VALID_VOUCHERS:
        return {"is_valid": False, "discount_amount": 0, "voucher_code": voucher_code,
                "message": f"Mã giảm giá '{voucher_code}' không hợp lệ hoặc đã hết hạn."}
    rule = VALID_VOUCHERS[code_upper]
    min_order = rule.get("min_order", 0)
    if order_amount > 0 and order_amount < min_order:
        return {"is_valid": False, "discount_amount": 0, "voucher_code": code_upper,
                "message": f"Mã '{code_upper}' chỉ áp dụng cho đơn hàng từ {min_order:,} VNĐ."}
    discount = 0.0
    if "discount_amount" in rule:
        discount = rule["discount_amount"]
    elif "discount_percent" in rule:
        calc = order_amount * (rule["discount_percent"] / 100.0)
        discount = min(calc, rule.get("max_discount", calc))
    final_amount = max(0.0, order_amount - discount)
    return {
        "status": "success", "voucher_code": code_upper, "is_valid": True,
        "description": rule["desc"],
        "original_amount": order_amount, "discount_amount": discount,
        "discount_formatted": f"{int(discount):,} VNĐ" if discount > 0 else "0 VNĐ",
        "final_amount": final_amount,
        "final_amount_formatted": f"{int(final_amount):,} VNĐ" if final_amount > 0 else "0 VNĐ",
    }


@tool
def tool_apply_voucher(
    voucher_code: str,
    order_amount: float = 0.0,
) -> dict:
    """Validate a voucher/coupon code and calculate total savings.

    Args:
        voucher_code: Coupon code string (e.g. "KYRO10", "WELCOME50")
        order_amount: Current total order value in VNĐ

    Returns:
        Dict containing validity, discount amount, final price, and voucher description.
    """
    return _calc_voucher(voucher_code, order_amount)


@tool
def tool_create_order_draft(
    user_id: int,
    product_id: int,
    quantity: int = 1,
    voucher_code: str = "",
    product_title: str = "",
    unit_price: float = 0.0,
) -> dict[str, Any]:
    """Create an autonomous order draft for the user with coupon computation.
    
    Args:
        user_id: User identifier
        product_id: Product ID to purchase
        quantity: Item quantity (default: 1)
        voucher_code: Optional voucher code to apply
        product_title: Optional title of the product
        unit_price: Optional unit price
        
    Returns:
        Dict containing draft order ID, invoice breakdown, applied voucher, and instructions.
    """
    draft_id = f"DRAFT-KYRO-{random.randint(100000, 999999)}"
    effective_unit_price = unit_price if unit_price > 0 else 15990000.0
    subtotal = effective_unit_price * quantity

    voucher_res = None
    discount_amount = 0.0
    if voucher_code:
        # Call private helper directly (not the @tool wrapper) to avoid StructuredTool invocation
        voucher_res = _calc_voucher(voucher_code, subtotal)
        if voucher_res.get("is_valid"):
            discount_amount = voucher_res.get("discount_amount", 0.0)

    total_amount = max(0.0, subtotal - discount_amount)

    return {
        "status": "success",
        "draft_order_id": draft_id,
        "user_id": user_id or 1,
        "items": [
            {
                "product_id": product_id,
                "title": product_title or f"Sản phẩm ID #{product_id}",
                "quantity": quantity,
                "unit_price": effective_unit_price,
                "subtotal": subtotal,
                "unit_price_formatted": f"{int(effective_unit_price):,} VNĐ",
            }
        ],
        "voucher_applied": voucher_res.get("voucher_code") if (voucher_res and voucher_res.get("is_valid")) else None,
        "subtotal_formatted": f"{int(subtotal):,} VNĐ",
        "discount_formatted": f"{int(discount_amount):,} VNĐ",
        "total_amount_formatted": f"{int(total_amount):,} VNĐ",
        "checkout_url": f"/checkout?draft_id={draft_id}",
        "message": f"Đã khởi tạo đơn hàng nháp **{draft_id}** thành công! Vui lòng xác nhận thanh toán.",
    }
