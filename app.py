from flask import Flask
from flask_cors import CORS
from config import Config
from extensions import db, bcrypt, jwt
from routes import api
from datetime import timedelta


def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/*": {"origins": "*"}})
    app.config.from_object(Config)

    # JWT Token Expiry (5 hours)
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=5)

    # ✅ ADD THESE 3 LINES — Ollama runs slow on CPU, prevent timeout
    app.config["TIMEOUT"] = 600
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 600
    app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB max upload

    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)

    # Register routes blueprint
    app.register_blueprint(api)

    with app.app_context():
        from models import User, Department
        print("Connecting to database...")
        db.create_all()
        print("Tables created...")

        department = Department.query.filter_by(code="CSE").first()
        if not department:
            print("Creating default department (CSE)...")
            department = Department(
                name="Computer Science Engineering",
                code="CSE"
            )
            db.session.add(department)
            db.session.commit()
            print("CSE Department created successfully!")

        existing_admin = User.query.filter_by(role="admin").first()
        if not existing_admin:
            print("Creating default admin...")
            hashed_pw = bcrypt.generate_password_hash("123456").decode("utf-8")
            admin = User(
                full_name="Super Admin",
                email="admin@university.com",
                role="admin",
                department_id=department.id,
                password=hashed_pw,
                is_first_login=False,
                is_active=True
            )
            db.session.add(admin)
            db.session.commit()
            print("Default admin created successfully!")

    return app


app = create_app()

if __name__ == "__main__":
    print("Starting Flask Server...")
    app.run(host="0.0.0.0", port=5000, debug=True)  # ← debug=True is fine for dev