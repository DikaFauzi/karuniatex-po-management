
from flask import Flask, render_template, request, jsonify, send_file
from pathlib import Path
from datetime import datetime, timedelta, timezone
from werkzeug.utils import secure_filename
from models.database import get_db, init_db
from services.master_service import load_master, customer_price_history, add_master_value, delete_master_value
from services.excel_service import build_template, build_report, parse_import
import uuid, os, hashlib

BASE_DIR=Path(__file__).resolve().parent
UPLOAD_DIR=BASE_DIR/"static"/"uploads"
UPLOAD_DIR.mkdir(parents=True,exist_ok=True)

def file_sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def cleanup_duplicate_images():
    """Remove exact duplicate image files/rows inside the same order."""
    conn=get_db()
    order_ids=[r["id"] for r in conn.execute("SELECT id FROM orders").fetchall()]
    for oid in order_ids:
        rows=conn.execute("SELECT id,filename FROM order_images WHERE order_id=? ORDER BY id",(oid,)).fetchall()
        seen={}
        for r in rows:
            p=UPLOAD_DIR/r["filename"]
            if not p.exists():
                conn.execute("DELETE FROM order_images WHERE id=?",(r["id"],))
                continue
            try:
                digest=file_sha256(p)
            except Exception:
                continue
            if digest in seen:
                conn.execute("DELETE FROM order_images WHERE id=?",(r["id"],))
                try:p.unlink()
                except Exception:pass
            else:
                seen[digest]=r["id"]
    conn.commit()
    conn.close()

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=25*1024*1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"]=0

JAKARTA_TZ = timezone(timedelta(hours=7))

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

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def next_po(conn):
    d=datetime.now().strftime("%y%m%d")
    row=conn.execute("SELECT value FROM app_meta WHERE key='po_seq'").fetchone()
    seq=int(row["value"]) + 1 if row else 1
    conn.execute("INSERT INTO app_meta(key,value) VALUES('po_seq',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(seq),))
    return f"PO-{d}-{seq:03d}"

def order_dict(conn, row):
    items=[dict(x) for x in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id",(row["id"],)).fetchall()]
    pays=[dict(x) for x in conn.execute("SELECT * FROM payments WHERE order_id=? ORDER BY id",(row["id"],)).fetchall()]
    imgs=[dict(x) for x in conn.execute("SELECT * FROM order_images WHERE order_id=? ORDER BY id",(row["id"],)).fetchall()]
    total=sum(float(i["qty_kg"])*float(i["harga_per_kg"]) for i in items)
    total_dp=sum(float(p["nominal"]) for p in pays)
    kg=sum(float(i["qty_kg"]) for i in items)
    rolls=sum(float(i["qty_roll"]) for i in items)
    d=dict(row)
    d.update(items=items,payments=pays,images=imgs,total=total,total_dp=total_dp,sisa=max(total-total_dp,0),total_qty_kg=kg,total_roll=rolls,dp_terakhir=(pays[-1]["nominal"] if pays else 0))
    return d

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify(ok=True, version="PRODUCTION V6", port=5050)

@app.get("/database")
def database_page():
    return render_template("database.html")

@app.get("/api/master")
def api_master():
    return jsonify(load_master())

@app.post("/api/master/<kind>")
def api_master_add(kind):
    data = request.get_json(force=True)
    try:
        add_master_value(kind, data.get("value", ""))
        return jsonify(ok=True, master=load_master())
    except ValueError as e:
        return jsonify(error=str(e)), 400

@app.delete("/api/master/<kind>")
def api_master_delete(kind):
    try:
        delete_master_value(
            kind,
            value=request.args.get("value"),
            customer_id=request.args.get("customer_id", type=int)
        )
        return jsonify(ok=True, master=load_master())
    except ValueError as e:
        return jsonify(error=str(e)), 400

@app.get("/api/orders")
def api_orders():
    conn=get_db()
    rows=conn.execute("""
      SELECT o.*, c.name customer
      FROM orders o JOIN customers c ON c.id=o.customer_id
      ORDER BY o.id DESC
    """).fetchall()
    data=[order_dict(conn,r) for r in rows]
    conn.close()
    return jsonify(data)

@app.post("/api/orders")
def create_order():
    data=request.get_json(force=True)
    conn=get_db()
    cur=conn.cursor()
    customer=(data.get("customer") or "").strip()
    if not customer: return jsonify(error="Customer wajib diisi"),400
    cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)",(customer,))
    cid=cur.execute("SELECT id FROM customers WHERE lower(name)=lower(?)",(customer,)).fetchone()["id"]
    no_po=next_po(conn)
    tanggal=data.get("tanggal") or datetime.now().date().isoformat()
    estimasi=int(data.get("estimasi_hari") or 21)
    target=(datetime.fromisoformat(tanggal).date()+timedelta(days=estimasi)).isoformat()
    cur.execute("""INSERT INTO orders(no_po,tanggal,customer_id,estimasi_hari,target,status,catatan,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",(no_po,tanggal,cid,estimasi,target,data.get("status") or "BELUM DP",data.get("catatan") or "",now_iso(),now_iso()))
    oid=cur.lastrowid
    for it in data.get("items",[]):
        kg=float(it.get("qty_kg") or 0); rolls=float(it.get("qty_roll") or (kg/25 if kg else 0))
        cur.execute("""INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg) VALUES(?,?,?,?,?,?)""",
                    (oid,it.get("jenis_kain",""),it.get("warna",""),kg,rolls,float(it.get("harga_per_kg") or 0)))
    for p in data.get("payments",[]):
        cur.execute("INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",(oid,float(p.get("nominal") or 0),p.get("created_at") or now_iso()))
    conn.commit()
    row=conn.execute("""SELECT o.*, c.name customer FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",(oid,)).fetchone()
    result=order_dict(conn,row); conn.close()
    return jsonify(result),201

@app.put("/api/orders/<int:oid>")
def update_order(oid):
    data=request.get_json(force=True)
    conn=get_db(); cur=conn.cursor()
    old=cur.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not old: return jsonify(error="PO tidak ditemukan"),404
    customer=(data.get("customer") or "").strip()
    cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)",(customer,))
    cid=cur.execute("SELECT id FROM customers WHERE lower(name)=lower(?)",(customer,)).fetchone()["id"]
    tanggal=data.get("tanggal") or old["tanggal"]; estimasi=int(data.get("estimasi_hari") or old["estimasi_hari"])
    target=(datetime.fromisoformat(tanggal).date()+timedelta(days=estimasi)).isoformat()
    cur.execute("""UPDATE orders SET tanggal=?,customer_id=?,estimasi_hari=?,target=?,status=?,catatan=?,updated_at=? WHERE id=?""",
                (tanggal,cid,estimasi,target,data.get("status") or old["status"],data.get("catatan") or "",now_iso(),oid))
    cur.execute("DELETE FROM order_items WHERE order_id=?",(oid,))
    cur.execute("DELETE FROM payments WHERE order_id=?",(oid,))
    for it in data.get("items",[]):
        kg=float(it.get("qty_kg") or 0); rolls=float(it.get("qty_roll") or (kg/25 if kg else 0))
        cur.execute("INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg) VALUES(?,?,?,?,?,?)",
                    (oid,it.get("jenis_kain",""),it.get("warna",""),kg,rolls,float(it.get("harga_per_kg") or 0)))
    for p in data.get("payments",[]):
        cur.execute("INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",(oid,float(p.get("nominal") or 0),p.get("created_at") or now_iso()))
    conn.commit()
    row=conn.execute("""SELECT o.*, c.name customer FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",(oid,)).fetchone()
    result=order_dict(conn,row); conn.close()
    return jsonify(result)

@app.delete("/api/orders/<int:oid>")
def delete_order(oid):
    conn=get_db()
    imgs=conn.execute("SELECT filename FROM order_images WHERE order_id=?",(oid,)).fetchall()
    conn.execute("DELETE FROM orders WHERE id=?",(oid,)); conn.commit(); conn.close()
    for i in imgs:
        p=UPLOAD_DIR/i["filename"]
        if p.exists(): p.unlink()
    return jsonify(ok=True)

@app.post("/api/orders/<int:oid>/images")
def upload_images(oid):
    conn=get_db()
    if not conn.execute("SELECT id FROM orders WHERE id=?",(oid,)).fetchone():
        conn.close()
        return jsonify(error="PO tidak ditemukan"),404

    existing_rows=conn.execute("SELECT filename FROM order_images WHERE order_id=?",(oid,)).fetchall()
    existing_hashes=set()
    for r in existing_rows:
        p=UPLOAD_DIR/r["filename"]
        if p.exists():
            try: existing_hashes.add(file_sha256(p))
            except Exception: pass

    added=[]
    skipped=0
    for f in request.files.getlist("images"):
        if not f.filename:
            continue
        ext=Path(secure_filename(f.filename)).suffix.lower()
        if ext not in {".jpg",".jpeg",".png",".webp"}:
            continue
        temp_name=f"_tmp_{uuid.uuid4().hex}{ext}"
        temp_path=UPLOAD_DIR/temp_name
        f.save(temp_path)
        digest=file_sha256(temp_path)
        if digest in existing_hashes:
            skipped+=1
            temp_path.unlink(missing_ok=True)
            continue
        name=f"{uuid.uuid4().hex}{ext}"
        final_path=UPLOAD_DIR/name
        temp_path.replace(final_path)
        conn.execute("INSERT INTO order_images(order_id,filename) VALUES(?,?)",(oid,name))
        existing_hashes.add(digest)
        added.append(name)

    conn.commit()
    conn.close()
    return jsonify(added=added, skipped_duplicates=skipped)

@app.delete("/api/images/<int:image_id>")
def delete_image(image_id):
    conn=get_db()
    row=conn.execute("SELECT filename FROM order_images WHERE id=?",(image_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM order_images WHERE id=?",(image_id,)); conn.commit()
        p=UPLOAD_DIR/row["filename"]
        if p.exists(): p.unlink()
    conn.close()
    return jsonify(ok=True)

@app.get("/api/history-price")
def history_price():
    return jsonify(customer_price_history(request.args.get("customer",""),request.args.get("fabric",""),request.args.get("color","")))

@app.get("/print/dp/<int:oid>")
def print_dp(oid):
    conn=get_db()
    try:
        row=conn.execute("""SELECT o.*, c.name customer
                            FROM orders o JOIN customers c ON c.id=o.customer_id
                            WHERE o.id=?""",(oid,)).fetchone()
        if not row:
            return "PO tidak ditemukan", 404
        order=order_dict(conn,row)
        return render_template("print_dp.html", order=order)
    except Exception as e:
        app.logger.exception("Gagal membuat halaman print DP")
        return f"Gagal membuat Print DP: {e}", 500
    finally:
        conn.close()

@app.get("/print/receipt/<int:oid>")
def print_receipt(oid):
    conn=get_db()
    try:
        row=conn.execute("""SELECT o.*, c.name customer
                            FROM orders o JOIN customers c ON c.id=o.customer_id
                            WHERE o.id=?""",(oid,)).fetchone()
        if not row:
            return "PO tidak ditemukan", 404
        order=order_dict(conn,row)
        return render_template("print_receipt.html", order=order)
    finally:
        conn.close()

@app.get("/excel/template")
def excel_template():
    return send_file(build_template(),as_attachment=True,download_name="TEMPLATE-IMPORT-PO-AN-OTS.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/excel/report")
def excel_report():
    conn=get_db()
    rows=conn.execute("""SELECT o.*, c.name customer FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC""").fetchall()
    orders=[order_dict(conn,r) for r in rows]; conn.close()
    return send_file(build_report(orders),as_attachment=True,download_name=f"LAPORAN-PO-KESELURUHAN-{datetime.now().date()}.xlsx",mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.post("/excel/import")
def excel_import():
    if "file" not in request.files: return jsonify(error="File tidak ada"),400
    try: data=parse_import(request.files["file"])
    except Exception as e: return jsonify(error=str(e)),400

    conn=get_db(); cur=conn.cursor()
    cur.execute("DELETE FROM orders")
    order_map={}
    for r in data["orders"]:
        no_po=str(r.get("NO_PO") or "").strip()
        if not no_po: continue
        customer=str(r.get("CUSTOMER") or "").strip()
        cur.execute("INSERT OR IGNORE INTO customers(name) VALUES(?)",(customer,))
        cid=cur.execute("SELECT id FROM customers WHERE lower(name)=lower(?)",(customer,)).fetchone()["id"]
        tanggal=str(r.get("TANGGAL") or datetime.now().date().isoformat())
        if "/" in tanggal:
            try: tanggal=datetime.strptime(tanggal,"%d/%m/%Y").date().isoformat()
            except: pass
        est=int(r.get("ESTIMASI_HARI") or 21)
        target=(datetime.fromisoformat(tanggal).date()+timedelta(days=est)).isoformat()
        cur.execute("""INSERT INTO orders(no_po,tanggal,customer_id,estimasi_hari,target,status,catatan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (no_po,tanggal,cid,est,target,str(r.get("STATUS") or "BELUM DP"),str(r.get("CATATAN") or ""),now_iso(),now_iso()))
        order_map[no_po]=cur.lastrowid
    for r in data["items"]:
        no_po=str(r.get("NO_PO") or "").strip()
        if no_po not in order_map: continue
        kg=float(r.get("QTY_KG") or 0); rolls=float(r.get("QTY_ROLL") or (kg/25 if kg else 0))
        cur.execute("INSERT INTO order_items(order_id,jenis_kain,warna,qty_kg,qty_roll,harga_per_kg) VALUES(?,?,?,?,?,?)",
                    (order_map[no_po],str(r.get("JENIS_KAIN") or ""),str(r.get("WARNA") or ""),kg,rolls,float(r.get("HARGA_PER_KG") or 0)))
    for r in data["payments"]:
        no_po=str(r.get("NO_PO") or "").strip()
        if no_po not in order_map: continue
        dt=str(r.get("TANGGAL_JAM_DP") or now_iso())
        cur.execute("INSERT INTO payments(order_id,nominal,created_at) VALUES(?,?,?)",(order_map[no_po],float(r.get("NOMINAL_DP") or 0),dt))
    nums=[]
    for k in order_map:
        try: nums.append(int(k.split("-")[-1]))
        except: pass
    if nums: cur.execute("INSERT INTO app_meta(key,value) VALUES('po_seq',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(max(nums)),))
    conn.commit(); conn.close()
    return jsonify(imported=len(order_map))

if __name__=="__main__":
    init_db()
    cleanup_duplicate_images()
    app.run(host="0.0.0.0",port=5050,debug=False)
