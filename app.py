# app.py
# Flask + PyMongo CRUD example
# รองรับ: POST (create) ที่ client จะส่ง "id" เองได้ หรือไม่ส่งก็ได้ (MongoDB สร้าง ObjectId)
# การค้นหา/update/delete รองรับทั้ง _id แบบ ObjectId และ _id แบบ string (custom id)

import os
from flask import Flask, jsonify, request
from flask_cors import CORS  
from pymongo import MongoClient, errors
from bson.objectid import ObjectId
from dotenv import load_dotenv

# ---------- โหลดค่าจาก .env (เฉพาะสำหรับ development convenience) ----------
# load_dotenv() จะอ่านไฟล์ .env ใน working dir และเซ็ต environment variables ให้
load_dotenv()

# ---------- อ่านค่าจาก environment ----------
# MONGO_URI: connection string ไปยัง MongoDB Atlas (หรือ MongoDB instance อื่น)
MONGO_URI = os.environ.get("MONGO_URI")
# MONGO_DB_NAME: ชื่อฐานข้อมูลที่จะใช้ (ถ้ายังไม่มี MongoDB จะสร้างให้เมื่อเขียนข้อมูล)
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "demo_db")
# PORT: พอร์ตที่แอปจะฟัง (default 5000)
PORT = int(os.environ.get("PORT", 5000))

# ---------- ตรวจสอบว่ามีค่า MONGO_URI หรือไม่ ----------
if not MONGO_URI:
    raise RuntimeError(
        "MONGO_URI ไม่ถูกตั้งค่า — ให้สร้างไฟล์ .env หรือกำหนด environment variable MONGO_URI"
    )

# ---------- สร้าง MongoClient ที่ระดับโมดูล (top-level) ----------
# เหตุผล: เมื่อรันบน serverless / cloud function การสร้าง client ที่ระดับโมดูลช่วย reuse connection pool
# และลดเวลา cold start และการสร้าง connection ซ้ำซ้อน
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)  # timeout สั้นเพื่อไม่ให้ request ค้างนาน
db = client[MONGO_DB_NAME]  # เลือก database
users_col = db["users"]     # เลือก collection สำหรับเก็บ user documents

# ---------- สร้าง unique index บน field "email" (idempotent) ----------
# เพื่อป้องกันการสร้างข้อมูลซ้ำที่ email เดียวกัน
try:
    # create_index จะไม่ throw ถ้ามี index อยู่แล้ว (idempotent)
    users_col.create_index("email", unique=True)
except Exception as e:
    # บาง environment อาจไม่มีสิทธิ์สร้าง index — เตือนแต่ไม่หยุดการทำงาน
    print("Warning: ไม่สามารถสร้าง index บน email ได้:", e)

# ---------- สร้าง Flask application ----------
app = Flask(__name__)
CORS(app)

# ---------- Helper functions ----------

def serialize_user(doc):
    """
    แปลง document จาก MongoDB ให้เป็น JSON serializable dict
    - doc: document ที่ได้จาก pymongo (dictionary-like)
    - คืนค่า dict ที่มี key: id, name, email, role
    Note: _id จะแปลงเป็น string เสมอ เพื่อให้ client ใช้งานง่าย
    """
    if not doc:
        return None
    return {
        "id": str(doc.get("_id")),  # แปลง _id (ObjectId หรือ custom id) เป็น string
        "name": doc.get("name", ""),
        "email": doc.get("email", ""),
        "role": doc.get("role", "")
    }

def build_id_query(user_id):
    """
    สร้าง query สำหรับค้นหาโดย _id ที่รองรับได้ทั้ง:
    - MongoDB ObjectId (24 hex chars)
    - String custom id
    Algorithm:
    1. ถ้า user_id มีความยาว 24 และเป็น hex ทั้งหมด พยายามแปลงเป็น ObjectId
       ถ้าสำเร็จ ให้ return {"_id": ObjectId(...)}
    2. ถ้าไม่ใช่รูปแบบ ObjectId ให้ใช้ค่าเป็น string: {"_id": user_id}
    การออกแบบนี้ทำให้รองรับทั้งกรณี client ให้ id เป็น ObjectId string หรือ custom id
    """
    # ตรวจเบื้องต้น: ObjectId มักมีความยาว 24 และประกอบด้วยตัวอักษร hex (0-9a-f)
    try:
        if isinstance(user_id, str) and len(user_id) == 24:
            # พยายามสร้าง ObjectId — ถ้ารูปแบบไม่ถูกต้อง จะโยน exception
            oid = ObjectId(user_id)
            return {"_id": oid}
    except Exception:
        # ถ้าแปลงไม่สำเร็จ จะ fallback ไปใช้ string (custom id)
        pass
    # fallback: treat user_id as string custom id
    return {"_id": user_id}

# ---------- Routes (API endpoints) ----------

@app.route("/api/users", methods=["GET"])
def list_users():
    """
    GET /api/users
    - คืนค่ารายการ user ทั้งหมดใน collection
    - Algorithm: อ่านทุก document จาก collection, map ผ่าน serialize_user แล้ว return JSON list
    - ไม่มี pagination ในตัวอย่างนี้ (สามารถขยายเป็น page/limit ในอนาคต)
    """
    docs = users_col.find().sort([("_id", 1)])  # sort ตาม _id ขึ้นต้น (ascending)
    users = [serialize_user(d) for d in docs]
    return jsonify(users), 200

@app.route("/api/users/<user_id>", methods=["GET"])
def get_user(user_id):
    """
    GET /api/users/<user_id>
    - parameter: user_id (จาก URL path)
    - Algorithm:
      1. สร้าง query โดยใช้ build_id_query()
      2. หา document ด้วย find_one()
      3. ถ้าไม่มีคืน 404, ถ้ามีคืน serialize_user(document)
    """
    query = build_id_query(user_id)
    doc = users_col.find_one(query)
    if not doc:
        return jsonify({"error": "Not found"}), 404
    return jsonify(serialize_user(doc)), 200

@app.route("/api/users", methods=["POST"])
def create_user():
    """
    POST /api/users
    - JSON Body (ตัวอย่าง):
      {
        "id": "custom-id-123",    # optional — ถ้าไม่ส่งจะให้ MongoDB สร้าง ObjectId ให้อัตโนมัติ
        "name": "Alice",
        "email": "alice@example.com",
        "role": "engineer"
      }
    - Algorithm:
      1. อ่าน JSON payload
      2. ตรวจ validation เบื้องต้น: ต้องมี name, email
      3. ถ้ามี payload['id'] ให้ใช้เป็น _id (custom id)
         - สร้าง document โดยมี field "_id": custom_id
      4. ถ้าไม่มี id ให้สร้าง document โดยไม่ใส่ _id (MongoDB จะสร้าง ObjectId)
      5. insert_one(document) และจัดการ DuplicateKeyError (เช่น email หรือ custom id ซ้ำ)
      6. คืนค่า 201 พร้อม document ที่สร้าง (serialize)
    - หมายเหตุ: การใช้ custom id ต้องระวังไม่ให้ชนกัน (duplicate)
    """
    payload = request.get_json() or {}

    # อ่าน field พื้นฐาน
    custom_id = payload.get("id")  # optional: client กำหนด id เอง
    name = payload.get("name")
    email = payload.get("email")
    role = payload.get("role", "")

    # validation ขั้นต้น
    if not name or not email:
        return jsonify({"error": "Missing required fields: name and email"}), 400

    # สร้าง document ที่จะ insert
    if custom_id:
        # ถ้า client ให้ id มาเอง เราใช้เป็น _id โดยตรง
        doc = {
            "_id": custom_id,
            "name": name,
            "email": email,
            "role": role
        }
    else:
        # ให้ MongoDB สร้าง _id (ObjectId) อัตโนมัติ
        doc = {
            "name": name,
            "email": email,
            "role": role
        }

    try:
        # พยายาม insert document ลง collection
        res = users_col.insert_one(doc)
    except errors.DuplicateKeyError as e:
        # เกิดเมื่อ _id หรือ email ซ้ำ (unique index)
        # ส่งกลับ error 400 พร้อมข้อความที่ชัดเจน
        return jsonify({"error": "ID or Email already exists", "detail": str(e)}), 400
    except Exception as e:
        # กรณีอื่น ๆ เช่น network error
        return jsonify({"error": "Insert failed", "detail": str(e)}), 500

    # เตรียม response: ถ้าใช้ custom_id ให้ส่ง custom_id กลับ
    if custom_id:
        created = {
            "id": str(custom_id),
            "name": name,
            "email": email,
            "role": role
        }
        return jsonify(created), 201

    # ถ้าใช้ auto ObjectId: หา document ที่สร้างขึ้น (res.inserted_id คือ ObjectId)
    created_doc = users_col.find_one({"_id": res.inserted_id})
    return jsonify(serialize_user(created_doc)), 201

@app.route("/api/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    """
    PUT /api/users/<user_id>
    - parameter: user_id (จาก URL path)
    - Body: JSON ที่มี field ที่อนุญาตให้อัปเดต (name, email, role)
      ตัวอย่าง:
      { "role": "senior engineer" }
    - Algorithm:
      1. สร้าง query ด้วย build_id_query(user_id)
      2. เตรียม update payload ($set)
      3. เรียก find_one_and_update เพื่ออัปเดตและคืนค่า document ที่อัปเดตแล้ว
      4. จัดการกรณี DuplicateKeyError (เช่น update email เป็นค่าเดียวกับ user อื่น)
    """
    payload = request.get_json() or {}
    # กรองเฉพาะ field ที่เรายอมให้อัปเดต
    allowed_fields = {k: v for k, v in payload.items() if k in ("name", "email", "role")}
    if not allowed_fields:
        return jsonify({"error": "No updatable fields provided"}), 400

    query = build_id_query(user_id)

    try:
        # find_one_and_update: อัปเดตแล้วคืนค่า doc ก่อนหรือหลังอัปเดต ขึ้นกับ return_document
        # ใน PyMongo, return_document ต้องใช้ ReturnDocument enum ถ้าต้องการค่าใหม่
        # ที่นี่เราใช้ find_one_and_update ที่จะคืนค่า *ก่อนหน้า* โดยค่าเริ่มต้นไม่ได้; 
        # เพื่อเรียกค่าใหม่หลัง update ให้ใช้ find_one หลังจากนั้น (ง่ายและชัดเจน)
        result = users_col.find_one(query)
        if not result:
            return jsonify({"error": "Not found"}), 404

        # ทำ update
        users_col.update_one(query, {"$set": allowed_fields})
        # อ่าน document ใหม่หลัง update
        updated_doc = users_col.find_one(query)
    except errors.DuplicateKeyError as e:
        return jsonify({"error": "Email already exists", "detail": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Update failed", "detail": str(e)}), 500

    return jsonify(serialize_user(updated_doc)), 200

@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    """
    DELETE /api/users/<user_id>
    - parameter: user_id (จาก URL path)
    - Algorithm:
      1. สร้าง query ด้วย build_id_query()
      2. เรียก delete_one
      3. ถ้าลบสำเร็จ return 200 พร้อม id ที่ลบ
      4. ถ้าไม่มี record คืน 404
    """
    query = build_id_query(user_id)
    try:
        res = users_col.delete_one(query)
    except Exception as e:
        return jsonify({"error": "Delete failed", "detail": str(e)}), 500

    if res.deleted_count == 0:
        return jsonify({"error": "Not found"}), 404

    return jsonify({"deleted": user_id}), 200

@app.route("/health", methods=["GET"])
def health():
    """
    GET /health
    - health check แบบง่าย: ping MongoDB admin command
    - คืนค่า 200 ถ้าพร้อม, 500 พร้อมรายละเอียดถ้าไม่พร้อม
    """
    try:
        client.admin.command("ping")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

# ---------- Run server (local development) ----------
if __name__ == "__main__":
    # debug=True สำหรับการพัฒนา (auto reload) — ปิดเมื่อ deploy production
    app.run(host="0.0.0.0", port=PORT, debug=True)



