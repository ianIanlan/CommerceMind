"""PostgreSQL implementation of the CommerceStore contract."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from commerce.store import utc_now


class PostgresCommerceStore:
    def __init__(self, database_url: str):
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._dict_row = dict_row
        self.database_url = database_url
        self._lock = threading.RLock()
        self.initialize()

    @staticmethod
    def _sql(sql: str) -> str:
        """Domain service uses DB-API qmark placeholders; psycopg uses %s."""
        return sql.replace("?", "%s")

    def _connect(self):
        return self._psycopg.connect(self.database_url, row_factory=self._dict_row)

    def initialize(self) -> None:
        migration = Path(__file__).parent / "migrations" / "001_initial.sql"
        with self._connect() as conn:
            conn.execute(migration.read_text(encoding="utf-8"))

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(self._sql(sql), params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(self._sql(sql), params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._connect() as conn:
            cur = conn.execute(self._sql(sql), params)
            return cur.rowcount

    def transaction(self, callback):
        with self._lock, self._connect() as conn:
            with conn.transaction():
                return callback(_PostgresConnectionAdapter(conn))

    def audit(self, user_id: str, event_type: str, target_id: str, detail: Dict[str, Any], request_id: str = "") -> None:
        self.execute(
            "INSERT INTO audit_events(request_id,user_id,event_type,target_id,detail_json,created_at) VALUES(?,?,?,?,?,?)",
            (request_id, user_id, event_type, target_id, json.dumps(detail, ensure_ascii=False), utc_now()),
        )

    def seed_demo_data(self) -> None:
        products = [("P-100", "降噪蓝牙耳机", "electronics", 399.0, 0), ("P-200", "云端会员年卡", "virtual", 199.0, 1), ("P-300", "轻量旅行背包", "bags", 299.0, 0)]
        orders = [
            ("ORD-10001", "demo-user", "P-100", "paid", 399.0, "2026-09-10T08:00:00+00:00", None, "上海市浦东新区演示路 1 号"),
            ("ORD-10002", "demo-user", "P-300", "shipped", 299.0, "2026-09-08T08:00:00+00:00", None, "杭州市西湖区演示路 2 号"),
            ("ORD-10003", "demo-user", "P-100", "delivered", 399.0, "2026-09-05T08:00:00+00:00", "2026-09-17T08:00:00+00:00", "上海市浦东新区演示路 1 号"),
            ("ORD-10004", "demo-user", "P-200", "delivered", 199.0, "2026-09-12T08:00:00+00:00", "2026-09-12T08:01:00+00:00", "虚拟商品"),
            ("ORD-10005", "demo-user", "P-300", "paid", 299.0, "2026-09-18T08:00:00+00:00", None, "杭州市西湖区演示路 2 号"),
            ("ORD-OTHER", "other-user", "P-100", "paid", 399.0, "2026-09-18T08:00:00+00:00", None, "北京市朝阳区演示路 3 号"),
        ]
        payments = [("PAY-10001", "ORD-10001", 399.0, "alipay", "succeeded", "2026-09-10T08:01:00+00:00"), ("PAY-10005-A", "ORD-10005", 299.0, "wechat", "succeeded", "2026-09-18T08:01:00+00:00"), ("PAY-10005-B", "ORD-10005", 299.0, "wechat", "succeeded", "2026-09-18T08:03:00+00:00")]
        shipments = [("SHIP-10002", "ORD-10002", "顺丰", "SF-DEMO-10002", "in_transit", json.dumps([
            {"time": "2026-09-18T09:00:00+00:00", "status": "已揽收"},
            {"time": "2026-09-19T02:00:00+00:00", "status": "运输中"},
        ], ensure_ascii=False))]
        with self._connect() as conn:
            # psycopg 3 exposes executemany on Cursor rather than Connection.
            with conn.cursor() as cursor:
                cursor.executemany("INSERT INTO products VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", products)
                cursor.executemany("INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", orders)
                cursor.executemany("INSERT INTO payments VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", payments)
                cursor.executemany("INSERT INTO shipments VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", shipments)


class _PostgresConnectionAdapter:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql: str, params: tuple = ()):
        return self._connection.execute(PostgresCommerceStore._sql(sql), params)
