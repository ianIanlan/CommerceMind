from commerce.postgres_store import PostgresCommerceStore


def test_postgres_store_translates_domain_placeholders():
    assert PostgresCommerceStore._sql("SELECT * FROM orders WHERE user_id=? AND order_id=?") == (
        "SELECT * FROM orders WHERE user_id=%s AND order_id=%s"
    )
