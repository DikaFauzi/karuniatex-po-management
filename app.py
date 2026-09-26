from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from pathlib import Path
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from models.database import get_db, init_db
from services.master_service import load_master, customer_price_history, add_master_value, delete_master_value
from services.excel_service import build_template, build_report, parse_import
import uuid, os, hashlib, json

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.getenv("SECRET_KEY", "karuniatex-local-change-this-secret")

JAKARTA_TZ = timezone(timedelta(hours=7))

def now_iso():
    return datetime.now(JAKARTA_TZ).isoformat(timespec="seconds")

def current_user():
    if not session.get("user_id"):
        return None
    return {
        "id": session.get("user_id"),
        "username": session.get("username"),
        "display_name": session.get("display_name"),
        "role": session.get("role"),
    }

def is_server():
    return session.get("role") == "SERVER"

def audit(action, entity_type=None, entity_id=None, detail=None):
    user = current_user() or {"display_name": "SYSTEM", "role": "SYSTEM"}
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO audit_logs(actor,role,action,entity_type,entity_id,detail,ip_address,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                user.get("display_name") or user.get("username") or "SYSTEM",
                user.get("role") or "SYSTEM",
                action,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else detail,
                request.remote_addr if request else None,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

def server_only(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_server():
            if request.path.startswith("/api/"):
                return jsonify(error="Akses hanya untuk SERVER"), 403
            return redirect(url_for("index"))
        return fn(*args, **kwargs)
    return wrapper

PUBLIC_ENDPOINTS = {"login", "health", "static"}

@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if not session.get("user_id"):
        if request.path.startswith("/api/") or request.path.startswith("/excel/"):
            return jsonify(error="Sesi berakhir. Silakan login kembali."), 401
        return redirect(url_for("login", next=request.path))
    return None

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def cleanup_duplicate_images():
    conn = get_db()
    order_ids = [r["id"] for r in conn.execute("SELECT id FROM orders").fetchall()]
    for oid in order_ids:
        rows = conn.execute(
            "SELECT id,filename FROM order_images WHERE order_id=? ORDER BY id", (oid,)
        ).fetchall()
        seen = {}
        for r in rows:
            p = UPLOAD_DIR / r["filename"]
            if not p.exists():
                conn.execute("DELETE FROM order_images WHERE id=?", (r["id"],))
                continue
            try:
                digest = file_sha256(p)
            except Exception:
                continue
            if digest in seen:
                conn.execute("DELETE FROM order_images WHERE id=?", (r["id"],))
                try:
                    p.unlink()
                except Exception:
                    pass
            else:
                seen[digest] = r["id"]
    conn.commit()
    conn.close()

def _parse_datetime_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s[:-1] + "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
    return None

@app.template_filter("dt_id")
def format_datetime_id(value):
    dt = _parse_datetime_value(value)
    if not dt:
        return str(value or "-")
    if dt.tzinfo is not None:
        dt = dt.astimezone(JAKARTA_TZ)
    return dt.strftime("%d/%m/%Y %H:%M:%S")

@app.template_filter("date_id")
def format_date_id(value):
    if not value:
        return "-"
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s[:10]).strftime("%d/%m/%Y")
    except Exception:
        return s

def next_po(conn):
    d = datetime.now(JAKARTA_TZ).strftime("%y%m%d")
    row = conn.execute("SELECT value FROM app_meta WHERE key='po_seq'").fetchone()
    seq = int(row["value"]) + 1 if row else 1
    conn.execute(
        """INSERT INTO app_meta(key,value) VALUES('po_seq',?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (str(seq),),
    )
    return f"PO-{d}-{seq:03d}"

def order_dict(conn, row):
    items = [
        dict(x)
        for x in conn.execute(
            "SELECT * FROM order_items WHERE order_id=? ORDER BY id", (row["id"],)
        ).fetchall()
    ]
    pays = [
        dict(x)
        for x in conn.execute(
            """SELECT * FROM payments
               WHERE order_id=? AND deleted_at IS NULL
               ORDER BY id""",
            (row["id"],),
        ).fetchall()
    ]
    imgs = [
        dict(x)
        for x in conn.execute(
            "SELECT * FROM order_images WHERE order_id=? ORDER BY id", (row["id"],)
        ).fetchall()
    ]
    total = sum(float(i["qty_kg"]) * float(i["harga_per_kg"]) for i in items)
    total_dp = sum(float(p["nominal"]) for p in pays)
    kg = sum(float(i["qty_kg"]) for i in items)
    rolls = sum(float(i["qty_roll"]) for i in items)
    d = dict(row)
    d.update(
        items=items,
        payments=pays,
        images=imgs,
        total=total,
        total_dp=total_dp,
        sisa=max(total - total_dp, 0),
        total_qty_kg=kg,
        total_roll=rolls,
        dp_terakhir=(pays[-1]["nominal"] if pays else 0),
    )
    return d

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        pin = (request.form.get("pin") or "").strip()
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM users WHERE lower(username)=lower(?) AND active=1", (username,)
        ).fetchone()
        conn.close()
        if row and check_password_hash(row["pin_hash"], pin):
            session.clear()
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            session["display_name"] = row["display_name"]
            session["role"] = row["role"]
            session.permanent = False
            audit("LOGIN", "user", row["id"], {"username": row["username"]})
            return redirect(request.args.get("next") or url_for("index"))
        error = "Username atau PIN salah."
    return render_template("login.html", error=error)

@app.post("/logout")
def logout():
    if session.get("user_id"):
        audit("LOGOUT", "user", session.get("user_id"))
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
def index():
    return render_template("index.html", user=current_user(), is_server=is_server())

@app.get("/health")
def health():
    return jsonify(ok=True, version="PRODUCTION V7 ACCESS+AUDIT", port=5050)

@app.get("/api/me")
def api_me():
    return jsonify(current_user())

@app.get("/database")
@server_only
def database_page():
    return render_template("database.html", user=current_user())

@app.get("/logs")
@server_only
def logs_page():
    return render_template("logs.html", user=current_user())

@app.get("/restore-dp")
@server_only
def restore_dp_page():
    return render_template("restore_dp.html", user=current_user())

@app.get("/users")
@server_only
def users_page():
    return render_template("users.html", user=current_user())

@app.get("/api/users")
@server_only
def api_users():
    conn = get_db()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id,username,display_name,role,active,created_at,updated_at FROM users ORDER BY id"
        ).fetchall()
    ]
    conn.close()
    return jsonify(rows)

@app.put("/api/users/<int:user_id>/pin")
@server_only
def change_user_pin(user_id):
    data = request.get_json(force=True)
    pin = str(data.get("pin") or "").strip()
    if not (pin.isdigit() and len(pin) == 6):
        return jsonify(error="PIN harus tepat 6 digit angka"), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="User tidak ditemukan"), 404
    conn.execute(
        "UPDATE users SET pin_hash=?,updated_at=? WHERE id=?",
        (generate_password_hash(pin), now_iso(), user_id),
    )
    conn.commit()
    conn.close()
    audit("CHANGE_PIN", "user", user_id, {"target": row["display_name"]})
    return jsonify(ok=True)

@app.get("/api/logs")
@server_only
def api_logs():
    limit = min(max(request.args.get("limit", 200, type=int), 1), 1000)
    conn = get_db()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    ]
    conn.close()
    return jsonify(rows)

@app.get("/api/deleted-payments")
@server_only
def deleted_payments():
    conn = get_db()
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT p.id,p.order_id,p.nominal,p.created_at,p.deleted_at,p.deleted_by,
                      o.no_po,c.name customer
               FROM payments p
               JOIN orders o ON o.id=p.order_id
               JOIN customers c ON c.id=o.customer_id
               WHERE p.deleted_at IS NOT NULL
               ORDER BY p.deleted_at DESC"""
        ).fetchall()
    ]
    conn.close()
    return jsonify(rows)

@app.post("/api/payments/<int:payment_id>/restore")
@server_only
def restore_payment(payment_id):
    conn = get_db()
    row = conn.execute(
        """SELECT p.*,o.no_po FROM payments p
           JOIN orders o ON o.id=p.order_id
           WHERE p.id=?""",
        (payment_id,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify(error="DP tidak ditemukan"), 404
    if row["deleted_at"] is None:
        conn.close()
        return jsonify(error="DP ini tidak sedang terhapus"), 400
    conn.execute(
        """UPDATE payments
           SET deleted_at=NULL,deleted_by=NULL,restored_at=?,restored_by=?
           WHERE id=?""",
        (now_iso(), current_user()["display_name"], payment_id),
    )
    conn.commit()
    conn.close()
    audit(
        "RESTORE_DP",
        "payment",
        payment_id,
        {"no_po": row["no_po"], "nominal": row["nominal"]},
    )
    return jsonify(ok=True)

@app.get("/api/master")
def api_master():
    return jsonify(load_master())

@app.post("/api/master/<kind>")
@server_only
def api_master_add(kind):
    data = request.get_json(force=True)
    try:
        value = data.get("value", "")
        add_master_value(kind, value)
        audit("MASTER_ADD", kind, None, {"value": value})
        return jsonify(ok=True, master=load_master())
    except ValueError as e:
        return jsonify(error=str(e)), 400

@app.delete("/api/master/<kind>")
@server_only
def api_master_delete(kind):
    try:
        value = request.args.get("value")
        customer_id = request.args.get("customer_id", type=int)
        delete_master_value(kind, value=value, customer_id=customer_id)
        audit("MASTER_DELETE", kind, customer_id or value, {"value": value})
        return jsonify(ok=True, master=load_master())
    except ValueError as e:
        return jsonify(error=str(e)), 400

@app.get("/api/orders")
def api_orders():
    conn = get_db()
    rows = conn.execute(
        """SELECT o.*, c.name customer
           FROM orders o JOIN customers c ON c.id=o.customer_id
           ORDER BY o.id DESC"""
    ).fetchall()
    data = [order_dict(conn, r) for r in rows]
    conn.close()
    return jsonify(data)

@app.post("/api/orders")
def create_order():
    data = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    customer = (data.get("customer") or "").strip()
    if not customer:
        conn.close()
        return jsonify(error="Customer wajib diisi"), 400
    cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)", (customer,))
    cid = cur.execute(
        "SELECT id FROM customers WHERE lower(name)=lower(?)", (customer,)
    ).fetchone()["id"]
    no_po = next_po(conn)
    tanggal = data.get("tanggal") or datetime.now(JAKARTA_TZ).date().isoformat()
    estimasi = int(data.get("estimasi_hari") or 21)
    target = (datetime.fromisoformat(tanggal).date() + timedelta(days=estimasi)).isoformat()
    cur.execute(
        """INSERT INTO orders(no_po,tanggal,customer_id,estimasi_hari,target,status,catatan,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            no_po,
            tanggal,
            cid,
            estimasi,
            target,
            data.get("status") or "BELUM DP",
            data.get("catatan") or "",
            now_iso(),
            now_iso(),
        ),
    )
    oid = cur.lastrowid
    for it in data.get("items", []):
        kg = float(it.get("qty_kg") or 0)
        rolls = float(it.get("qty_roll") or (kg / 25 if kg else 0))
        cur.execute(
            """INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg)
               VALUES(?,?,?,?,?,?)""",
            (
                oid,
                it.get("jenis_kain", ""),
                it.get("warna", ""),
                kg,
                rolls,
                float(it.get("harga_per_kg") or 0),
            ),
        )
    for p in data.get("payments", []):
        cur.execute(
            "INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",
            (oid, float(p.get("nominal") or 0), p.get("created_at") or now_iso()),
        )
    conn.commit()
    row = conn.execute(
        """SELECT o.*, c.name customer FROM orders o
           JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",
        (oid,),
    ).fetchone()
    result = order_dict(conn, row)
    conn.close()
    audit("CREATE_PO", "order", oid, {"no_po": no_po, "customer": customer})
    return jsonify(result), 201

@app.put("/api/orders/<int:oid>")
def update_order(oid):
    data = request.get_json(force=True)
    conn = get_db()
    cur = conn.cursor()
    old = cur.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not old:
        conn.close()
        return jsonify(error="PO tidak ditemukan"), 404

    customer = (data.get("customer") or "").strip()
    if not customer:
        conn.close()
        return jsonify(error="Customer wajib diisi"), 400

    cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)", (customer,))
    cid = cur.execute(
        "SELECT id FROM customers WHERE lower(name)=lower(?)", (customer,)
    ).fetchone()["id"]
    tanggal = data.get("tanggal") or old["tanggal"]
    estimasi = int(data.get("estimasi_hari") or old["estimasi_hari"])
    target = (datetime.fromisoformat(tanggal).date() + timedelta(days=estimasi)).isoformat()
    cur.execute(
        """UPDATE orders SET tanggal=?,customer_id=?,estimasi_hari=?,target=?,status=?,catatan=?,updated_at=?
           WHERE id=?""",
        (
            tanggal,
            cid,
            estimasi,
            target,
            data.get("status") or old["status"],
            data.get("catatan") or "",
            now_iso(),
            oid,
        ),
    )

    cur.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    for it in data.get("items", []):
        kg = float(it.get("qty_kg") or 0)
        rolls = float(it.get("qty_roll") or (kg / 25 if kg else 0))
        cur.execute(
            """INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg)
               VALUES(?,?,?,?,?,?)""",
            (
                oid,
                it.get("jenis_kain", ""),
                it.get("warna", ""),
                kg,
                rolls,
                float(it.get("harga_per_kg") or 0),
            ),
        )

    active_rows = cur.execute(
        "SELECT * FROM payments WHERE order_id=? AND deleted_at IS NULL", (oid,)
    ).fetchall()
    active_ids = {r["id"] for r in active_rows}
    submitted_existing_ids = {
        int(p["id"]) for p in data.get("payments", []) if p.get("id")
    }

    deleted_ids = active_ids - submitted_existing_ids
    for payment_id in deleted_ids:
        pay = next((r for r in active_rows if r["id"] == payment_id), None)
        cur.execute(
            """UPDATE payments SET deleted_at=?,deleted_by=?
               WHERE id=? AND order_id=? AND deleted_at IS NULL""",
            (now_iso(), current_user()["display_name"], payment_id, oid),
        )
        if pay:
            conn.commit()
            audit(
                "DELETE_DP",
                "payment",
                payment_id,
                {"order_id": oid, "nominal": pay["nominal"]},
            )

    for p in data.get("payments", []):
        if p.get("id"):
            cur.execute(
                """UPDATE payments SET nominal=?,created_at=?
                   WHERE id=? AND order_id=? AND deleted_at IS NULL""",
                (
                    float(p.get("nominal") or 0),
                    p.get("created_at") or now_iso(),
                    int(p["id"]),
                    oid,
                ),
            )
        else:
            cur.execute(
                "INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",
                (oid, float(p.get("nominal") or 0), p.get("created_at") or now_iso()),
            )
            new_pid = cur.lastrowid
            conn.commit()
            audit(
                "ADD_DP",
                "payment",
                new_pid,
                {"order_id": oid, "nominal": float(p.get("nominal") or 0)},
            )

    conn.commit()
    row = conn.execute(
        """SELECT o.*, c.name customer FROM orders o
           JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",
        (oid,),
    ).fetchone()
    result = order_dict(conn, row)
    no_po = row["no_po"]
    conn.close()
    audit("UPDATE_PO", "order", oid, {"no_po": no_po, "customer": customer})
    return jsonify(result)

@app.delete("/api/orders/<int:oid>")
@server_only
def delete_order(oid):
    conn = get_db()
    order = conn.execute("SELECT no_po FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify(error="PO tidak ditemukan"), 404
    imgs = conn.execute(
        "SELECT filename FROM order_images WHERE order_id=?", (oid,)
    ).fetchall()
    no_po = order["no_po"]
    conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    conn.commit()
    conn.close()
    for i in imgs:
        p = UPLOAD_DIR / i["filename"]
        if p.exists():
            p.unlink()
    audit("DELETE_PO", "order", oid, {"no_po": no_po})
    return jsonify(ok=True)

@app.post("/api/orders/<int:oid>/images")
def upload_images(oid):
    conn = get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=?", (oid,)).fetchone():
        conn.close()
        return jsonify(error="PO tidak ditemukan"), 404

    existing_rows = conn.execute(
        "SELECT filename FROM order_images WHERE order_id=?", (oid,)
    ).fetchall()
    existing_hashes = set()
    for r in existing_rows:
        p = UPLOAD_DIR / r["filename"]
        if p.exists():
            try:
                existing_hashes.add(file_sha256(p))
            except Exception:
                pass

    added = []
    skipped = 0
    for f in request.files.getlist("images"):
        if not f.filename:
            continue
        ext = Path(secure_filename(f.filename)).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        temp_name = f"_tmp_{uuid.uuid4().hex}{ext}"
        temp_path = UPLOAD_DIR / temp_name
        f.save(temp_path)
        digest = file_sha256(temp_path)
        if digest in existing_hashes:
            skipped += 1
            temp_path.unlink(missing_ok=True)
            continue
        name = f"{uuid.uuid4().hex}{ext}"
        final_path = UPLOAD_DIR / name
        temp_path.replace(final_path)
        conn.execute(
            "INSERT INTO order_images(order_id,filename) VALUES(?,?)", (oid, name)
        )
        existing_hashes.add(digest)
        added.append(name)

    conn.commit()
    conn.close()
    if added:
        audit("UPLOAD_IMAGE", "order", oid, {"count": len(added)})
    return jsonify(added=added, skipped_duplicates=skipped)

@app.delete("/api/images/<int:image_id>")
def delete_image(image_id):
    conn = get_db()
    row = conn.execute(
        "SELECT order_id,filename FROM order_images WHERE id=?", (image_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM order_images WHERE id=?", (image_id,))
        conn.commit()
        p = UPLOAD_DIR / row["filename"]
        if p.exists():
            p.unlink()
        oid = row["order_id"]
    else:
        oid = None
    conn.close()
    if row:
        audit("DELETE_IMAGE", "image", image_id, {"order_id": oid})
    return jsonify(ok=True)

@app.get("/api/history-price")
def history_price():
    return jsonify(
        customer_price_history(
            request.args.get("customer", ""),
            request.args.get("fabric", ""),
            request.args.get("color", ""),
        )
    )

@app.get("/print/dp/<int:oid>")
def print_dp(oid):
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT o.*, c.name customer
               FROM orders o JOIN customers c ON c.id=o.customer_id
               WHERE o.id=?""",
            (oid,),
        ).fetchone()
        if not row:
            return "PO tidak ditemukan", 404
        order = order_dict(conn, row)
        return render_template("print_dp.html", order=order)
    finally:
        conn.close()

@app.get("/print/receipt/<int:oid>")
def print_receipt(oid):
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT o.*, c.name customer
               FROM orders o JOIN customers c ON c.id=o.customer_id
               WHERE o.id=?""",
            (oid,),
        ).fetchone()
        if not row:
            return "PO tidak ditemukan", 404
        order = order_dict(conn, row)
        return render_template("print_receipt.html", order=order)
    finally:
        conn.close()

@app.get("/excel/template")
def excel_template():
    return send_file(
        build_template(),
        as_attachment=True,
        download_name="TEMPLATE-IMPORT-PO-AN-OTS.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@app.get("/excel/report")
def excel_report():
    conn = get_db()
    rows = conn.execute(
        """SELECT o.*, c.name customer FROM orders o
           JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC"""
    ).fetchall()
    orders = [order_dict(conn, r) for r in rows]
    conn.close()
    audit("EXPORT_EXCEL", "report", None, {"orders": len(orders)})
    return send_file(
        build_report(orders),
        as_attachment=True,
        download_name=f"LAPORAN-PO-KESELURUHAN-{datetime.now(JAKARTA_TZ).date()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@app.post("/excel/import")
@server_only
def excel_import():
    if "file" not in request.files:
        return jsonify(error="File tidak ada"), 400
    try:
        data = parse_import(request.files["file"])
    except Exception as e:
        return jsonify(error=str(e)), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM orders")
    order_map = {}
    for r in data["orders"]:
        no_po = str(r.get("NO_PO") or "").strip()
        if not no_po:
            continue
        customer = str(r.get("CUSTOMER") or "").strip()
        cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)", (customer,))
        cid = cur.execute(
            "SELECT id FROM customers WHERE lower(name)=lower(?)", (customer,)
        ).fetchone()["id"]
        tanggal = str(r.get("TANGGAL") or datetime.now(JAKARTA_TZ).date().isoformat())
        if "/" in tanggal:
            try:
                tanggal = datetime.strptime(tanggal, "%d/%m/%Y").date().isoformat()
            except Exception:
                pass
        est = int(r.get("ESTIMASI_HARI") or 21)
        target = (datetime.fromisoformat(tanggal).date() + timedelta(days=est)).isoformat()
        cur.execute(
            """INSERT INTO orders(no_po,tanggal,customer_id,estimasi_hari,target,status,catatan,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                no_po,
                tanggal,
                cid,
                est,
                target,
                str(r.get("STATUS") or "BELUM DP"),
                str(r.get("CATATAN") or ""),
                now_iso(),
                now_iso(),
            ),
        )
        order_map[no_po] = cur.lastrowid
    for r in data["items"]:
        no_po = str(r.get("NO_PO") or "").strip()
        if no_po not in order_map:
            continue
        kg = float(r.get("QTY_KG") or 0)
        rolls = float(r.get("QTY_ROLL") or (kg / 25 if kg else 0))
        cur.execute(
            """INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg)
               VALUES(?,?,?,?,?,?)""",
            (
                order_map[no_po],
                str(r.get("JENIS_KAIN") or ""),
                str(r.get("WARNA") or ""),
                kg,
                rolls,
                float(r.get("HARGA_PER_KG") or 0),
            ),
        )
    for r in data["payments"]:
        no_po = str(r.get("NO_PO") or "").strip()
        if no_po not in order_map:
            continue
        dt = str(r.get("TANGGAL_JAM_DP") or now_iso())
        cur.execute(
            "INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",
            (order_map[no_po], float(r.get("NOMINAL_DP") or 0), dt),
        )
    nums = []
    for k in order_map:
        try:
            nums.append(int(k.split("-")[-1]))
        except Exception:
            pass
    if nums:
        cur.execute(
            """INSERT INTO app_meta(key,value) VALUES('po_seq',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(max(nums)),),
        )
    conn.commit()
    conn.close()
    audit("IMPORT_EXCEL", "report", None, {"orders": len(order_map)})
    return jsonify(imported=len(order_map))

if __name__ == "__main__":
    init_db()
    cleanup_duplicate_images()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
