"""The 'application' side: an OLTP shop database and a simulator producing realistic changes
(new orders, status updates, cancellations deleted by the app, customers changing segment/e-mail)."""
import random

import psycopg

from . import config

SCHEMA = """
CREATE TABLE customers (
    id         serial PRIMARY KEY,
    name       text NOT NULL,
    email      text NOT NULL,
    country    char(2) NOT NULL,
    segment    text NOT NULL DEFAULT 'bronze',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
    id          serial PRIMARY KEY,
    customer_id int NOT NULL REFERENCES customers(id),
    status      text NOT NULL DEFAULT 'new',
    amount      numeric(10,2) NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION touch() RETURNS trigger AS $$ BEGIN NEW.updated_at := clock_timestamp(); RETURN NEW; END $$
    LANGUAGE plpgsql;
CREATE TRIGGER customers_touch BEFORE UPDATE ON customers FOR EACH ROW EXECUTE FUNCTION touch();
CREATE TRIGGER orders_touch BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION touch();
"""
COUNTRIES = ["FR", "DE", "MA", "BE", "CA", "ES"]
NEXT_STATUS = {"new": "paid", "paid": "shipped", "shipped": "delivered"}


def create_database(admin_dsn: str, name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as con:
        # drop a leftover replication slot first, otherwise the database cannot be dropped
        con.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE database = %s",
                    (name,))
        con.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        con.execute(f'CREATE DATABASE "{name}"')


def seed(rng: random.Random, n_customers: int = 200, n_orders: int = 1000) -> None:
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as con:
        con.execute(SCHEMA)
        with con.cursor() as cur:
            cur.executemany("INSERT INTO customers (name, email, country, segment) VALUES (%s, %s, %s, %s)",
                            [(f"Customer {i}", f"c{i}@mail.com", rng.choice(COUNTRIES),
                              rng.choice(["bronze", "silver", "gold"])) for i in range(1, n_customers + 1)])
            cur.executemany("INSERT INTO orders (customer_id, status, amount) VALUES (%s, %s, %s)",
                            [(rng.randint(1, n_customers), rng.choice(list(NEXT_STATUS) + ["delivered"]),
                              round(rng.uniform(10, 900), 2)) for _ in range(n_orders)])


def simulate_round(rng: random.Random, new_orders: int = 120) -> dict:
    """One 'business hour' of activity, in several transactions like a real app."""
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "customers_changed": 0}
    with psycopg.connect(config.dsn(config.SOURCE_DB)) as con:
        n_cust = con.execute("SELECT max(id) FROM customers").fetchone()[0]
        for _ in range(new_orders):                                     # 1 transaction per order
            with con.transaction():
                con.execute("INSERT INTO orders (customer_id, amount) VALUES (%s, %s)",
                            (rng.randint(1, n_cust), round(rng.uniform(10, 900), 2)))
            stats["inserted"] += 1
        ids = [r[0] for r in con.execute("SELECT id FROM orders WHERE status <> 'delivered' ORDER BY random() "
                                         "LIMIT 150").fetchall()]
        with con.transaction():                                         # batch job: progress statuses
            for oid in ids:
                con.execute("UPDATE orders SET status = CASE status WHEN 'new' THEN 'paid' WHEN 'paid' THEN 'shipped'"
                            " ELSE 'delivered' END WHERE id = %s", (oid,))
            stats["updated"] += len(ids)
        with con.transaction():                                         # same order updated twice in one hour
            for oid in ids[:20]:
                con.execute("UPDATE orders SET amount = amount * 0.9 WHERE id = %s", (oid,))
            stats["updated"] += min(20, len(ids))
        gone = con.execute("DELETE FROM orders WHERE id IN (SELECT id FROM orders WHERE status = 'new' "
                           "ORDER BY random() LIMIT 15) RETURNING id").fetchall()   # cancelled -> hard-deleted
        stats["deleted"] += len(gone)
        for cid in rng.sample(range(1, n_cust + 1), 10):                 # customers move segment / e-mail
            con.execute("UPDATE customers SET segment = %s, email = %s WHERE id = %s",
                        (rng.choice(["bronze", "silver", "gold"]), f"c{cid}.{rng.randint(1, 99)}@mail.com", cid))
        stats["customers_changed"] += 10
        con.commit()
    return stats
