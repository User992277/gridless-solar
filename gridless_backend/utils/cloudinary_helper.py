import cloudinary
import cloudinary.uploader
from flask import current_app

def upload_balcony_image(file_storage, user_id, image_type):
    """
    Uploads a file directly to Cloudinary and returns the secure URL.
    image_type: 'railing' or 'view'
    """
    cloudinary.config(
        cloud_name=current_app.config["CLOUDINARY_CLOUD_NAME"],
        api_key=current_app.config["CLOUDINARY_API_KEY"],
        api_secret=current_app.config["CLOUDINARY_API_SECRET"]
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