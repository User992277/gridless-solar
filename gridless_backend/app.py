import os
import random
from datetime import datetime
from dotenv import load_dotenv

import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import cloudinary
import cloudinary.uploader

from functools import wraps
from config import Config

# Import shared db instance and database models
from database import db
from models import User, OTP, Booking

import razorpay

# Load environment variables
load_dotenv()

# Initialize Flask App
app = Flask(__name__)

# Initialize Razorpay Client
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

# --- Configuration ---
app.config.from_object(Config)
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

def issue_token(user_id):
    return serializer.dumps({"user_id": user_id})

def get_authenticated_user_id():
    """Checks Authorization: Bearer <token> first, falls back to session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if token and token != "null" and token != "undefined":
            try:
                data = serializer.loads(token, max_age=60 * 60 * 24 * 30)  # 30 days
                return data.get("user_id")
            except (BadSignature, SignatureExpired):
                pass
    return session.get("user_id")

def get_authenticated_user_id_strict():
    """Checks Bearer token first, falls back to session if valid."""
    return get_authenticated_user_id()

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = get_authenticated_user_id()
        user = User.query.get(user_id) if user_id else None
        if not user or not user.is_admin:
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return wrapper

# Initialize Extensions
db.init_app(app)
CORS(app, supports_credentials=True, origins=[
    "https://gridless-solar.shop",
    "https://www.gridless-solar.shop",
    "https://gridless-solar.vercel.app", 
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5000",
    "http://127.0.0.1:5000"
], allow_headers=["Content-Type", "Authorization"], methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])

@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_BYTES = 15 * 1024 * 1024  # Expanded to 15MB for high-res mobile photos

def validate_image(file_storage):
    if not file_storage or not file_storage.filename:
        return False
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS and not (file_storage.mimetype and file_storage.mimetype.startswith("image/")):
        return False
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    return size <= MAX_BYTES

def upload_balcony_image(file_storage, user_id, image_type):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET")
    )
    
    folder_path = f"balcony_solar/user_{user_id}"
    public_id = f"{image_type}_photo"
    
    upload_result = cloudinary.uploader.upload(
        file_storage,
        folder=folder_path,
        public_id=public_id,
        overwrite=True,
        resource_type="image"
    )
    return upload_result.get("secure_url")

# ==========================================
# ROUTE 1: QUALIFICATION WIZARD
# ==========================================
@app.route("/api/qualify", methods=["POST"])
def qualify_balcony():
    data = request.json or {}
    direction = data.get("direction", "").upper()
    try:
        length = float(data.get("length_meters", 0))
    except (ValueError, TypeError):
        length = 2.0
    has_plug = data.get("has_plug", True)
    
    if length < 1.6:
        recommended_tier = "Compact (225W Loom Solar)"
    elif 1.6 <= length <= 2.5:
        recommended_tier = "Comfort (400W Waaree All-Black)"
    else:
        recommended_tier = "Luxury Deck (540W Waaree or Dual Panels)"
        
    warning = None
    if direction in ["N", "NORTH", "NW", "NE"]:
        warning = "North-facing balconies receive indirect light. A custom tilt mount may be required."
        
    return jsonify({
        "status": "success",
        "recommended_tier": recommended_tier,
        "warning": warning,
        "has_plug_issue": not has_plug
    }), 200

# ==========================================
# ROUTE 2: AUTHENTICATION & BREVO OTP
# ==========================================
@app.route("/api/send-otp", methods=["POST"])
@limiter.limit("5 per minute")
def send_otp():
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    intent = data.get("intent", "login")
    
    if not email:
        return jsonify({"error": "Email is required"}), 400
        
    if intent == "login":
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({"error": "NO_BOOKING", "message": "No account found."}), 404
            
        booking = Booking.query.filter_by(user_id=user.id).first()
        if not booking:
            return jsonify({"error": "NO_BOOKING", "message": "No active booking found."}), 404

    otp_code = str(random.randint(100000, 999999))
    
    otp_entry = OTP.query.filter_by(email=email).first()
    if not otp_entry:
        otp_entry = OTP(email=email, code=otp_code, expires_at=OTP.generate_expiration())
        db.session.add(otp_entry)
    else:
        otp_entry.code = otp_code
        otp_entry.expires_at = OTP.generate_expiration()
        
    db.session.commit()
    
    brevo_api_key = os.environ.get('BREVO_API_KEY')
    sender_email = os.environ.get('SENDER_EMAIL')
    
    if brevo_api_key:
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": brevo_api_key,
            "content-type": "application/json"
        }
        payload = {
            "sender": {"name": "GRIDLESS", "email": sender_email},
            "to": [{"email": email}],
            "subject": "Your GRIDLESS Verification Code",
            "htmlContent": f"""
            <div style="font-family: sans-serif; padding: 20px; color: #1C2321;">
                <h2>Verify Your Email</h2>
                <p>Your GRIDLESS verification code is:</p>
                <h1 style="color: #B08D57; letter-spacing: 4px;">{otp_code}</h1>
                <p>This code is valid for 10 minutes.</p>
            </div>
            """
        }
        try:
            res = requests.post(url, headers=headers, json=payload)
            res.raise_for_status()
        except Exception as e:
            print(f"[Brevo Error] Could not send email: {e}")
            
    return jsonify({"message": "OTP sent successfully to email"}), 200

@app.route("/api/verify-otp", methods=["POST"])
@limiter.limit("5 per minute")
def verify_otp():
    data = request.json or {}
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    
    if not email or not code:
        return jsonify({"error": "Email and OTP code are required"}), 400
    
    record = OTP.query.filter_by(email=email, code=code).first()
    if not record or record.expires_at < datetime.utcnow():
        return jsonify({"error": "Invalid or expired OTP"}), 400
        
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name, phone=phone)
        db.session.add(user)
    else:
        if name: user.name = name
        if phone: user.phone = phone
        
    db.session.delete(record)
    db.session.commit()
    
    session["user_id"] = user.id
    token = issue_token(user.id)
    
    return jsonify({
        "message": "Authenticated successfully",
        "token": token,
        "user": {"id": user.id, "email": user.email, "name": user.name}
    }), 200

# ==========================================
# ROUTE 3: CREATE RAZORPAY ORDER
# ==========================================
@app.route("/api/create-order", methods=["POST"])
def create_order():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({"error": "Authentication required. Please verify with OTP first."}), 401
        
    try:
        order = razorpay_client.order.create({
            "amount": 100,  # ₹1.00 (100 paise)
            "currency": "INR",
            "payment_capture": 1
        })
        return jsonify({
            "order_id": order["id"], 
            "amount": 100,
            "key_id": os.getenv("RAZORPAY_KEY_ID")
        }), 200
    except Exception as e:
        return jsonify({"error": f"Razorpay Order Creation Failed: {str(e)}"}), 500

# ==========================================
# ROUTE 4: SITE INSPECTION BOOKING & UPLOADS
# ==========================================
@app.route("/api/book", methods=["POST"])
def create_booking():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({"error": "Authentication required before booking"}), 401
        
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User record not found"}), 404
        
    # Verify Razorpay Signature
    razorpay_payment_id = request.form.get("razorpay_payment_id")
    razorpay_order_id = request.form.get("razorpay_order_id")
    razorpay_signature = request.form.get("razorpay_signature")
    
    if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
        return jsonify({"error": "Missing payment signature details"}), 400
    
    try:
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
    except Exception as e:
        return jsonify({"error": f"Payment signature verification failed: {str(e)}"}), 400

    balcony_direction = request.form.get("balcony_direction") or "South"
    balcony_length = request.form.get("balcony_length") or "2.0"
    floor_number = request.form.get("floor_number") or "1"
    has_plug = request.form.get("has_plug", "true").lower() == "true"
    selected_kit = request.form.get("selected_kit") or "Grid-Tied (Bill Reducer)"
    
    full_address = request.form.get("full_address", "").strip()
    flat_number = request.form.get("flat_number", "").strip()
    
    if not full_address or not flat_number:
        return jsonify({"error": "Address details are required"}), 400
    
    railing_file = request.files.get("railing_image")
    view_file = request.files.get("view_image")
    
    if not railing_file or not view_file:
        return jsonify({"error": "Both railing and view photos are required"}), 400
        
    if not validate_image(railing_file) or not validate_image(view_file):
        return jsonify({"error": "Images must be in JPEG, PNG, WEBP, or HEIC format and under 15MB"}), 400
        
    try:
        railing_url = upload_balcony_image(railing_file, user_id, "railing")
        view_url = upload_balcony_image(view_file, user_id, "view")
    except Exception as e:
        return jsonify({"error": f"Image upload failed: {str(e)}"}), 500
    
    new_booking = Booking(
        user_id=user.id,
        balcony_direction=balcony_direction,
        balcony_length=balcony_length,
        floor_number=floor_number,
        has_plug=has_plug,
        selected_kit=selected_kit,
        full_address=full_address,
        flat_number=flat_number,
        railing_image_url=railing_url,
        view_image_url=view_url,
        status="Site Inspection Fee Paid"
    )
    
    db.session.add(new_booking)
    db.session.commit()
    
    return jsonify({
        "message": "Booking submitted successfully",
        "booking_id": new_booking.id,
        "status": new_booking.status
    }), 201

# ==========================================
# ROUTE 5: GET CURRENT USER & DASHBOARD DATA
# ==========================================
@app.route("/api/me", methods=["GET"])
def get_current_user():
    user_id = get_authenticated_user_id()
    if not user_id:
        return jsonify({"authenticated": False}), 401
        
    user = User.query.get(user_id)
    if not user:
        return jsonify({"authenticated": False}), 404
        
    latest_booking = Booking.query.filter_by(user_id=user.id).order_by(Booking.created_at.desc()).first()
    
    booking_data = None
    if latest_booking:
        booking_data = {
            "id": latest_booking.id,
            "selected_kit": latest_booking.selected_kit,
            "status": latest_booking.status,
            "address": f"{latest_booking.flat_number}, {latest_booking.full_address}",
            "created_at": latest_booking.created_at.strftime("%Y-%m-%d"),
            "railing_image": latest_booking.railing_image_url
        }
        
    return jsonify({
        "authenticated": True,
        "user": {
            "name": user.name,
            "email": user.email,
            "phone": user.phone
        },
        "booking": booking_data
    }), 200

# ==========================================
# ROUTE 6: LOGOUT
# ==========================================
@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    return jsonify({"message": "Logged out successfully"}), 200

# --- App Initialization ---
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.getenv("FLASK_ENV") != "production")