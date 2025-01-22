from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from functools import wraps
from flask import jsonify, request, flash, redirect, url_for, send_file
from werkzeug.utils import secure_filename
from urllib.parse import urlparse, urljoin
from io import BytesIO
from gridfs import GridFS
from bson import ObjectId
import logging
import time
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File handling constants
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# ============ Database Utilities ============

def with_mongodb_retry(retries=3, delay=2):
    """Decorator for retrying MongoDB operations"""
    def decorator(f):
        @wraps(f)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(retries):
                try:
                    return await f(*args, **kwargs)
                except (ServerSelectionTimeoutError, ConnectionFailure) as e:
                    last_error = e
                    if attempt < retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                        time.sleep(delay)
                    else:
                        logger.error(f"All {retries} attempts failed: {str(e)}")
            raise last_error
        return wrapper
    return decorator

class DatabaseManager:
    """Base class for database operations"""
    def __init__(self, mongo_uri: str):
        self.mongo_uri = mongo_uri
        self.client = None
        self.db = None
        self.connect()

    def connect(self):
        """Establish connection to MongoDB"""
        try:
            if self.client is None:
                self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
                self.client.server_info()
                self.db = self.client.get_default_database()
                logger.info("Successfully connected to MongoDB")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

    def ensure_connected(self):
        """Ensure database connection is active"""
        try:
            if self.client is None:
                self.connect()
            else:
                self.client.server_info()
        except Exception:
            logger.warning("Lost connection to MongoDB, attempting to reconnect...")
            self.connect()

    def __del__(self):
        """Cleanup MongoDB connection"""
        if self.client:
            self.client.close()

# ============ Route Utilities ============

def async_route(f):
    """Decorator to handle async routes"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

def handle_route_errors(f):
    """Decorator to handle common route errors"""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Route error: {str(e)}", exc_info=True)
            flash("An internal error has occurred.", "error")
            return error_response("An internal error has occurred.", 500)
    return wrapper

# ============ File Handling Utilities ============

def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_file_to_gridfs(file, db, prefix: str = '') -> str:
    """Save file to GridFS and return file ID"""
    if file and allowed_file(file.filename):
        fs = GridFS(db)
        filename = secure_filename(f"{prefix}_{file.filename}" if prefix else file.filename)
        file_id = fs.put(
            file.stream.read(),
            filename=filename,
            content_type=file.content_type
        )
        return str(file_id)
    return None

def send_gridfs_file(file_id, db, default_path: str = None):
    """Send file from GridFS or return default file"""
    try:
        fs = GridFS(db)
        if isinstance(file_id, str):
            file_id = ObjectId(file_id)
        file_data = fs.get(file_id)
        return send_file(
            BytesIO(file_data.read()),
            mimetype=file_data.content_type,
            download_name=file_data.filename
        )
    except Exception as e:
        logger.error(f"Error retrieving file: {str(e)}")
        if default_path:
            return send_file(default_path)
        return error_response("An internal error has occurred.", 500)

# ============ Response Utilities ============

def success_response(message: str = "Success", data: dict = None, status_code: int = 200):
    """Standard success response"""
    response = {
        "success": True,
        "message": message
    }
    if data is not None:
        response["data"] = data
    return jsonify(response), status_code

def error_response(message: str = "Error", status_code: int = 400):
    """Standard error response"""
    return jsonify({
        "success": False,
        "message": message
    }), status_code

# ============ Security Utilities ============

def is_safe_url(target: str) -> bool:
    """Verify URL is safe for redirects"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

async def check_password_strength(password: str) -> tuple[bool, str]:
    """
    Check if password meets minimum requirements:
    - At least 8 characters
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    return True, "Password meets all requirements" 