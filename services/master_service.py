
import json
from pathlib import Path
from models.database import get_db

BASE_DIR = Path(__file__).resolve().parents[1]
MASTER_PATH = BASE_DIR / "data" / "master.json"

def _read_master_file():
    if not MASTER_PATH.exists():
        return {"fabrics": [], "colors": [], "default_prices": []}
    return json.loads(MASTER_PATH.read_text(encoding="utf-8"))

def _write_master_file(data):
    MASTER_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def load_master():
    data = _read_master_file()
    conn = get_db()
    customer_rows = conn.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    conn.close()
    data["customers"] = [dict(r) for r in customer_rows]
    return data

def add_master_value(kind, value):
    value = (value or "").strip()
    if not value:
        raise ValueError("Nilai tidak boleh kosong.")

    if kind == "customers":
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)", (value,))
        conn.commit()
        conn.close()
        return

    field = {"fabrics": "fabrics", "colors": "colors"}.get(kind)
    if not field:
        raise ValueError("Jenis master tidak valid.")

    data = _read_master_file()
    values = data.get(field, [])
    if value.casefold() not in {str(v).casefold() for v in values}:
        values.append(value)
        values.sort(key=lambda x: str(x).casefold())
        data[field] = values
        _write_master_file(data)

def delete_master_value(kind, value=None, customer_id=None):
    if kind == "customers":
        if not customer_id:
            raise ValueError("Customer ID tidak ditemukan.")
        conn = get_db()
        used = conn.execute("SELECT COUNT(*) n FROM orders WHERE customer_id=?", (customer_id,)).fetchone()["n"]
        if used:
            conn.close()
            raise ValueError("Customer sudah dipakai pada order dan tidak dapat dihapus.")
        conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        conn.commit()
        conn.close()
        return

    field = {"fabrics": "fabrics", "colors": "colors"}.get(kind)
    if not field:
        raise ValueError("Jenis master tidak valid.")
    data = _read_master_file()
    target = (value or "").casefold()
    data[field] = [v for v in data.get(field, []) if str(v).casefold() != target]
    _write_master_file(data)

def customer_price_history(customer_name, fabric, color):
    conn = get_db()
    rows = conn.execute("""
      SELECT o.no_po, o.tanggal, oi.harga_per_kg
      FROM order_items oi
      JOIN orders o ON o.id=oi.order_id
      JOIN customers c ON c.id=o.customer_id
      WHERE lower(c.name)=lower(?)
        AND lower(oi.jenis_kain)=lower(?)
        AND lower(oi.warna)=lower(?)
      ORDER BY o.created_at DESC
      LIMIT 10
    """, (customer_name, fabric, color)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
