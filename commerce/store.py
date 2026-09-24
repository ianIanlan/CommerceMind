"""基于 SQLite 的可复现电商演示数据仓库。

生产环境可替换为 PostgreSQL；领域服务只依赖本类公开方法。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommerceStore:
    def __init__(self, path: str):
        self.path = str(Path(path)) if path != ":memory:" else path
        self._lock = threading.RLock()
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self.path == ":memory:":
            self._memory_conn = self._new_connection()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _connect(self) -> sqlite3.Connection:
        return self._memory_conn or self._new_connection()

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._memory_conn:
            conn.close()

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            is_virtual INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(product_id),
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            delivered_at TEXT,
            address TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            payment_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            amount REAL NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            paid_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shipments (
            shipment_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            carrier TEXT NOT NULL,
            tracking_no TEXT NOT NULL,
            status TEXT NOT NULL,
            events_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refund_requests (
            refund_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            user_id TEXT NOT NULL,
            amount REAL NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(order_id, status)
        );
        CREATE TABLE IF NOT EXISTS pending_actions (
            action_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            resource_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            target_id TEXT,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS handoff_tickets (
            ticket_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            conv_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            intent TEXT NOT NULL,
            urgency TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(request_id)
        );
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(schema)
                conn.commit()
            finally:
                self._close(conn)

    def seed_demo_data(self) -> None:
        """幂等导入演示数据。"""
        products = [
            ("P-100", "降噪蓝牙耳机", "electronics", 399.0, 0),
            ("P-200", "云端会员年卡", "virtual", 199.0, 1),
            ("P-300", "轻量旅行背包", "bags", 299.0, 0),
        ]
        orders = [
            ("ORD-10001", "demo-user", "P-100", "paid", 399.0, "2026-09-10T08:00:00+00:00", None, "上海市浦东新区演示路 1 号"),
            ("ORD-10002", "demo-user", "P-300", "shipped", 299.0, "2026-09-08T08:00:00+00:00", None, "杭州市西湖区演示路 2 号"),
            ("ORD-10003", "demo-user", "P-100", "delivered", 399.0, "2026-09-05T08:00:00+00:00", "2026-09-17T08:00:00+00:00", "上海市浦东新区演示路 1 号"),
            ("ORD-10004", "demo-user", "P-200", "delivered", 199.0, "2026-09-12T08:00:00+00:00", "2026-09-12T08:01:00+00:00", "虚拟商品"),
            ("ORD-10005", "demo-user", "P-300", "paid", 299.0, "2026-09-18T08:00:00+00:00", None, "杭州市西湖区演示路 2 号"),
            ("ORD-OTHER", "other-user", "P-100", "paid", 399.0, "2026-09-18T08:00:00+00:00", None, "北京市朝阳区演示路 3 号"),
        ]
        payments = [
            ("PAY-10001", "ORD-10001", 399.0, "alipay", "succeeded", "2026-09-10T08:01:00+00:00"),
            ("PAY-10005-A", "ORD-10005", 299.0, "wechat", "succeeded", "2026-09-18T08:01:00+00:00"),
            ("PAY-10005-B", "ORD-10005", 299.0, "wechat", "succeeded", "2026-09-18T08:03:00+00:00"),
        ]
        shipments = [
            ("SHIP-10002", "ORD-10002", "顺丰", "SF-DEMO-10002", "in_transit", json.dumps([
                {"time": "2026-09-18T09:00:00+00:00", "status": "已揽收"},
                {"time": "2026-09-19T02:00:00+00:00", "status": "运输中"},
            ], ensure_ascii=False)),
        ]
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?, ?)", products)
                conn.executemany("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", orders)
                conn.executemany("INSERT OR IGNORE INTO payments VALUES (?, ?, ?, ?, ?, ?)", payments)
                conn.executemany("INSERT OR IGNORE INTO shipments VALUES (?, ?, ?, ?, ?, ?)", shipments)
                conn.commit()
            finally:
                self._close(conn)

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else None
            finally:
                self._close(conn)

    def fetch_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                return [dict(row) for row in conn.execute(sql, params).fetchall()]
            finally:
                self._close(conn)

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount
            finally:
                self._close(conn)

    def transaction(self, callback):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = callback(conn)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
            finally:
                self._close(conn)

    def audit(self, user_id: str, event_type: str, target_id: str, detail: Dict[str, Any], request_id: str = "") -> None:
        self.execute(
            "INSERT INTO audit_events(request_id,user_id,event_type,target_id,detail_json,created_at) VALUES(?,?,?,?,?,?)",
            (request_id, user_id, event_type, target_id, json.dumps(detail, ensure_ascii=False), utc_now()),
        )
