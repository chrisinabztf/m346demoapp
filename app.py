import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from datetime import datetime

# --- Konfiguration ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# Datenbank
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "gallery")
DB_PASS = os.environ.get("DB_PASS", "gallery")
DB_NAME = os.environ.get("DB_NAME", "gallery")
app.config["SQLALCHEMY_DATABASE_URI"] = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Storage-Modus: "local" oder "s3"
STORAGE_MODE = os.environ.get("STORAGE_MODE", "local")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# --- S3/MinIO Client (nur wenn STORAGE_MODE=s3) ---
s3_client = None
if STORAGE_MODE == "s3":
    import boto3
    from botocore.client import Config
    S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
    S3_BUCKET = os.environ.get("S3_BUCKET", "cloudgallery")
    s3_client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )
    # Bucket erstellen falls nicht vorhanden
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        s3_client.create_bucket(Bucket=S3_BUCKET)

# --- Datenbankmodell ---
class Image(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    storage = db.Column(db.String(10), default="local")  # "local" oder "s3"

with app.app_context():
    db.create_all()

# --- Hilfsfunktionen ---
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_file(file, filename):
    if STORAGE_MODE == "s3":
        s3_client.upload_fileobj(file, S3_BUCKET, filename, ExtraArgs={"ContentType": file.content_type})
        return "s3"
    else:
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        return "local"

def get_image_url(image):
    if image.storage == "s3":
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": image.filename},
            ExpiresIn=3600
        )
    return url_for("serve_image", filename=image.filename)

# --- Routen ---
@app.route("/")
def index():
    images = Image.query.order_by(Image.uploaded_at.desc()).all()
    image_urls = [(img, get_image_url(img)) for img in images]
    return render_template("index.html", image_urls=image_urls, storage_mode=STORAGE_MODE)

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        flash("Keine Datei ausgewählt", "error")
        return redirect(url_for("index"))
    file = request.files["file"]
    if file.filename == "":
        flash("Keine Datei ausgewählt", "error")
        return redirect(url_for("index"))
    if not allowed_file(file.filename):
        flash("Nur PNG, JPG, JPEG und GIF erlaubt", "error")
        return redirect(url_for("index"))

    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    storage = save_file(file, filename)

    image = Image(filename=filename, original_name=secure_filename(file.filename), storage=storage)
    db.session.add(image)
    db.session.commit()
    flash(f"Bild hochgeladen ({storage.upper()} Storage)", "success")
    return redirect(url_for("index"))

@app.route("/delete/<image_id>", methods=["POST"])
def delete(image_id):
    image = Image.query.get_or_404(image_id)
    if image.storage == "s3":
        s3_client.delete_object(Bucket=S3_BUCKET, Key=image.filename)
    else:
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], image.filename)
        if os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(image)
    db.session.commit()
    flash("Bild gelöscht", "success")
    return redirect(url_for("index"))

@app.route("/uploads/<filename>")
def serve_image(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/health")
def health():
    return {"status": "ok", "storage": STORAGE_MODE}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
