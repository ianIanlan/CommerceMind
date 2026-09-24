# Stripe Test Mode 真实集成验证

CommerceMind 可以让退款确认动作调用 Stripe Test Mode，而不是在本地伪造支付渠道结果。该链路复用订单归属校验、退款资格判断、`prepare -> confirm`、审计和幂等状态机。

## 安全边界

- 脚本只接受 `sk_test_...`，拒绝 Stripe Live Key。
- 密钥只放在本地 `.env`，不得写入命令、日志、截图或 Git。
- 没有显式 `--execute` 时，脚本拒绝创建测试支付。
- CommerceMind `action_id` 会作为 Stripe `Idempotency-Key`，重复确认不会重复退款。
- Stripe 失败或超时时，本地事务回滚，不会向用户报告成功。

## 运行

先在本地 `.env` 添加新签发的 Stripe 测试密钥：

```env
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_BASE_URL=https://api.stripe.com
```

执行：

```bash
.runtime-venv/bin/python scripts/run_stripe_test_integration.py --execute
```

脚本会在 Stripe Test Mode 中创建一笔 ¥399.00 的测试支付，将其绑定到演示订单 `ORD-10001`，通过 CommerceMind 两阶段状态机退款，再重复确认一次以验证幂等性。输出仅包含测试资源 ID、状态和审计数量，不包含密钥。

## 证据要求

面试展示时应同时保留：

1. 脚本的脱敏 JSON 输出；
2. Stripe Dashboard Test Mode 中对应的 PaymentIntent 和 Refund；
3. CommerceMind 的 action/audit 记录；
4. 支付渠道故障测试，证明失败时没有本地退款记录且没有“成功”话术。

未实际运行脚本之前，只能表述为“已实现并自动化测试 Stripe 适配器”，不能声称已经完成真实沙箱验证。
