from flask import Flask
from config import Config
from extensions import db, bcrypt, jwt
from routes import api
from datetime import timedelta


# ==========================================
# CREATE FLASK APP
# ==========================================
def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # JWT Token Expiry (5 hours)
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=5)

    # Initialize extensions
    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)

    # Register routes blueprint
    app.register_blueprint(api)

    # ==========================================
    # DATABASE INITIALIZATION
    # ==========================================
    with app.app_context():

        # Import models inside context to avoid circular imports
        from models import User, Department

        # Create tables if not exist
        db.create_all()

        # --------------------------------------
        # CREATE DEFAULT DEPARTMENT
        # --------------------------------------
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

        # --------------------------------------
        # CREATE DEFAULT ADMIN
        # --------------------------------------
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


# ==========================================
# RUN APPLICATION
# ==========================================
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)