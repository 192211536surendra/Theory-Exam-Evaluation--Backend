import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "exam_ai_system_secure_secret_key_2026")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "exam_ai_system_secure_secret_key_2026")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://root:@localhost/exam_ai_db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 10}
    }
