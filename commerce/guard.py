"""面向电商高风险回复的确定性输出审核。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence


@dataclass(frozen=True)
class GuardResult:
    content: str
    violations: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.violations)


class CommerceResponseGuard:
    """阻止无执行证据的高风险承诺和敏感信息索取。

    这是发送回复前的最后防线，不负责评价语言风格或通用事实质量。
    """

    _SENSITIVE_REQUESTS = (
        "短信验证码",
        "支付密码",
        "完整银行卡号",
        "银行卡密码",
        "登录密码",
    )

    _UNVERIFIED_ACTIONS = (
        # 只匹配完成式承诺，不匹配“状态：成功/失败/处理中/已退款”等状态枚举。
        (re.compile(r"(?:已经(?:为您)?(?:完成)?退款(?:成功)?|已为您(?:完成)?退款(?:成功)?|已完成退款(?:成功)?|退款已(?:完成|成功))"), "UNVERIFIED_REFUND_CLAIM", "尚未执行退款；退款需要先完成资格预检并由用户确认。"),
        (re.compile(r"(?:已经|已)(?:为您)?取消订单"), "UNVERIFIED_CANCEL_CLAIM", "尚未执行取消订单；请先核验订单状态并确认操作。"),
        (re.compile(r"(?:已经|已)(?:为您)?修改(?:了)?(?:收货)?地址"), "UNVERIFIED_ADDRESS_CLAIM", "尚未执行地址修改；请先核验订单状态并确认操作。"),
    )

    def review(self, content: str, tools_used: Sequence[str] = ()) -> GuardResult:
        text = (content or "").strip()
        violations: List[str] = []

        # 先移除“请不要/切勿发送验证码”这类安全提醒，再检查剩余文本是否在索取敏感数据。
        request_text = re.sub(
            r"(?:请)?(?:不要|勿|切勿)(?:提供|发送|输入|告诉)[^。！？]*[。！？]?",
            "",
            text,
        )
        requests_sensitive_data = any(
            secret in request_text
            and (
                re.search(rf"(?:请|需要|必须|把|将).{{0,8}}(?:提供|发送|输入|告诉).{{0,12}}{re.escape(secret)}", request_text)
                or re.search(rf"(?:提供|发送|输入|告诉).{{0,8}}{re.escape(secret)}", request_text)
            )
            for secret in self._SENSITIVE_REQUESTS
        )
        if requests_sensitive_data:
            return GuardResult(
                content=(
                    "为保护账户和资金安全，请不要提供密码、短信验证码或完整银行卡号。"
                    "如需核验订单，请仅提供订单号和必要的非敏感交易摘要。"
                ),
                violations=["SENSITIVE_DATA_REQUEST"],
            )

        # /chat 中不存在执行退款、取消订单和修改地址的工具。即使模型声称完成，
        # 也必须改写为等待预检/确认的真实状态。
        for pattern, code, replacement in self._UNVERIFIED_ACTIONS:
            if pattern.search(text):
                violations.append(code)
                text = pattern.sub(replacement, text)

        if "已查询" in text and not any(
            tool in tools_used
            for tool in ("get_order", "get_logistics", "get_payment_records", "get_refund_status")
        ):
            violations.append("UNVERIFIED_QUERY_CLAIM")
            text = text.replace("已查询", "当前尚未完成查询")

        return GuardResult(content=text, violations=list(dict.fromkeys(violations)))
