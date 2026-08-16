from datetime import datetime, timedelta
from database import db

class User(db.Model):
    __tablename__ = "users"
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    bookings = db.relationship("Booking", backref="user", lazy=True)

class OTP(db.Model):
    __tablename__ = "otps"
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    @classmethod
    def generate_expiration(cls):
        return datetime.utcnow() + timedelta(minutes=10)

class Booking(db.Model):
    __tablename__ = "bookings"
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    
    # Qualification Data
    balcony_direction = db.Column(db.String(20), nullable=False)
    balcony_length = db.Column(db.String(50), nullable=False)
    floor_number = db.Column(db.String(20), nullable=False)
    has_plug = db.Column(db.Boolean, default=True)
    selected_kit = db.Column(db.String(50), nullable=False)
    
    # Site Inspection Address Details
    full_address = db.Column(db.Text, nullable=False)
    flat_number = db.Column(db.String(50), nullable=False)
    
    # Cloudinary Image URLs
    railing_image_url = db.Column(db.String(300), nullable=False)
    view_image_url = db.Column(db.String(300), nullable=False)
    
    # Workflow Status
    status = db.Column(db.String(50), default="Site Inspection Remaining")
    
    # New Pricing Columns mapped from your SQL commands
    price_charged = db.Column(db.Numeric(10, 2), nullable=True)
    advance_amount = db.Column(db.Numeric(10, 2), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)