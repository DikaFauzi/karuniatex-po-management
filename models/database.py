import os
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "po_an_ots.db"

DEFAULT_USERS = [
    ("server", "SERVER", "SERVER", os.getenv("SERVER_PIN", "909090")),
    ("admin1", "ADMIN 1", "ADMIN", os.getenv("ADMIN1_PIN", "111111")),
    ("admin2", "ADMIN 2", "ADMIN", os.getenv("ADMIN2_PIN", "222222")),
    ("admin3", "ADMIN 3", "ADMIN", os.getenv("ADMIN3_PIN", "333333")),
]

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn

def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def _add_column_if_missing(conn, table, column, ddl):
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );

    CREATE TABLE IF NOT EXISTS orders(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      no_po TEXT NOT NULL UNIQUE,
      tanggal TEXT NOT NULL,
      customer_id INTEGER NOT NULL,
      estimasi_hari INTEGER NOT NULL DEFAULT 21,
      target TEXT,
      status TEXT NOT NULL DEFAULT 'BELUM DP',
      catatan TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(customer_id) REFERENCES customers(id)
    );

    CREATE TABLE IF NOT EXISTS order_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id INTEGER NOT NULL,
      jenis_kain TEXT NOT NULL,
      warna TEXT NOT NULL,
      qty_kg REAL NOT NULL DEFAULT 0,
      qty_roll REAL NOT NULL DEFAULT 0,
      harga_per_kg REAL NOT NULL DEFAULT 0,
      FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id INTEGER NOT NULL,
      nominal REAL NOT NULL,
      created_at TEXT NOT NULL,
      deleted_at TEXT,
      deleted_by TEXT,
      restored_at TEXT,
      restored_by TEXT,
      FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS order_images(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      order_id INTEGER NOT NULL,
      filename TEXT NOT NULL,
      FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS app_meta(
      key TEXT PRIMARY KEY,
      value TEXT
    );

    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      display_name TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('SERVER','ADMIN')),
      pin_hash TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS audit_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      actor TEXT NOT NULL,
      role TEXT NOT NULL,
      action TEXT NOT NULL,
      entity_type TEXT,
      entity_id TEXT,
      detail TEXT,
      ip_address TEXT,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_payments_order_active
      ON payments(order_id, deleted_at);

    CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
      ON audit_logs(created_at DESC);
    """)

    # Migrasi database lama tanpa menghapus data.
    _add_column_if_missing(conn, "payments", "deleted_at", "deleted_at TEXT")
    _add_column_if_missing(conn, "payments", "deleted_by", "deleted_by TEXT")
    _add_column_if_missing(conn, "payments", "restored_at", "restored_at TEXT")
    _add_column_if_missing(conn, "payments", "restored_by", "restored_by TEXT")

    for username, display_name, role, pin in DEFAULT_USERS:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO users(username,display_name,role,pin_hash,active)
                   VALUES(?,?,?,?,1)""",
                (username, display_name, role, generate_password_hash(pin))
            )

    conn.commit()
    conn.close()
