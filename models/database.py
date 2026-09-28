import os
import sqlite3
from contextlib import contextmanager
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


@contextmanager
def db_transaction(immediate=True):
    """Transaksi database atomik: commit jika sukses, rollback jika gagal."""
    conn = get_db()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

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
      status TEXT NOT NULL DEFAULT 'PROSES',
      catatan TEXT DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      deleted_at TEXT,
      deleted_by TEXT,
      restored_at TEXT,
      restored_by TEXT,
      edit_count INTEGER NOT NULL DEFAULT 0,
      last_edited_at TEXT,
      last_edited_by TEXT,
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
      created_by_user_id INTEGER,
      created_by TEXT,
      created_role TEXT,
      deleted_at TEXT,
      deleted_by TEXT,
      delete_reason TEXT,
      FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS payment_correction_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      payment_id INTEGER NOT NULL,
      order_id INTEGER NOT NULL,
      requested_nominal REAL NOT NULL,
      reason TEXT NOT NULL,
      requested_by_user_id INTEGER,
      requested_by TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING','APPROVED','REJECTED')),
      resolved_by TEXT,
      resolution_note TEXT,
      created_at TEXT NOT NULL,
      resolved_at TEXT,
      FOREIGN KEY(payment_id) REFERENCES payments(id) ON DELETE CASCADE,
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

    CREATE INDEX IF NOT EXISTS idx_payment_correction_pending
      ON payment_correction_requests(status, created_at DESC);
    """)

    _add_column_if_missing(conn, "payments", "deleted_at", "deleted_at TEXT")
    _add_column_if_missing(conn, "payments", "deleted_by", "deleted_by TEXT")
    _add_column_if_missing(conn, "payments", "created_by_user_id", "created_by_user_id INTEGER")
    _add_column_if_missing(conn, "payments", "created_by", "created_by TEXT")
    _add_column_if_missing(conn, "payments", "created_role", "created_role TEXT")
    _add_column_if_missing(conn, "payments", "delete_reason", "delete_reason TEXT")
    _add_column_if_missing(conn, "orders", "deleted_at", "deleted_at TEXT")
    _add_column_if_missing(conn, "orders", "deleted_by", "deleted_by TEXT")
    _add_column_if_missing(conn, "orders", "restored_at", "restored_at TEXT")
    _add_column_if_missing(conn, "orders", "restored_by", "restored_by TEXT")
    _add_column_if_missing(conn, "orders", "edit_count", "edit_count INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "orders", "last_edited_at", "last_edited_at TEXT")
    _add_column_if_missing(conn, "orders", "last_edited_by", "last_edited_by TEXT")
    # Kolom current_pin dipertahankan hanya untuk kompatibilitas database lama,
    # tetapi nilainya wajib dikosongkan. PIN hanya disimpan dalam bentuk hash.
    _add_column_if_missing(conn, "users", "current_pin", "current_pin TEXT")
    conn.execute("UPDATE users SET current_pin=NULL WHERE current_pin IS NOT NULL")
    _add_column_if_missing(conn, "users", "active", "active INTEGER NOT NULL DEFAULT 1")

    # Migrasi tiga status lama yang dihapus dari workflow.
    conn.execute("UPDATE orders SET status='PROSES' WHERE status IN ('BELUM DP','READY')")
    conn.execute("UPDATE orders SET status='DONE' WHERE status='LUNAS'")


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