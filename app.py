from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from pathlib import Path
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from models.database import get_db, init_db, db_transaction
from services.master_service import load_master, customer_price_history, add_master_value, delete_master_value
from services.excel_service import build_report
from services.backup_service import start_backup_scheduler, create_full_backup
from services.printer_service import print_receipt as print_receipt_thermal, get_target_printer, get_default_printer, list_printers, PrinterError
import uuid, os, hashlib, json, secrets, string, re

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.getenv("SECRET_KEY", "karuniatex-local-change-this-secret")
JAKARTA_TZ = timezone(timedelta(hours=7))
ORDER_STATUSES = {"DP MASUK", "PROSES", "DONE", "CANCEL"}

def now_iso():
    return datetime.now(JAKARTA_TZ).isoformat(timespec="seconds")

def current_user():
    if not session.get("user_id"): return None
    return {"id":session.get("user_id"),"username":session.get("username"),
            "display_name":session.get("display_name"),"role":session.get("role")}

def is_server():
    return session.get("role") == "SERVER"

def audit(action, entity_type=None, entity_id=None, detail=None):
    user = current_user() or {"display_name":"SYSTEM","role":"SYSTEM"}
    conn=get_db()
    conn.execute("""INSERT INTO audit_logs(actor,role,action,entity_type,entity_id,detail,ip_address,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (user.get("display_name") or "SYSTEM", user.get("role") or "SYSTEM",
                  action, entity_type, str(entity_id) if entity_id is not None else None,
                  json.dumps(detail,ensure_ascii=False) if isinstance(detail,(dict,list)) else detail,
                  request.remote_addr if request else None, now_iso()))
    conn.commit(); conn.close()

def server_only(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        if not is_server():
            if request.path.startswith("/api/") or request.path.startswith("/excel/"):
                return jsonify(error="Akses hanya untuk SERVER"),403
            return redirect(url_for("index"))
        return fn(*args,**kwargs)
    return wrapper

PUBLIC_ENDPOINTS={"login","health","static"}

# Pastikan schema siap, lalu mulai backup otomatis.
init_db()
start_backup_scheduler(interval_hours=6, retention_days=14)


def _simple_order_snapshot(order):
    return {
        "customer": order.get("customer"),
        "status": order.get("status"),
        "tanggal": order.get("tanggal"),
        "estimasi_hari": order.get("estimasi_hari"),
        "catatan": order.get("catatan"),
        "items": [
            {
                "id": x.get("id"),
                "jenis_kain": x.get("jenis_kain"),
                "warna": x.get("warna"),
                "qty_kg": float(x.get("qty_kg") or 0),
                "qty_roll": float(x.get("qty_roll") or 0),
                "harga_per_kg": float(x.get("harga_per_kg") or 0),
            } for x in order.get("items",[])
        ],
        "payments": [
            {
                "id": x.get("id"),
                "nominal": float(x.get("nominal") or 0),
                "created_at": x.get("created_at"),
            } for x in order.get("payments",[])
        ],
    }

def _fmt_history_value(field, value):
    if field == "harga_per_kg":
        try:
            return f"Rp{float(value or 0):,.0f}".replace(",", ".")
        except Exception:
            return str(value or 0)
    if field in {"qty_kg", "qty_roll"}:
        try:
            n=float(value or 0)
            return f"{n:g}"
        except Exception:
            return str(value or 0)
    return str(value or "-")

def _build_change_list(before, after):
    changes=[]
    for key,label in [
        ("customer","Customer"),
        ("status","Status"),
        ("tanggal","Tanggal"),
        ("estimasi_hari","Estimasi"),
        ("catatan","Catatan"),
    ]:
        b=before.get(key)
        a=after.get(key)
        if str(b or "") != str(a or ""):
            changes.append({"field":label,"before":b,"after":a})

    before_items=before.get("items") or []
    after_items=after.get("items") or []
    before_by_id={int(x["id"]):x for x in before_items if x.get("id") is not None}
    after_by_id={int(x["id"]):x for x in after_items if x.get("id") is not None}

    labels={
        "jenis_kain":"Jenis Kain",
        "warna":"Warna",
        "qty_kg":"Qty KG",
        "qty_roll":"Qty Roll",
        "harga_per_kg":"Harga/KG",
    }

    for idx,item in enumerate(after_items, start=1):
        iid=item.get("id")
        old=before_by_id.get(int(iid)) if iid is not None else None
        if old is None:
            desc=f"{item.get('jenis_kain') or '-'} • {item.get('warna') or '-'} • {_fmt_history_value('qty_kg',item.get('qty_kg'))} KG • {_fmt_history_value('harga_per_kg',item.get('harga_per_kg'))}/KG"
            changes.append({"field":f"Item {idx} Ditambahkan","before":"-","after":desc})
            continue
        for field,label in labels.items():
            b=old.get(field)
            a=item.get(field)
            if field in {"qty_kg","qty_roll","harga_per_kg"}:
                try:
                    same=abs(float(b or 0)-float(a or 0)) < 0.000001
                except Exception:
                    same=str(b or "") == str(a or "")
            else:
                same=str(b or "") == str(a or "")
            if not same:
                changes.append({
                    "field":f"Item {idx} • {label}",
                    "before":_fmt_history_value(field,b),
                    "after":_fmt_history_value(field,a),
                })

    after_ids=set(after_by_id)
    for idx,item in enumerate(before_items, start=1):
        iid=item.get("id")
        if iid is not None and int(iid) not in after_ids:
            desc=f"{item.get('jenis_kain') or '-'} • {item.get('warna') or '-'} • {_fmt_history_value('qty_kg',item.get('qty_kg'))} KG • {_fmt_history_value('harga_per_kg',item.get('harga_per_kg'))}/KG"
            changes.append({"field":f"Item Dihapus","before":desc,"after":"-"})

    before_pays={int(x["id"]):x for x in (before.get("payments") or []) if x.get("id") is not None}
    after_pays={int(x["id"]):x for x in (after.get("payments") or []) if x.get("id") is not None}
    for pid,p in after_pays.items():
        old=before_pays.get(pid)
        if old and abs(float(old.get("nominal") or 0)-float(p.get("nominal") or 0)) > 0.01:
            changes.append({
                "field":"Pembayaran DP",
                "before":_fmt_history_value("harga_per_kg",old.get("nominal")),
                "after":_fmt_history_value("harga_per_kg",p.get("nominal")),
            })
    if len(before.get("payments") or []) != len(after.get("payments") or []):
        changes.append({
            "field":"Jumlah Pembayaran DP",
            "before":f"{len(before.get('payments') or [])} pembayaran",
            "after":f"{len(after.get('payments') or [])} pembayaran"
        })
    return changes


def admin_order_locked(conn, oid):
    if is_server():
        return False
    row=conn.execute("SELECT status FROM orders WHERE id=?",(oid,)).fetchone()
    return bool(row and row["status"]=="DONE")


MAX_QTY_KG=10000
MAX_QTY_ROLL=10000
MAX_HARGA_PER_KG=150000

def validate_item_limits(items):
    for idx,it in enumerate(items or [], start=1):
        try:
            kg=float(it.get("qty_kg") or 0)
            roll=float(it.get("qty_roll") or 0)
            harga=_normalize_price(it.get("harga_per_kg") or 0)
        except Exception:
            return f"Item {idx}: QTY KG, QTY Roll, atau harga tidak valid."

        if kg < 0:
            return f"Item {idx}: QTY KG tidak boleh negatif."
        if kg > MAX_QTY_KG:
            return f"Item {idx}: QTY KG maksimal 10.000 KG."

        if roll < 0:
            return f"Item {idx}: QTY Roll tidak boleh negatif."
        if roll > MAX_QTY_ROLL:
            return f"Item {idx}: QTY Roll maksimal 10.000 Roll."

        if harga < 0:
            return f"Item {idx}: Harga/KG tidak boleh negatif."
        if harga > MAX_HARGA_PER_KG:
            return f"Item {idx}: Harga/KG maksimal Rp150.000."
    return None


def calc_items_total(items):
    total=0.0
    for it in items or []:
        try:
            kg=float(it.get("qty_kg") or 0)
            harga=_normalize_price(it.get("harga_per_kg") or 0)
        except Exception:
            continue
        total += kg * harga
    return total


def validate_payments_total(items, payments):
    total=calc_items_total(items)
    total_dp=0.0

    for idx,p in enumerate(payments or [], start=1):
        try:
            nominal=float(p.get("nominal") or 0)
        except Exception:
            return f"DP ke-{idx}: nominal tidak valid."

        if nominal < 0:
            return f"DP ke-{idx}: nominal tidak boleh negatif."

        total_dp += nominal

    # Toleransi sangat kecil untuk menghindari masalah floating point.
    if total_dp > total + 0.01:
        return (
            f"Total DP tidak boleh melebihi total pesanan. "
            f"Total pesanan Rp{total:,.0f}, total DP Rp{total_dp:,.0f}."
        )

    return None


def payment_actor_fields():
    u=current_user() or {}
    return (
        u.get("id"),
        user_identity(u),
        (u.get("role") or "SYSTEM").upper()
    )

def payment_total_for_order(conn, oid, exclude_payment_id=None):
    sql="""SELECT COALESCE(SUM(nominal),0) total
           FROM payments WHERE order_id=? AND deleted_at IS NULL"""
    params=[oid]
    if exclude_payment_id is not None:
        sql += " AND id<>?"
        params.append(exclude_payment_id)
    row=conn.execute(sql,tuple(params)).fetchone()
    return float(row["total"] or 0)

def order_items_total_db(conn, oid):
    row=conn.execute(
        """SELECT COALESCE(SUM(qty_kg*harga_per_kg),0) total
           FROM order_items WHERE order_id=?""",
        (oid,)
    ).fetchone()
    return float(row["total"] or 0)

def user_identity(user=None):
    u=user or current_user()
    if not u:
        return "-"
    role=(u.get("role") or u.get("username") or "USER").upper()
    name=(u.get("display_name") or "").strip()
    if name and name.upper()!=role:
        return f"{role} ({name.upper()})"
    return role

@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None: return None
    if not session.get("user_id"):
        if request.path.startswith("/api/") or request.path.startswith("/excel/"):
            return jsonify(error="Sesi berakhir. Silakan login kembali."),401
        return redirect(url_for("login",next=request.path))



def file_sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def cleanup_duplicate_images():
    conn=get_db()
    for oid in [r["id"] for r in conn.execute("SELECT id FROM orders").fetchall()]:
        rows=conn.execute("SELECT id,filename FROM order_images WHERE order_id=? ORDER BY id",(oid,)).fetchall()
        seen={}
        for r in rows:
            p=UPLOAD_DIR/r["filename"]
            if not p.exists():
                conn.execute("DELETE FROM order_images WHERE id=?",(r["id"],)); continue
            try: digest=file_sha256(p)
            except Exception: continue
            if digest in seen:
                conn.execute("DELETE FROM order_images WHERE id=?",(r["id"],))
                try: p.unlink()
                except Exception: pass
            else: seen[digest]=r["id"]
    conn.commit(); conn.close()

def _parse_datetime_value(value):
    if value is None:return None
    if isinstance(value,datetime):return value
    s=str(value).strip()
    if not s:return None
    try:
        if s.endswith("Z"): return datetime.fromisoformat(s[:-1]+"+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S"):
            try:return datetime.strptime(s,fmt)
            except ValueError:pass
    return None

@app.template_filter("dt_id")
def format_datetime_id(value):
    dt=_parse_datetime_value(value)
    if not dt:return str(value or "-")
    if dt.tzinfo is not None:dt=dt.astimezone(JAKARTA_TZ)
    return dt.strftime("%d/%m/%Y %H:%M:%S")

@app.template_filter("date_id")
def format_date_id(value):
    if not value:return "-"
    s=str(value).strip()
    try:return datetime.fromisoformat(s[:10]).strftime("%d/%m/%Y")
    except Exception:return s

@app.template_filter("time_id")
def format_time_id(value):
    dt=_parse_datetime_value(value)
    if not dt:return "-"
    if dt.tzinfo is not None:dt=dt.astimezone(JAKARTA_TZ)
    return dt.strftime("%H:%M:%S")

def _normalize_price(value):
    """Simpan harga sebagai Rupiah bulat persis sesuai input pengguna.

    Tidak ada pembulatan otomatis ke kelipatan Rp1.000.
    """
    try:
        price = float(value or 0)
    except Exception:
        return 0.0
    if price < 0:
        price = 0.0
    return float(int(round(price)))

def next_po(conn):
    """Generate sequential PO numbers in the OTS format for NEW orders only.

    Format: PO-OTS-00001, PO-OTS-00002, ...
    The sequence is based on the highest OTS PO number ever stored, including
    soft-deleted records, so a deleted number is never reused. Legacy PO codes
    are left untouched and do not affect the new OTS sequence.
    """
    row = conn.execute(
        """
        SELECT MAX(CAST(SUBSTR(no_po, 8) AS INTEGER)) AS max_no
        FROM orders
        WHERE no_po GLOB 'PO-OTS-[0-9][0-9][0-9][0-9][0-9]'
        """
    ).fetchone()
    next_number = int((row["max_no"] if row else 0) or 0) + 1

    # Extra collision guard. Normally one pass is enough because max_no comes
    # from the same database, but this also protects manually imported data.
    while True:
        code = f"PO-OTS-{next_number:05d}"
        exists = conn.execute(
            "SELECT 1 FROM orders WHERE no_po=? LIMIT 1", (code,)
        ).fetchone()
        if not exists:
            return code
        next_number += 1

def _payment_admin_undo_meta(payment):
    if is_server():
        return False,0
    uid=session.get("user_id")
    if not uid or payment.get("created_by_user_id") != uid:
        return False,0
    dt=_parse_datetime_value(payment.get("created_at"))
    if not dt:
        return False,0
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=JAKARTA_TZ)
    elapsed=(datetime.now(JAKARTA_TZ)-dt.astimezone(JAKARTA_TZ)).total_seconds()
    left=max(0,int(30-elapsed))
    return left>0,left

def order_dict(conn,row):
    items=[dict(x) for x in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id",(row["id"],)).fetchall()]
    pays=[dict(x) for x in conn.execute("""SELECT * FROM payments WHERE order_id=? AND deleted_at IS NULL ORDER BY id""",(row["id"],)).fetchall()]
    for p in pays:
        can_undo,seconds_left=_payment_admin_undo_meta(p)
        p["can_undo"]=can_undo
        p["undo_seconds_left"]=seconds_left
    imgs=[dict(x) for x in conn.execute("SELECT * FROM order_images WHERE order_id=? ORDER BY id",(row["id"],)).fetchall()]
    total=sum(float(i["qty_kg"])*float(i["harga_per_kg"]) for i in items)
    total_dp=sum(float(p["nominal"]) for p in pays)
    d=dict(row); d.update(items=items,payments=pays,images=imgs,total=total,total_dp=total_dp,
        sisa=max(total-total_dp,0),total_qty_kg=sum(float(i["qty_kg"]) for i in items),
        total_roll=sum(float(i["qty_roll"]) for i in items),dp_terakhir=(pays[-1]["nominal"] if pays else 0))
    return d

@app.route("/login",methods=["GET","POST"])
def login():
    if session.get("user_id"): return redirect(url_for("index"))
    error=None
    if request.method=="POST":
        username=(request.form.get("username") or "").strip().lower()
        pin=(request.form.get("pin") or "").strip()
        conn=get_db()
        row=conn.execute("SELECT * FROM users WHERE lower(username)=lower(?) AND active=1 AND active=1",(username,)).fetchone()
        conn.close()
        if row and check_password_hash(row["pin_hash"],pin):
            session.clear()
            session.update(user_id=row["id"],username=row["username"],display_name=row["display_name"],role=row["role"])
            audit("LOGIN","user",row["id"],{"username":row["username"]})
            return redirect(request.args.get("next") or url_for("index"))
        error="Username atau PIN salah."
    return render_template("login.html",error=error)

@app.post("/logout")
def logout():
    if session.get("user_id"):audit("LOGOUT","user",session.get("user_id"))
    session.clear(); return redirect(url_for("login"))

@app.get("/")
def index():
    return render_template("index.html",user=current_user(),is_server=is_server())

@app.get("/health")
def health():
    return jsonify(ok=True,version="LOCAL V7.4 RESTORE PO+EXPORT",port=5050)

@app.get("/api/me")
def api_me(): return jsonify(current_user())

@app.get("/database")
@server_only
def database_page(): return render_template("database.html",user=current_user())

@app.get("/logs")
@server_only
def logs_page(): return render_template("logs.html",user=current_user())

@app.get("/restore-po")
@server_only
def restore_po_page(): return render_template("restore_po.html",user=current_user())

@app.get("/users")
@server_only
def users_page(): return render_template("users.html",user=current_user())

@app.get("/api/users")
@server_only
def api_users():
    conn=get_db()
    rows=[dict(r) for r in conn.execute("SELECT id,username,display_name,role,active,created_at,updated_at FROM users ORDER BY id").fetchall()]
    conn.close(); return jsonify(rows)



@app.post("/api/users")
@server_only
def create_admin_user():
    data=request.get_json(force=True)
    username=(data.get("username") or "").strip().lower()
    display_name=(data.get("display_name") or "").strip()
    pin=(data.get("pin") or "").strip()

    if not username:
        return jsonify(error="Username wajib diisi."),400
    if not re.fullmatch(r"[a-z0-9._-]{3,30}",username):
        return jsonify(error="Username 3-30 karakter: huruf kecil, angka, titik, strip, atau underscore."),400
    if not display_name:
        return jsonify(error="Nama petugas wajib diisi."),400
    if len(display_name)>30:
        return jsonify(error="Nama petugas maksimal 30 karakter."),400
    if not re.fullmatch(r"\d{6}",pin):
        return jsonify(error="PIN harus tepat 6 digit angka."),400

    conn=get_db()
    exists=conn.execute("SELECT id FROM users WHERE lower(username)=lower(?)",(username,)).fetchone()
    if exists:
        conn.close()
        return jsonify(error="Username sudah digunakan."),409

    admin_count=conn.execute("SELECT COUNT(*) n FROM users WHERE role='ADMIN'").fetchone()["n"]
    role_label=f"ADMIN {admin_count+1}"
    now=now_iso()
    cur=conn.execute(
        """INSERT INTO users(username,display_name,role,pin_hash,active,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        (username,display_name,"ADMIN",generate_password_hash(pin),1,now,now)
    )
    uid=cur.lastrowid
    conn.commit()
    conn.close()

    audit("CREATE_USER","user",uid,{
        "username":username,
        "display_name":display_name,
        "role":role_label
    })
    return jsonify(ok=True,id=uid,role=role_label),201

@app.put("/api/users/<int:user_id>/active")
@server_only
def set_user_active(user_id):
    data=request.get_json(force=True)
    active=1 if bool(data.get("active")) else 0

    conn=get_db()
    user=conn.execute(
        "SELECT id,username,display_name,role,active FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    if not user:
        conn.close()
        return jsonify(error="User tidak ditemukan."),404

    # Akun SERVER tidak boleh dinonaktifkan dari menu user.
    if user["role"]=="SERVER" and active==0:
        conn.close()
        return jsonify(error="Akun SERVER tidak dapat dinonaktifkan."),400

    conn.execute(
        "UPDATE users SET active=?,updated_at=? WHERE id=?",
        (active,now_iso(),user_id)
    )
    conn.commit()
    conn.close()

    audit("ACTIVATE_USER" if active else "DEACTIVATE_USER","user",user_id,{
        "username":user["username"],
        "display_name":user["display_name"],
        "role":user["role"]
    })
    return jsonify(ok=True,active=bool(active))

@app.put("/api/users/<int:user_id>/name")
@server_only
def update_user_name(user_id):
    data=request.get_json(force=True)
    display_name=(data.get("display_name") or "").strip()

    if not display_name:
        return jsonify(error="Nama admin wajib diisi."),400
    if len(display_name)>30:
        return jsonify(error="Nama admin maksimal 30 karakter."),400

    conn=get_db()
    user=conn.execute(
        "SELECT id,username,display_name,role FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    if not user:
        conn.close()
        return jsonify(error="User tidak ditemukan"),404

    old_name=user["display_name"]
    conn.execute(
        "UPDATE users SET display_name=?,updated_at=? WHERE id=?",
        (display_name,now_iso(),user_id)
    )
    conn.commit()
    conn.close()

    audit(
        "CHANGE_USER_NAME",
        "user",
        user_id,
        {
            "username":user["username"],
            "role":user["role"],
            "before":old_name,
            "after":display_name
        }
    )
    return jsonify(ok=True,display_name=display_name)

@app.put("/api/users/<int:user_id>/pin")
@server_only
def change_user_pin(user_id):
    data=request.get_json(force=True); pin=str(data.get("pin") or "").strip()
    if not(pin.isdigit() and len(pin)==6):return jsonify(error="PIN harus tepat 6 digit angka"),400
    conn=get_db(); row=conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
    if not row: conn.close(); return jsonify(error="User tidak ditemukan"),404
    conn.execute("UPDATE users SET pin_hash=?,current_pin=NULL,updated_at=? WHERE id=?",(generate_password_hash(pin),now_iso(),user_id))
    conn.commit(); conn.close()
    audit("CHANGE_PIN","user",user_id,{"target":row["display_name"]}); return jsonify(ok=True)

@app.get("/api/logs")
@server_only
def api_logs():
    limit=min(max(request.args.get("limit",200,type=int),1),1000)
    conn=get_db(); rows=[dict(r) for r in conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",(limit,)).fetchall()]
    conn.close(); return jsonify(rows)



@app.post("/api/orders/<int:oid>/payments")
def add_payment_now(oid):
    data=request.get_json(force=True)
    try:
        nominal=float(data.get("nominal") or 0)
    except Exception:
        return jsonify(error="Nominal DP tidak valid"),400

    if nominal<=0:
        return jsonify(error="Nominal DP harus lebih dari 0"),400

    conn=get_db()
    if admin_order_locked(conn,oid):
        conn.close()
        return jsonify(error="PO DONE terkunci. ADMIN hanya dapat melihat."),403
    order=conn.execute("SELECT id,no_po FROM orders WHERE id=?",(oid,)).fetchone()
    if not order:
        conn.close()
        return jsonify(error="PO tidak ditemukan"),404

    total_order=conn.execute(
        """SELECT COALESCE(SUM(qty_kg * harga_per_kg),0) total
           FROM order_items WHERE order_id=?""",
        (oid,)
    ).fetchone()["total"] or 0

    total_dp_active=conn.execute(
        """SELECT COALESCE(SUM(nominal),0) total
           FROM payments
           WHERE order_id=? AND deleted_at IS NULL""",
        (oid,)
    ).fetchone()["total"] or 0

    if float(total_dp_active) + nominal > float(total_order) + 0.01:
        sisa=max(float(total_order)-float(total_dp_active),0)
        conn.close()
        return jsonify(
            error=(
                f"Total DP tidak boleh melebihi total pesanan. "
                f"Sisa DP maksimal Rp{sisa:,.0f}."
            )
        ),400

    created_at=data.get("created_at") or now_iso()
    creator_uid,creator_name,creator_role=payment_actor_fields()
    cur=conn.execute(
        """INSERT INTO payments(
             order_id,nominal,created_at,created_by_user_id,created_by,created_role
           ) VALUES(?,?,?,?,?,?)""",
        (oid,nominal,created_at,creator_uid,creator_name,creator_role)
    )
    pid=cur.lastrowid
    conn.commit()

    row=conn.execute(
        """SELECT id,order_id,nominal,created_at,created_by_user_id,created_by,created_role,
                  deleted_at,deleted_by,delete_reason
           FROM payments WHERE id=?""",
        (pid,)
    ).fetchone()
    conn.close()

    audit("ADD_DP","payment",pid,{
        "order_id":oid,
        "no_po":order["no_po"],
        "nominal":nominal
    })
    return jsonify(dict(row)),201

@app.delete("/api/payments/<int:payment_id>")
def soft_delete_payment(payment_id):
    data=request.get_json(silent=True) or {}
    reason=(data.get("reason") or "").strip()

    conn=get_db()
    row=conn.execute(
        """SELECT p.*,o.no_po,o.status FROM payments p
           JOIN orders o ON o.id=p.order_id
           WHERE p.id=?""",
        (payment_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify(error="DP tidak ditemukan"),404
    if row["deleted_at"] is not None:
        conn.close()
        return jsonify(error="DP sudah dibatalkan"),400
    if row["status"]=="DONE" and not is_server():
        conn.close()
        return jsonify(error="PO DONE terkunci. ADMIN hanya dapat melihat."),403

    actor=user_identity()
    action="CANCEL_DP"

    if not is_server():
        created=_parse_datetime_value(row["created_at"])
        if created and created.tzinfo is None:
            created=created.replace(tzinfo=JAKARTA_TZ)
        elapsed=999999
        if created:
            elapsed=(datetime.now(JAKARTA_TZ)-created.astimezone(JAKARTA_TZ)).total_seconds()

        same_creator=(row["created_by_user_id"]==session.get("user_id"))
        if not same_creator or elapsed>30:
            conn.close()
            return jsonify(
                error="DP yang sudah tersimpan tidak dapat dihapus oleh ADMIN. Gunakan Ajukan Koreksi."
            ),403

        reason="Undo input DP dalam 30 detik"
        action="UNDO_DP"
    else:
        if len(reason)<3:
            conn.close()
            return jsonify(error="Alasan pembatalan DP wajib diisi oleh SERVER."),400

    deleted_at=now_iso()
    conn.execute(
        """UPDATE payments
           SET deleted_at=?,deleted_by=?,delete_reason=?
           WHERE id=?""",
        (deleted_at,actor,reason,payment_id)
    )
    conn.commit()
    conn.close()

    audit(action,"payment",payment_id,{
        "order_id":row["order_id"],
        "no_po":row["no_po"],
        "nominal":row["nominal"],
        "reason":reason
    })
    return jsonify(ok=True,message="DP dibatalkan dan tercatat di riwayat aktivitas.")




@app.put("/api/payments/<int:payment_id>/correct")
@server_only
def correct_payment(payment_id):
    data=request.get_json(force=True)
    reason=(data.get("reason") or "").strip()
    try:
        nominal=float(data.get("nominal"))
    except Exception:
        return jsonify(error="Nominal koreksi tidak valid."),400

    if nominal < 0:
        return jsonify(error="Nominal koreksi tidak boleh negatif."),400
    if len(reason)<3:
        return jsonify(error="Alasan koreksi wajib diisi."),400

    try:
        with db_transaction() as conn:
            row=conn.execute(
                """SELECT p.*,o.no_po FROM payments p
                   JOIN orders o ON o.id=p.order_id
                   WHERE p.id=?""",
                (payment_id,)
            ).fetchone()
            if not row:
                return jsonify(error="DP tidak ditemukan"),404
            if row["deleted_at"] is not None:
                return jsonify(error="DP sudah dibatalkan."),400

            total_order=order_items_total_db(conn,row["order_id"])
            other_dp=payment_total_for_order(conn,row["order_id"],payment_id)
            if other_dp + nominal > total_order + 0.01:
                return jsonify(
                    error=f"Nominal koreksi membuat total DP melebihi total pesanan. Maksimal {total_order-other_dp:,.0f}."
                ),400

            before=float(row["nominal"])
            if nominal==0:
                conn.execute(
                    """UPDATE payments SET deleted_at=?,deleted_by=?,delete_reason=?
                       WHERE id=?""",
                    (now_iso(),user_identity(),reason,payment_id)
                )
                action="CANCEL_DP"
            else:
                conn.execute(
                    "UPDATE payments SET nominal=? WHERE id=?",
                    (nominal,payment_id)
                )
                action="CORRECT_DP"

        audit(action,"payment",payment_id,{
            "order_id":row["order_id"],
            "no_po":row["no_po"],
            "before":before,
            "after":nominal,
            "reason":reason
        })
        return jsonify(ok=True)
    except Exception as e:
        app.logger.exception("KOREKSI DP GAGAL")
        return jsonify(error=f"Gagal mengoreksi DP: {e}"),500


@app.post("/api/payments/<int:payment_id>/correction-request")
def request_payment_correction(payment_id):
    data=request.get_json(force=True)
    reason=(data.get("reason") or "").strip()
    try:
        requested_nominal=float(data.get("requested_nominal"))
    except Exception:
        return jsonify(error="Nominal koreksi tidak valid."),400

    if requested_nominal < 0:
        return jsonify(error="Nominal koreksi tidak boleh negatif."),400
    if len(reason)<3:
        return jsonify(error="Alasan koreksi wajib diisi minimal 3 karakter."),400

    conn=get_db()
    row=conn.execute(
        """SELECT p.*,o.no_po FROM payments p
           JOIN orders o ON o.id=p.order_id
           WHERE p.id=?""",
        (payment_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify(error="DP tidak ditemukan."),404
    if row["deleted_at"] is not None:
        conn.close()
        return jsonify(error="DP sudah dibatalkan."),400

    exists=conn.execute(
        """SELECT id FROM payment_correction_requests
           WHERE payment_id=? AND status='PENDING'""",
        (payment_id,)
    ).fetchone()
    if exists:
        conn.close()
        return jsonify(error="DP ini sudah memiliki pengajuan koreksi yang menunggu SERVER."),409

    if abs(float(row["nominal"])-requested_nominal)<0.01:
        conn.close()
        return jsonify(error="Nominal koreksi sama dengan nominal saat ini."),400

    u=current_user() or {}
    cur=conn.execute(
        """INSERT INTO payment_correction_requests(
             payment_id,order_id,requested_nominal,reason,
             requested_by_user_id,requested_by,status,created_at
           ) VALUES(?,?,?,?,?,?,'PENDING',?)""",
        (
            payment_id,row["order_id"],requested_nominal,reason,
            u.get("id"),user_identity(u),now_iso()
        )
    )
    rid=cur.lastrowid
    conn.commit()
    conn.close()

    audit("REQUEST_DP_CORRECTION","payment",payment_id,{
        "request_id":rid,
        "order_id":row["order_id"],
        "no_po":row["no_po"],
        "before":row["nominal"],
        "requested":requested_nominal,
        "reason":reason
    })
    return jsonify(ok=True,request_id=rid),201


@app.get("/dp-corrections")
@server_only
def dp_corrections_page():
    return render_template("dp_corrections.html",user=current_user())


@app.get("/api/dp-corrections")
@server_only
def api_dp_corrections():
    conn=get_db()
    rows=[dict(r) for r in conn.execute(
        """SELECT r.*,p.nominal current_nominal,o.no_po,c.name customer
           FROM payment_correction_requests r
           JOIN payments p ON p.id=r.payment_id
           JOIN orders o ON o.id=r.order_id
           JOIN customers c ON c.id=o.customer_id
           ORDER BY CASE r.status WHEN 'PENDING' THEN 0 ELSE 1 END,
                    r.id DESC"""
    ).fetchall()]
    conn.close()
    return jsonify(rows)


@app.post("/api/dp-corrections/<int:request_id>/resolve")
@server_only
def resolve_dp_correction(request_id):
    data=request.get_json(force=True)
    decision=(data.get("decision") or "").strip().upper()
    note=(data.get("note") or "").strip()

    if decision not in {"APPROVE","REJECT"}:
        return jsonify(error="Keputusan tidak valid."),400

    try:
        with db_transaction() as conn:
            req=conn.execute(
                """SELECT r.*,p.nominal current_nominal,p.deleted_at,o.no_po
                   FROM payment_correction_requests r
                   JOIN payments p ON p.id=r.payment_id
                   JOIN orders o ON o.id=r.order_id
                   WHERE r.id=?""",
                (request_id,)
            ).fetchone()
            if not req:
                return jsonify(error="Pengajuan koreksi tidak ditemukan."),404
            if req["status"]!="PENDING":
                return jsonify(error="Pengajuan ini sudah diproses."),400

            actor=user_identity()
            resolved_at=now_iso()

            if decision=="REJECT":
                conn.execute(
                    """UPDATE payment_correction_requests
                       SET status='REJECTED',resolved_by=?,resolution_note=?,resolved_at=?
                       WHERE id=?""",
                    (actor,note or "Ditolak oleh SERVER",resolved_at,request_id)
                )
                action="REJECT_DP_CORRECTION"
            else:
                if req["deleted_at"] is not None:
                    return jsonify(error="DP sudah dibatalkan sehingga tidak dapat dikoreksi."),400

                requested=float(req["requested_nominal"])
                total_order=order_items_total_db(conn,req["order_id"])
                other_dp=payment_total_for_order(conn,req["order_id"],req["payment_id"])
                if other_dp + requested > total_order + 0.01:
                    return jsonify(
                        error=f"Koreksi ditolak: total DP akan melebihi total pesanan. Maksimal Rp{total_order-other_dp:,.0f}."
                    ),400

                if requested==0:
                    conn.execute(
                        """UPDATE payments
                           SET deleted_at=?,deleted_by=?,delete_reason=?
                           WHERE id=?""",
                        (
                            resolved_at,actor,
                            f"Disetujui dari pengajuan koreksi #{request_id}: {req['reason']}",
                            req["payment_id"]
                        )
                    )
                else:
                    conn.execute(
                        "UPDATE payments SET nominal=? WHERE id=?",
                        (requested,req["payment_id"])
                    )

                conn.execute(
                    """UPDATE payment_correction_requests
                       SET status='APPROVED',resolved_by=?,resolution_note=?,resolved_at=?
                       WHERE id=?""",
                    (actor,note or "Disetujui oleh SERVER",resolved_at,request_id)
                )
                action="APPROVE_DP_CORRECTION"

            detail={
                "request_id":request_id,
                "order_id":req["order_id"],
                "no_po":req["no_po"],
                "before":req["current_nominal"],
                "requested":req["requested_nominal"],
                "reason":req["reason"],
                "note":note
            }
        audit(action,"payment",req["payment_id"],detail)
        return jsonify(ok=True)
    except Exception as e:
        app.logger.exception("PROSES KOREKSI DP GAGAL")
        return jsonify(error=f"Gagal memproses koreksi DP: {e}"),500


@app.get("/api/orders/<int:oid>/history")
def order_history(oid):
    conn=get_db()
    order=conn.execute(
        """SELECT id,no_po,edit_count,last_edited_at,last_edited_by
           FROM orders WHERE id=?""",
        (oid,)
    ).fetchone()
    if not order:
        conn.close()
        return jsonify(error="PO tidak ditemukan"),404

    order_rows=conn.execute(
        """SELECT id,actor,role,action,detail,created_at
           FROM audit_logs
           WHERE entity_type='order'
             AND CAST(entity_id AS TEXT)=CAST(? AS TEXT)
             AND action IN ('EDIT_PO','UPDATE_PO','RESTORE_PO')
           ORDER BY id DESC
           LIMIT 100""",
        (oid,)
    ).fetchall()

    payment_rows=conn.execute(
        """SELECT id,actor,role,action,detail,created_at
           FROM audit_logs
           WHERE entity_type='payment'
             AND action IN (
               'ADD_DP','DELETE_DP','UNDO_DP','CANCEL_DP','CORRECT_DP',
               'REQUEST_DP_CORRECTION','APPROVE_DP_CORRECTION','REJECT_DP_CORRECTION'
             )
           ORDER BY id DESC
           LIMIT 300"""
    ).fetchall()
    conn.close()

    history=[]
    for r in list(order_rows)+list(payment_rows):
        try:
            detail=json.loads(r["detail"] or "{}")
        except Exception:
            detail={}

        if r["action"] not in ('EDIT_PO','UPDATE_PO','RESTORE_PO'):
            if str(detail.get("order_id")) != str(oid):
                continue

        history.append({
            "id":r["id"],
            "actor":r["actor"],
            "role":r["role"],
            "action":r["action"],
            "detail":detail,
            "created_at":r["created_at"]
        })

    history.sort(key=lambda x:x["id"],reverse=True)
    return jsonify(order=dict(order),history=history[:100])

@app.get("/api/deleted-orders")
@server_only
def deleted_orders():
    conn=get_db()
    rows=[dict(r) for r in conn.execute(
        """SELECT o.id,o.no_po,o.tanggal,o.status,o.catatan,o.created_at,
                  o.deleted_at,o.deleted_by,c.name customer,
                  COALESCE((SELECT SUM(oi.qty_kg*oi.harga_per_kg)
                            FROM order_items oi WHERE oi.order_id=o.id),0) total,
                  COALESCE((SELECT SUM(p.nominal)
                            FROM payments p
                            WHERE p.order_id=o.id AND p.deleted_at IS NULL),0) total_dp,
                  (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id=o.id) jumlah_item,
                  (SELECT COUNT(*) FROM order_images im WHERE im.order_id=o.id) jumlah_foto
           FROM orders o
           JOIN customers c ON c.id=o.customer_id
           WHERE o.deleted_at IS NOT NULL
           ORDER BY o.deleted_at DESC"""
    ).fetchall()]
    conn.close()
    return jsonify(rows)

@app.post("/api/orders/<int:oid>/restore")
@server_only
def restore_order(oid):
    try:
        with db_transaction() as conn:
            row=conn.execute(
                "SELECT id,no_po,deleted_at FROM orders WHERE id=?",
                (oid,)
            ).fetchone()
            if not row:
                return jsonify(error="PO tidak ditemukan"),404
            if row["deleted_at"] is None:
                return jsonify(error="PO ini tidak sedang terhapus"),400

            actor=user_identity()
            conn.execute(
                """UPDATE orders
                   SET deleted_at=NULL,deleted_by=NULL,restored_at=?,restored_by=?,updated_at=?
                   WHERE id=?""",
                (now_iso(),actor,now_iso(),oid)
            )
            no_po=row["no_po"]

        audit("RESTORE_PO","order",oid,{"no_po":no_po})
        return jsonify(ok=True)
    except Exception as e:
        app.logger.exception("RESTORE PO GAGAL - transaksi dirollback")
        return jsonify(error=f"Gagal restore PO: {e}"),500

@app.get("/api/master")
def api_master(): return jsonify(load_master())

@app.post("/api/master/<kind>")
@server_only
def api_master_add(kind):
    data=request.get_json(force=True)
    try:
        value=data.get("value",""); add_master_value(kind,value)
        audit("MASTER_ADD",kind,None,{"value":value}); return jsonify(ok=True,master=load_master())
    except ValueError as e:return jsonify(error=str(e)),400

@app.delete("/api/master/<kind>")
@server_only
def api_master_delete(kind):
    try:
        value=request.args.get("value"); customer_id=request.args.get("customer_id",type=int)
        delete_master_value(kind,value=value,customer_id=customer_id)
        audit("MASTER_DELETE",kind,customer_id or value,{"value":value}); return jsonify(ok=True,master=load_master())
    except ValueError as e:return jsonify(error=str(e)),400

@app.get("/api/orders")
def api_orders():
    conn=get_db()
    rows=conn.execute("""SELECT o.*,c.name customer
                       FROM orders o
                       JOIN customers c ON c.id=o.customer_id
                       WHERE o.deleted_at IS NULL
                       ORDER BY o.id DESC""").fetchall()
    data=[order_dict(conn,r) for r in rows]
    conn.close()
    response=jsonify(data)
    response.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    return response

@app.post("/api/orders")
def create_order():
    data=request.get_json(force=True)

    limit_error=validate_item_limits(data.get("items",[]))
    if limit_error:
        return jsonify(error=limit_error),400

    payment_error=validate_payments_total(data.get("items",[]),data.get("payments",[]))
    if payment_error:
        return jsonify(error=payment_error),400

    customer=(data.get("customer") or "").strip()
    if not customer:
        return jsonify(error="Customer wajib diisi"),400
    if len(customer)>30:
        return jsonify(error="Nama customer maksimal 30 karakter."),400

    requested_status=(data.get("status") or "PROSES").strip().upper()
    if requested_status not in ORDER_STATUSES:
        return jsonify(error="Status PO tidak valid."),400

    try:
        with db_transaction() as conn:
            cur=conn.cursor()
            cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)",(customer,))
            cid=cur.execute(
                "SELECT id FROM customers WHERE lower(name)=lower(?)",
                (customer,)
            ).fetchone()["id"]

            no_po=next_po(conn)
            tanggal=datetime.now(JAKARTA_TZ).date().isoformat()
            estimasi=int(data.get("estimasi_hari") or 21)
            target=(datetime.fromisoformat(tanggal).date()+timedelta(days=estimasi)).isoformat()

            cur.execute(
                """INSERT INTO orders(
                     no_po,tanggal,customer_id,estimasi_hari,target,status,catatan,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    no_po,tanggal,cid,estimasi,target,
                    requested_status,
                    data.get("catatan") or "",
                    now_iso(),now_iso()
                )
            )
            oid=cur.lastrowid

            for it in data.get("items",[]):
                kg=float(it.get("qty_kg") or 0)
                rolls=float(it.get("qty_roll") or (kg/25 if kg else 0))
                cur.execute(
                    """INSERT INTO order_items(
                         order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        oid,it.get("jenis_kain",""),it.get("warna",""),
                        kg,rolls,_normalize_price(it.get("harga_per_kg") or 0)
                    )
                )

            creator_uid,creator_name,creator_role=payment_actor_fields()
            for p in data.get("payments",[]):
                cur.execute(
                    """INSERT INTO payments(
                         order_id,nominal,created_at,created_by_user_id,created_by,created_role
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        oid,float(p.get("nominal") or 0),
                        p.get("created_at") or now_iso(),
                        creator_uid,creator_name,creator_role
                    )
                )

            row=conn.execute(
                """SELECT o.*,c.name customer
                   FROM orders o JOIN customers c ON c.id=o.customer_id
                   WHERE o.id=? AND o.deleted_at IS NULL""",
                (oid,)
            ).fetchone()
            result=order_dict(conn,row)

        audit("CREATE_PO","order",oid,{"no_po":no_po,"customer":customer})
        return jsonify(result),201
    except Exception as e:
        app.logger.exception("CREATE PO GAGAL - transaksi dirollback")
        return jsonify(error=f"Gagal membuat PO. Tidak ada data setengah tersimpan. Detail: {e}"),500

@app.put("/api/orders/<int:oid>")
def update_order(oid):
    data=request.get_json(force=True)

    limit_error=validate_item_limits(data.get("items",[]))
    if limit_error:
        return jsonify(error=limit_error),400

    payment_error=validate_payments_total(data.get("items",[]),data.get("payments",[]))
    if payment_error:
        return jsonify(error=payment_error),400

    customer=(data.get("customer") or "").strip()
    if not customer:
        return jsonify(error="Customer wajib diisi"),400
    if len(customer)>30:
        return jsonify(error="Nama customer maksimal 30 karakter."),400

    deleted_events=[]
    added_events=[]

    requested_status=(data.get("status") or "").strip().upper()
    if requested_status and requested_status not in ORDER_STATUSES:
        return jsonify(error="Status PO tidak valid."),400

    try:
        with db_transaction() as conn:
            cur=conn.cursor()

            old=cur.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
            if not old:
                return jsonify(error="PO tidak ditemukan"),404

            if old["status"]=="DONE" and not is_server():
                return jsonify(error="PO berstatus DONE dan sudah dikunci. ADMIN hanya dapat melihat data."),403

            before_row=conn.execute(
                """SELECT o.*,c.name customer
                   FROM orders o JOIN customers c ON c.id=o.customer_id
                   WHERE o.id=?""",
                (oid,)
            ).fetchone()
            before_order=order_dict(conn,before_row)
            before_snapshot=_simple_order_snapshot(before_order)

            active_rows=cur.execute(
                "SELECT * FROM payments WHERE order_id=? AND deleted_at IS NULL",
                (oid,)
            ).fetchall()
            active_by_id={int(r["id"]):r for r in active_rows}
            submitted_existing={
                int(p["id"]):p for p in data.get("payments",[]) if p.get("id")
            }

            if not is_server():
                missing_ids=set(active_by_id)-set(submitted_existing)
                if missing_ids:
                    return jsonify(
                        error="DP yang sudah tersimpan tidak dapat dihapus oleh ADMIN. Gunakan Ajukan Koreksi."
                    ),403

                for pid,p in submitted_existing.items():
                    oldp=active_by_id.get(pid)
                    if oldp is None:
                        return jsonify(error="Data DP tidak valid."),400
                    incoming=float(p.get("nominal") or 0)
                    if abs(incoming-float(oldp["nominal"] or 0))>0.01:
                        return jsonify(
                            error="Nominal DP tersimpan tidak dapat diubah langsung oleh ADMIN. Gunakan Ajukan Koreksi."
                        ),403

            cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)",(customer,))
            cid=cur.execute(
                "SELECT id FROM customers WHERE lower(name)=lower(?)",
                (customer,)
            ).fetchone()["id"]

            tanggal=old["tanggal"] or datetime.now(JAKARTA_TZ).date().isoformat()
            estimasi=int(data.get("estimasi_hari") or old["estimasi_hari"])
            target=(datetime.fromisoformat(tanggal).date()+timedelta(days=estimasi)).isoformat()

            actor=user_identity()
            edited_at=now_iso()

            cur.execute(
                """UPDATE orders
                   SET tanggal=?,customer_id=?,estimasi_hari=?,target=?,status=?,catatan=?
                   WHERE id=?""",
                (
                    tanggal,cid,estimasi,target,
                    requested_status or old["status"],
                    data.get("catatan") or "",
                    oid
                )
            )

            # Update item secara in-place agar ID item tetap stabil untuk histori/audit.
            existing_items={int(r["id"]):dict(r) for r in cur.execute(
                "SELECT * FROM order_items WHERE order_id=?",(oid,)
            ).fetchall()}
            kept_item_ids=set()

            for it in data.get("items",[]):
                kg=float(it.get("qty_kg") or 0)
                rolls=float(it.get("qty_roll") or (kg/25 if kg else 0))
                item_id=it.get("id")
                existing_id=int(item_id) if str(item_id or "").isdigit() else None
                old_item=existing_items.get(existing_id) if existing_id is not None else None
                explicit_price_change=bool(it.get("_price_changed"))

                if old_item is not None and not explicit_price_change:
                    harga=_normalize_price(old_item.get("harga_per_kg") or 0)
                else:
                    harga=_normalize_price(it.get("harga_per_kg") or 0)

                if old_item is not None:
                    cur.execute(
                        """UPDATE order_items
                           SET jenis_kain=?,warna=?,qty_kg=?,qty_roll=?,harga_per_kg=?
                           WHERE id=? AND order_id=?""",
                        (it.get("jenis_kain", ""),it.get("warna", ""),kg,rolls,harga,existing_id,oid)
                    )
                    kept_item_ids.add(existing_id)
                else:
                    cur.execute(
                        """INSERT INTO order_items(
                             order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg
                           ) VALUES(?,?,?,?,?,?)""",
                        (oid,it.get("jenis_kain", ""),it.get("warna", ""),kg,rolls,harga)
                    )
                    kept_item_ids.add(cur.lastrowid)

            for existing_id in set(existing_items)-kept_item_ids:
                cur.execute("DELETE FROM order_items WHERE id=? AND order_id=?",(existing_id,oid))

            active_ids={r["id"] for r in active_rows}
            submitted_existing_ids={
                int(p["id"]) for p in data.get("payments",[]) if p.get("id")
            }

            for payment_id in active_ids-submitted_existing_ids:
                pay=next((r for r in active_rows if r["id"]==payment_id),None)
                cur.execute(
                    """UPDATE payments SET deleted_at=?,deleted_by=?,delete_reason=?
                       WHERE id=? AND order_id=? AND deleted_at IS NULL""",
                    (edited_at,actor,"Dihapus saat edit PO oleh SERVER",payment_id,oid)
                )
                if pay:
                    deleted_events.append((payment_id,pay["nominal"]))

            for p in data.get("payments",[]):
                if p.get("id"):
                    if is_server():
                        cur.execute(
                            """UPDATE payments SET nominal=?,created_at=?
                               WHERE id=? AND order_id=? AND deleted_at IS NULL""",
                            (
                                float(p.get("nominal") or 0),
                                p.get("created_at") or now_iso(),
                                int(p["id"]),oid
                            )
                        )
                else:
                    creator_uid,creator_name,creator_role=payment_actor_fields()
                    cur.execute(
                        """INSERT INTO payments(
                             order_id,nominal,created_at,created_by_user_id,created_by,created_role
                           ) VALUES(?,?,?,?,?,?)""",
                        (
                            oid,float(p.get("nominal") or 0),
                            p.get("created_at") or now_iso(),
                            creator_uid,creator_name,creator_role
                        )
                    )
                    added_events.append((cur.lastrowid,float(p.get("nominal") or 0)))

            row=conn.execute(
                """SELECT o.*,c.name customer
                   FROM orders o JOIN customers c ON c.id=o.customer_id
                   WHERE o.id=? AND o.deleted_at IS NULL""",
                (oid,)
            ).fetchone()
            interim_result=order_dict(conn,row)
            no_po=row["no_po"]
            after_snapshot=_simple_order_snapshot(interim_result)
            changes=_build_change_list(before_snapshot,after_snapshot)

            if changes:
                cur.execute(
                    """UPDATE orders
                       SET updated_at=?,edit_count=COALESCE(edit_count,0)+1,
                           last_edited_at=?,last_edited_by=?
                       WHERE id=?""",
                    (edited_at,edited_at,actor,oid)
                )
                row=conn.execute(
                    """SELECT o.*,c.name customer
                       FROM orders o JOIN customers c ON c.id=o.customer_id
                       WHERE o.id=? AND o.deleted_at IS NULL""",
                    (oid,)
                ).fetchone()
                result=order_dict(conn,row)
            else:
                result=interim_result

        for payment_id,nominal in deleted_events:
            audit("DELETE_DP","payment",payment_id,{
                "order_id":oid,"no_po":no_po,"nominal":nominal
            })
        for payment_id,nominal in added_events:
            audit("ADD_DP","payment",payment_id,{
                "order_id":oid,"no_po":no_po,"nominal":nominal
            })

        if changes:
            audit("EDIT_PO","order",oid,{
                "no_po":no_po,
                "changes":changes,
                "change_count":len(changes),
                "edit_count":result.get("edit_count",0)
            })
        return jsonify(result)

    except Exception as e:
        app.logger.exception("UPDATE PO GAGAL - transaksi dirollback")
        return jsonify(error=f"Gagal mengubah PO. Perubahan dibatalkan seluruhnya. Detail: {e}"),500

@app.delete("/api/orders/<int:oid>")
@server_only
def delete_order(oid):
    try:
        with db_transaction() as conn:
            order=conn.execute(
                "SELECT id,no_po,deleted_at FROM orders WHERE id=?",
                (oid,)
            ).fetchone()
            if not order:
                return jsonify(error="PO tidak ditemukan"),404
            if order["deleted_at"] is not None:
                return jsonify(error="PO sudah berada di Restore PO"),400

            actor=user_identity()
            conn.execute(
                """UPDATE orders SET deleted_at=?,deleted_by=?,updated_at=? WHERE id=?""",
                (now_iso(),actor,now_iso(),oid)
            )
            no_po=order["no_po"]

        audit("DELETE_PO","order",oid,{"no_po":no_po,"soft_delete":True})
        return jsonify(ok=True)
    except Exception as e:
        app.logger.exception("DELETE PO GAGAL - transaksi dirollback")
        return jsonify(error=f"Gagal menghapus PO: {e}"),500

@app.post("/api/orders/<int:oid>/images")
def upload_images(oid):
    conn=get_db()
    if admin_order_locked(conn,oid):
        conn.close()
        return jsonify(error="PO DONE terkunci. ADMIN hanya dapat melihat."),403
    if not conn.execute("SELECT id FROM orders WHERE id=?",(oid,)).fetchone():
        conn.close(); return jsonify(error="PO tidak ditemukan"),404
    existing_hashes=set()
    for r in conn.execute("SELECT filename FROM order_images WHERE order_id=?",(oid,)).fetchall():
        p=UPLOAD_DIR/r["filename"]
        if p.exists():
            try:existing_hashes.add(file_sha256(p))
            except Exception:pass
    added=[]; skipped=0
    for f in request.files.getlist("images"):
        if not f.filename:continue
        ext=Path(secure_filename(f.filename)).suffix.lower()
        if ext not in {".jpg",".jpeg",".png",".webp"}:continue
        temp_path=UPLOAD_DIR/f"_tmp_{uuid.uuid4().hex}{ext}"; f.save(temp_path)
        digest=file_sha256(temp_path)
        if digest in existing_hashes:
            skipped+=1; temp_path.unlink(missing_ok=True); continue
        name=f"{uuid.uuid4().hex}{ext}"; final_path=UPLOAD_DIR/name; temp_path.replace(final_path)
        conn.execute("INSERT INTO order_images(order_id,filename) VALUES(?,?)",(oid,name))
        existing_hashes.add(digest); added.append(name)
    conn.commit(); conn.close()
    if added:audit("UPLOAD_IMAGE","order",oid,{"count":len(added)})
    return jsonify(added=added,skipped_duplicates=skipped)

@app.delete("/api/images/<int:image_id>")
def delete_image(image_id):
    conn=get_db(); row=conn.execute("SELECT order_id,filename FROM order_images WHERE id=?",(image_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM order_images WHERE id=?",(image_id,)); conn.commit()
        p=UPLOAD_DIR/row["filename"]
        if p.exists():p.unlink()
        oid=row["order_id"]
    else:oid=None
    conn.close()
    if row:audit("DELETE_IMAGE","image",image_id,{"order_id":oid})
    return jsonify(ok=True)

@app.get("/api/history-price")
def history_price():
    return jsonify(customer_price_history(request.args.get("customer",""),request.args.get("fabric",""),request.args.get("color","")))

@app.get("/print/dp/<int:oid>")
def print_dp(oid):
    conn=get_db()
    try:
        row=conn.execute("""SELECT o.*,c.name customer
                          FROM orders o JOIN customers c ON c.id=o.customer_id
                          WHERE o.id=? AND o.deleted_at IS NULL""",(oid,)).fetchone()
        if not row:return "PO tidak ditemukan",404
        return render_template("print_dp.html",order=order_dict(conn,row),operator=user_identity())
    finally:conn.close()

@app.get("/print/receipt/<int:oid>")
def print_receipt(oid):
    conn=get_db()
    try:
        row=conn.execute("""SELECT o.*,c.name customer
                          FROM orders o JOIN customers c ON c.id=o.customer_id
                          WHERE o.id=? AND o.deleted_at IS NULL""",(oid,)).fetchone()
        if not row:return "PO tidak ditemukan",404
        return render_template("print_receipt.html",order=order_dict(conn,row),operator=user_identity())
    finally:conn.close()


@app.post("/api/print/receipt/<int:oid>")
def api_print_receipt(oid):
    conn=get_db()
    try:
        row=conn.execute("""SELECT o.*,c.name customer
                          FROM orders o JOIN customers c ON c.id=o.customer_id
                          WHERE o.id=? AND o.deleted_at IS NULL""",(oid,)).fetchone()
        if not row:
            return jsonify(error="PO tidak ditemukan"),404
        order=order_dict(conn,row)
    finally:
        conn.close()

    try:
        payload=request.get_json(silent=True) or {}
        requested_printer=(payload.get("printer") or "").strip()
        if requested_printer:
            installed=list_printers()
            matched=next((p for p in installed if p.casefold()==requested_printer.casefold()),None)
            if not matched:
                return jsonify(error=f"Printer '{requested_printer}' tidak ditemukan di PC server."),400
            requested_printer=matched
        result=print_receipt_thermal(order,user_identity(),requested_printer or None)
        audit("PRINT_THERMAL_AUTO_CUT","order",oid,{"printer":result.get("printer"),"job_id":result.get("job_id")})
        return jsonify(ok=True,printer=result.get("printer"),job_id=result.get("job_id"))
    except PrinterError as e:
        return jsonify(error=str(e)),500
    except Exception as e:
        app.logger.exception("PRINT THERMAL GAGAL")
        return jsonify(error=f"Print thermal gagal: {e}"),500


@app.get("/api/printer/status")
def printer_status():
    try:
        printers=list_printers()
        target=get_target_printer()
        return jsonify(
            ok=True,
            printer=target,
            default_printer=get_default_printer(),
            printers=printers,
            mode="ESC/POS RAW + AUTO CUT",
        )
    except PrinterError as e:
        return jsonify(ok=False,error=str(e),printers=[]),500



@app.post("/api/backup-now")
@server_only
def manual_backup():
    try:
        folder=create_full_backup("MANUAL")
        audit("BACKUP_MANUAL","system",None,{"folder":folder.name})
        return jsonify(ok=True,backup=folder.name)
    except Exception as e:
        app.logger.exception("BACKUP MANUAL GAGAL")
        return jsonify(error=f"Backup gagal: {e}"),500

@app.get("/excel/report")
def excel_report():
    try:
        conn=get_db()
        rows=conn.execute(
            """SELECT o.*,c.name customer
               FROM orders o JOIN customers c ON c.id=o.customer_id
               WHERE o.deleted_at IS NULL
               ORDER BY o.id DESC"""
        ).fetchall()
        orders=[order_dict(conn,r) for r in rows]
        conn.close()

        output=build_report(orders)
        audit("EXPORT_EXCEL","report",None,{"orders":len(orders)})
        return send_file(
            output,
            as_attachment=True,
            download_name=f"LAPORAN-PO-KESELURUHAN-{datetime.now(JAKARTA_TZ).date()}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        app.logger.exception("EXPORT EXCEL GAGAL")
        try:
            audit("EXPORT_EXCEL_ERROR","report",None,{"error":str(e)})
        except Exception:
            pass
        return jsonify(error=f"Export Excel gagal: {e}"),500



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)