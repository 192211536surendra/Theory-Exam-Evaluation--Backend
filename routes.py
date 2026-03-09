# ======================================================
# IMPORTS
# ======================================================

import os
import json
import time
import mimetypes
import re

# OCR + IMAGE PROCESSING
import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from datetime import datetime

# GROQ AI
from groq import Groq

# FLASK
from flask import Blueprint, request, jsonify
from extensions import db, bcrypt

from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity

# ======================================================
# TESSERACT CONFIG (WINDOWS PATH)
# ======================================================

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ======================================================
# MODELS IMPORT
# ======================================================

from models import (
    User,
    Student,
    Faculty,
    Department,
    FacultyDetails,
    Exam,
    StudentExamResult,
    OMRExam,
    OMRAnswerKey,
    OMRSheet,
    OMRResult,
    TheoryExam,
    TheoryAnswerSheet,
    TheoryResult,
)

# ======================================================
# BLUEPRINT INIT
# ======================================================

api = Blueprint("api", __name__)

print("ROUTES FILE LOADED SUCCESSFULLY")

DEFAULT_PASSWORD = "123456"
# ======================================================
# UPLOAD FOLDERS CONFIG
# ======================================================

BASE_UPLOAD_FOLDER = "uploads"

OMR_UPLOAD_FOLDER = os.path.join(BASE_UPLOAD_FOLDER, "omr_sheets")
THEORY_UPLOAD_FOLDER = os.path.join(BASE_UPLOAD_FOLDER, "theory")

os.makedirs(OMR_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THEORY_UPLOAD_FOLDER, exist_ok=True)


# ======================================================
# GROQ CONFIGURATION
# ======================================================



# groq_client = Groq(api_key=GROQ_API_KEY)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)


# ======================================================
# HELPER FUNCTION - ADMIN CHECK
# ======================================================
def admin_required():
    try:
        admin_id = int(get_jwt_identity())
    except:
        return None

    admin = User.query.get(admin_id)

    if not admin or admin.role != "admin":
        return None

    return admin


# ======================================================
# PDF TEXT EXTRACTOR (OpenCV + Tesseract OCR)
# ======================================================


# ======================================================
# PDF TEXT EXTRACTOR (ADVANCED OCR VERSION)
# ======================================================

def extract_pdf_text(pdf_path):

    text_output = ""

    try:

        # Convert PDF to images
        pages = convert_from_path(pdf_path, dpi=400)

        for page in pages:

            img = np.array(page)

            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Increase contrast
            gray = cv2.equalizeHist(gray)

            # Remove noise
            blur = cv2.GaussianBlur(gray, (3,3), 0)

            # Adaptive threshold (better for uneven lighting)
            thresh = cv2.adaptiveThreshold(
                blur,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                2
            )

            # Morphological dilation (helps handwriting strokes)
            kernel = np.ones((2,2), np.uint8)
            dilated = cv2.dilate(thresh, kernel, iterations=1)

            # Sharpen image
            sharpen_kernel = np.array([
                [0,-1,0],
                [-1,5,-1],
                [0,-1,0]
            ])
            sharpen = cv2.filter2D(dilated, -1, sharpen_kernel)

            # Tesseract configuration
            custom_config = r'--oem 3 --psm 6 -l eng'

            text = pytesseract.image_to_string(
                sharpen,
                config=custom_config
            )

            text_output += text + "\n"

    except Exception as e:
        print("OCR Error:", str(e))

    return text_output
#===========================================
#  OMR EVALUATION
#==========================================
def detect_omr_score(sheet_path, exam_id):
    
    image = cv2.imread(sheet_path)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    blur = cv2.GaussianBlur(gray, (5,5), 0)

    thresh = cv2.threshold(
        blur,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]

    contours, _ = cv2.findContours(
        thresh,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    bubbles = []

    for c in contours:

        x,y,w,h = cv2.boundingRect(c)
        aspect_ratio = w / float(h)

        if w >= 20 and h >= 20 and 0.9 <= aspect_ratio <= 1.1:
            bubbles.append(c)

    bubbles = sorted(bubbles, key=lambda c: (cv2.boundingRect(c)[1], cv2.boundingRect(c)[0]))

    answer_key = {
        k.question_number: k.correct_option
        for k in OMRAnswerKey.query.filter_by(exam_id=exam_id).all()
    }

    options = ["A","B","C","D"]

    correct = 0
    question_index = 0

    for i in range(0, len(bubbles), 4):

        question_index += 1

        group = bubbles[i:i+4]

        filled = None
        max_pixels = 0

        for j, c in enumerate(group):

            mask = np.zeros(thresh.shape, dtype="uint8")

            cv2.drawContours(mask, [c], -1, 255, -1)

            masked = cv2.bitwise_and(thresh, thresh, mask=mask)

            total = cv2.countNonZero(masked)

            if total > max_pixels:
                max_pixels = total
                filled = j

        if filled is not None:

            selected_option = options[filled]

            correct_option = answer_key.get(question_index)

            if selected_option == correct_option:
                correct += 1

    return correct
# ======================================================
# LOGIN HANDLER
# ======================================================
def handle_login(email, password, role):

    if not email or not password:
        return jsonify({"error": "Invalid credentials"}), 401

    user = User.query.filter_by(email=email, role=role).first()

    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    if not bcrypt.check_password_hash(user.password, password):
        return jsonify({"error": "Invalid credentials"}), 401

    if not user.is_active:
        return jsonify({"error": "Account is inactive"}), 403

    token = create_access_token(identity=str(user.id))

    return (
        jsonify(
            {
                "message": f"{role.capitalize()} login successful",
                "token": token,
                "role": user.role,
                "first_login": user.is_first_login,
            }
        ),
        200,
    )


# ======================================================
# LOGIN ROUTES
# ======================================================
@api.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    return handle_login(data.get("email"), data.get("password"), "admin")


@api.route("/student/login", methods=["POST"])
def student_login():
    data = request.get_json() or {}
    return handle_login(data.get("email"), data.get("password"), "student")


@api.route("/faculty/login", methods=["POST"])
def faculty_login():
    data = request.get_json() or {}
    return handle_login(data.get("email"), data.get("password"), "faculty")


# ======================================================
# ADMIN DASHBOARD
# ======================================================
@api.route("/admin/dashboard", methods=["GET"])
@jwt_required()
def admin_dashboard():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    return jsonify(
        {
            "total_students": User.query.filter_by(role="student").count(),
            "total_faculty": User.query.filter_by(role="faculty").count(),
            "total_departments": Department.query.count(),
            "active_users": User.query.filter_by(is_active=True).count(),
            "inactive_users": User.query.filter_by(is_active=False).count(),
        }
    )


# ======================================================
# CHANGE PASSWORD (ALL USERS)
# ======================================================
@api.route("/change_password", methods=["POST"])
@jwt_required()
def change_password():

    user_id = int(get_jwt_identity())

    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json() or {}

    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not current_password or not new_password:
        return jsonify({"error": "Both passwords required"}), 400

    # verify current password
    if not bcrypt.check_password_hash(user.password, current_password):
        return jsonify({"error": "Current password incorrect"}), 401

    # update password
    hashed_pw = bcrypt.generate_password_hash(new_password).decode("utf-8")

    user.password = hashed_pw
    user.is_first_login = False

    db.session.commit()

    return jsonify({"message": "Password updated successfully"}), 200


# ======================================================
# CREATE USER (ADMIN)
# ======================================================
@api.route("/admin/create_user", methods=["POST"])
@jwt_required()
def create_user():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}

    full_name = data.get("full_name")
    email = data.get("email")
    role = data.get("role")
    department_code = data.get("department_code")

    if not full_name or not email or not role or not department_code:
        return jsonify({"error": "Missing required fields"}), 400

    if role not in ["student", "faculty"]:
        return jsonify({"error": "Invalid role"}), 400

    # check duplicate email
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already exists"}), 400

    # find department
    department = Department.query.filter_by(code=department_code).first()

    if not department:
        return jsonify({"error": "Department not found"}), 404

    hashed_pw = bcrypt.generate_password_hash(DEFAULT_PASSWORD).decode("utf-8")

    try:

        # ===============================
        # CREATE USER
        # ===============================
        user = User(
            full_name=full_name,
            email=email,
            role=role,
            department_id=department.id,
            password=hashed_pw,
            is_active=True,
        )

        db.session.add(user)
        db.session.flush()

        # ===============================
        # STUDENT PROFILE
        # ===============================
        if role == "student":

            student = Student(
                user_id=user.id,
                roll_number=data.get("roll_number"),
                semester=data.get("semester"),
                cgpa=data.get("cgpa"),
            )

            db.session.add(student)

        # ===============================
        # FACULTY PROFILE
        # ===============================
        elif role == "faculty":

            faculty_code = f"FAC{user.id:04}"

            faculty = Faculty(
                faculty_id=faculty_code,
                user_id=user.id,
                designation=data.get("designation"),
                experience_years=data.get("experience_years"),
                qualification=data.get("qualification"),
            )

            db.session.add(faculty)

            # insert into faculty_details table
            faculty_details = FacultyDetails(
                faculty_id=faculty_code,
                user_id=user.id,
                department_id=department.id,
                designation=data.get("designation"),
                qualification=data.get("qualification"),
                experience_years=data.get("experience_years"),
            )

            db.session.add(faculty_details)

        db.session.commit()

        return jsonify({
            "message": "User created successfully",
            "user_id": user.id
        }), 201

    except Exception as e:

        db.session.rollback()

        return jsonify({
            "error": "Database error",
            "details": str(e)
        }), 500

# ======================================================
# GENERATE STUDENTS (BULK)
# ======================================================
@api.route("/admin/generate_students", methods=["POST"])
@jwt_required()
def generate_students():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    department = Department.query.filter_by(code="CSE").first()

    if not department:
        return jsonify({"error": "CSE department not found"}), 400

    student_names = [
        "Arjun Kumar",
        "Vishnu Prasad",
        "Suri Surendra",
        "Karthik Reddy",
        "Sai Teja",
        "Rohit Sharma",
        "Aditya Varma",
        "Pranav Iyer",
        "Harsha Vardhan",
        "Lokesh Babu",
        "Naveen Kumar",
        "Rahul Krishna",
        "Abhinav Rao",
        "Sandeep Kumar",
        "Vignesh R",
        "Mohan Raj",
        "Gokul Krishna",
        "Chaitanya Reddy",
        "Manoj Kumar",
        "Dinesh Babu",
    ]

    hashed_pw = bcrypt.generate_password_hash(DEFAULT_PASSWORD).decode("utf-8")

    count = 1

    for name in student_names:

        for _ in range(3):

            email = name.lower().replace(" ", ".") + f"{count}@university.com"

            if User.query.filter_by(email=email).first():
                count += 1
                continue

            user = User(
                full_name=name,
                email=email,
                role="student",
                department_id=department.id,
                password=hashed_pw,
            )

            db.session.add(user)
            db.session.flush()

            student = Student(
                user_id=user.id, roll_number=f"CS{count:03}", semester=6, cgpa=7.5
            )

            db.session.add(student)

            count += 1

    db.session.commit()

    return jsonify({"message": "Students generated successfully"})


# ======================================================
# GENERATE FACULTY (BULK)
# ======================================================
@api.route("/admin/generate_faculty", methods=["POST"])
@jwt_required()
def generate_faculty():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    department = Department.query.filter_by(code="CSE").first()

    if not department:
        return jsonify({"error": "CSE department not found"}), 400

    faculty_names = [
        "Dr Ravi Kumar",
        "Dr Anil Sharma",
        "Dr Meena Iyer",
        "Dr Prakash Reddy",
        "Dr Suresh Babu",
        "Dr Kavitha Rao",
        "Dr Harish Kumar",
        "Dr Divya Lakshmi",
        "Dr Manoj Varma",
        "Dr Sneha Reddy",
        "Dr Gopal Krishna",
        "Dr Rekha Sharma",
        "Dr Vinay Kumar",
        "Dr Sunitha Devi",
        "Dr Ramesh Naidu",
        "Dr Keerthi Prasad",
        "Dr Nandini Rao",
        "Dr Arvind Kumar",
        "Dr Priya Nair",
        "Dr Venkatesh Reddy",
    ]

    hashed_pw = bcrypt.generate_password_hash(DEFAULT_PASSWORD).decode("utf-8")

    count = 1

    for name in faculty_names:

        email = name.lower().replace(" ", ".") + "@university.com"

        if User.query.filter_by(email=email).first():
            continue

        user = User(
            full_name=name,
            email=email,
            role="faculty",
            department_id=department.id,
            password=hashed_pw,
        )

        db.session.add(user)
        db.session.flush()

        faculty_code = f"FAC{user.id:04}"

        faculty = Faculty(
            faculty_id=faculty_code,
            user_id=user.id,
            designation="Assistant Professor",
            experience_years=5 + (count % 5),
            qualification="PhD",
        )

        db.session.add(faculty)

        count += 1

    db.session.commit()

    return jsonify({"message": "20 faculty generated successfully"}), 200


# ======================================================
# TOGGLE USER STATUS
# ======================================================
@api.route("/admin/toggle_user_status/<int:user_id>", methods=["PUT"])
@jwt_required()
def toggle_user_status(user_id):

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    if user.role == "admin":
        return jsonify({"error": "Cannot modify admin"}), 400

    user.is_active = not user.is_active

    db.session.commit()

    return jsonify({"message": "User status updated", "is_active": user.is_active})


# ======================================================
# DELETE USER
# ======================================================
@api.route("/admin/delete_user/<int:user_id>", methods=["DELETE"])
@jwt_required()
def delete_user(user_id):

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    if user.role == "admin":
        return jsonify({"error": "Cannot delete admin"}), 400

    Student.query.filter_by(user_id=user.id).delete()
    Faculty.query.filter_by(user_id=user.id).delete()

    db.session.delete(user)

    db.session.commit()

    return jsonify({"message": "User deleted successfully"})


# ======================================================
# GET USERS
# ======================================================
@api.route("/admin/users", methods=["GET"])
@jwt_required()
def get_users():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    role = request.args.get("role")

    if not role:
        return jsonify({"error": "role parameter required"}), 400

    users = User.query.filter_by(role=role).all()

    result = []

    for u in users:
        result.append(
            {
                "id": u.id,
                "full_name": u.full_name,
                "email": u.email,
                "is_active": u.is_active,
            }
        )

    return jsonify(result)


# ======================================================
# PROFILE
# ======================================================
@api.route("/profile", methods=["GET"])
@jwt_required()
def profile():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    response = user.to_dict()

    # Student Profile Data
    if user.role == "student" and user.student_profile:
        response.update(
            {
                "roll_number": user.student_profile.roll_number,
                "semester": user.student_profile.semester,
                "cgpa": user.student_profile.cgpa,
            }
        )

    # Faculty Profile Data
    if user.role == "faculty" and user.faculty_profile:
        response.update(
            {
                "faculty_id": user.faculty_profile.faculty_id,   # ✅ Added
                "designation": user.faculty_profile.designation,
                "experience_years": user.faculty_profile.experience_years,
            }
        )

    return jsonify(response)

# ======================================================
# CREATE DEPARTMENT
# ======================================================
@api.route("/admin/create_department", methods=["POST"])
@jwt_required()
def create_department():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}

    name = data.get("name")
    code = data.get("code")

    if not name or not code:
        return jsonify({"error": "name and code required"}), 400

    if Department.query.filter_by(code=code.upper()).first():
        return jsonify({"error": "Department already exists"}), 400

    dept = Department(name=name.strip(), code=code.upper())

    db.session.add(dept)
    db.session.commit()

    return jsonify({"message": "Department created successfully"}), 201


# ======================================================
# GET ALL DEPARTMENTS
# ======================================================
@api.route("/admin/departments", methods=["GET"])
@jwt_required()
def get_departments():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    departments = Department.query.all()

    result = []

    for d in departments:

        faculty_count = User.query.filter_by(role="faculty", department_id=d.id).count()

        student_count = User.query.filter_by(role="student", department_id=d.id).count()

        result.append(
            {
                "id": d.id,
                "name": d.name,
                "code": d.code,
                "faculty_count": faculty_count,
                "student_count": student_count,
            }
        )

    return jsonify(result), 200


# ======================================================
# UPDATE DEPARTMENT
# ======================================================
@api.route("/admin/update_department/<int:dept_id>", methods=["PUT"])
@jwt_required()
def update_department(dept_id):

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    dept = Department.query.get(dept_id)

    if not dept:
        return jsonify({"error": "Department not found"}), 404

    data = request.get_json() or {}

    dept.name = data.get("name", dept.name)
    dept.code = data.get("code", dept.code)

    db.session.commit()

    return jsonify({"message": "Department updated successfully"}), 200


# ======================================================
# DELETE DEPARTMENT
# ======================================================
@api.route("/admin/delete_department/<int:dept_id>", methods=["DELETE"])
@jwt_required()
def delete_department(dept_id):

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    dept = Department.query.get(dept_id)

    if not dept:
        return jsonify({"error": "Department not found"}), 404

    db.session.delete(dept)
    db.session.commit()

    return jsonify({"message": "Department deleted successfully"}), 200


# ======================================================
# ADMIN DASHBOARD STATS
# ======================================================
@api.route("/admin/dashboard_stats", methods=["GET"])
@jwt_required()
def dashboard_stats():

    if not admin_required():
        return jsonify({"error": "Unauthorized"}), 403

    total_departments = Department.query.count()

    total_faculty = User.query.filter_by(role="faculty").count()

    total_students = User.query.filter_by(role="student").count()

    return (
        jsonify(
            {
                "total_departments": total_departments,
                "total_faculty": total_faculty,
                "total_students": total_students,
            }
        ),
        200,
    )


# ======================================================
# STUDENT DASHBOARD
# ======================================================
@api.route("/student/dashboard", methods=["GET"])
@jwt_required()
def student_dashboard():

    user_id = int(get_jwt_identity())

    user = User.query.get(user_id)

    if not user or user.role != "student":
        return jsonify({"error": "Unauthorized"}), 403

    # ===============================
    # GET STUDENT PROFILE
    # ===============================
    student = Student.query.filter_by(user_id=user.id).first()

    if not student:
        return jsonify({"error": "Student profile not found"}), 404

    # ===============================
    # STUDENT RESULTS
    # ===============================
    results = StudentExamResult.query.filter_by(student_id=student.id).all()

    # ===============================
    # TOTAL EXAMS
    # ===============================
    exams_taken = len(results)

    average_score = 0

    if exams_taken > 0:
        average_score = sum(r.marks_obtained for r in results) / exams_taken

    # ===============================
    # RECENT EXAMS
    # ===============================
    recent_exams = []

    for r in results[-5:]:  # last 5 exams

        exam = Exam.query.get(r.exam_id)

        if exam:
            recent_exams.append(
                {
                    "exam_name": exam.exam_name,
                    "date": exam.exam_date,
                    "score": r.marks_obtained,
                    "status": "Evaluated",
                }
            )

    # ===============================
    # DASHBOARD RESPONSE
    # ===============================
    dashboard_data = {
        "full_name": user.full_name,
        "student_id": student.roll_number,
        "cgpa": student.cgpa,
        "semester": student.semester,
        "rank": 12,
        "performance": {
            "average_score": round(average_score, 2),
            "exams_taken": exams_taken,
            "class_average": 82.3,
            "improvement": "+6.2%",
        },
        "recent_exams": recent_exams,
    }

    return jsonify(dashboard_data)

#=======================================================
#  CREATE EXAM
#=======================================================
@api.route("/faculty/exams", methods=["GET"])
@jwt_required()
def get_all_exams():

    user_id = int(get_jwt_identity())

    omr_exams = OMRExam.query.filter_by(faculty_id=user_id).all()
    theory_exams = TheoryExam.query.filter_by(faculty_id=user_id).all()

    exams = []

    for e in omr_exams:
        exams.append({
            "exam_id": e.id,
            "exam_name": e.exam_name,
            "type": "OMR",
            "created_at": e.created_at
        })

    for e in theory_exams:
        exams.append({
            "exam_id": e.id,
            "exam_name": e.exam_title,
            "type": "THEORY",
            "created_at": e.created_at
        })

    return jsonify(exams)
# ======================================================
# CREATE EXAM (OMR / THEORY)
# ======================================================
@api.route("/faculty/create_exam", methods=["POST"])
@jwt_required()
def create_exam():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()

    exam_type = data.get("exam_type")

    try:

        if exam_type == "OMR":

            exam = OMRExam(
                exam_name=data.get("exam_name"),
                total_questions=data.get("total_questions"),
                options_per_question=data.get("options_per_question", 4),
                marks_per_question=data.get("marksPerQuestion"),
                faculty_id=user.id
            )

            db.session.add(exam)
            db.session.commit()

            return jsonify({
                "message": "OMR exam created successfully",
                "exam_id": exam.id,
                "type": "OMR"
            }), 201


        elif exam_type == "THEORY":

            exam = TheoryExam(
                exam_title=data.get("exam_name"),
                subject_code=data.get("subject_code"),
                total_marks=data.get("total_marks"),
                faculty_id=user.id
            )

            db.session.add(exam)
            db.session.commit()

            return jsonify({
                "message": "Theory exam created successfully",
                "exam_id": exam.id,
                "type": "THEORY"
            }), 201

        else:
            return jsonify({"error": "Invalid exam type"}), 400


    except Exception as e:

        db.session.rollback()

        return jsonify({
            "error": "Failed to create exam",
            "details": str(e)
        }), 500
# ======================================================
# CREATE THEORY EXAM (FIXED)
# ======================================================
@api.route("/faculty/create_theory_exam", methods=["POST"])
@jwt_required()
def create_theory_exam():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}

    exam_title = data.get("exam_name")
    subject_code = data.get("subject_code")
    total_marks = data.get("total_marks")

    if not exam_title or not subject_code or not total_marks:
        return jsonify({"error": "exam_title, subject_code, total_marks required"}), 400

    exam = TheoryExam(
        exam_title=exam_title,
        subject_code=subject_code,
        total_marks=total_marks,
        faculty_id=user.id,
    )

    db.session.add(exam)
    db.session.commit()

    return (
        jsonify({"message": "Theory exam created successfully", "exam_id": exam.id}),
        201,
    )


# ======================================================
# GET THEORY EXAMS (FACULTY)
# ======================================================
@api.route("/faculty/theory_exams", methods=["GET"])
@jwt_required()
def get_theory_exams():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    exams = TheoryExam.query.filter_by(faculty_id=user_id).all()

    result = []

    for exam in exams:
        result.append(
            {
                "id": exam.id,
                "exam_title": exam.exam_title,
                "subject_code": exam.subject_code,
                "total_marks": exam.total_marks,
                "created_at": exam.created_at,
            }
        )

    return jsonify(result), 200


# ======================================================
# THEORY AI EVALUATION FUNCTION (IMPROVED)
# ======================================================

def evaluate_theory(answer_key_path, student_path):

    # ===============================
    # OCR TEXT EXTRACTION
    # ===============================
    answer_key_text = extract_pdf_text(answer_key_path)
    student_text = extract_pdf_text(student_path)

    # Debug output
    print("\n===== OCR DEBUG =====")
    print("ANSWER KEY SAMPLE:\n", answer_key_text[:300])
    print("STUDENT ANSWER SAMPLE:\n", student_text[:300])
    print("======================\n")

    # Prevent empty OCR
    if not answer_key_text.strip():
        print("⚠️ OCR failed for answer key")
        return None

    if not student_text.strip():
        print("⚠️ OCR failed for student answer")
        return None

    # Limit text length (prevent token overflow)
    answer_key_text = answer_key_text[:3500]
    student_text = student_text[:3500]

    # ===============================
    # AI PROMPT
    # ===============================
    prompt = f"""
You are an expert university professor evaluating exam papers.

Instructions:
- Compare student answers with the answer key
- Score fairly based on correctness
- If answer partially correct give partial marks
- If incorrect give 0
- Do not exceed maximum marks

Return ONLY valid JSON in this format:

{{
 "total_score": number,
 "max_score": number,
 "percent": number,
 "overall_comment": string,
 "per_question":[
   {{
     "q_no":"Q1",
     "max_marks":10,
     "marks_awarded":8,
     "brief_feedback":"Short explanation",
     "confidence":0.8
   }}
 ]
}}

ANSWER KEY:
{answer_key_text}

STUDENT ANSWER:
{student_text}
"""

    # ===============================
    # GROQ AI REQUEST
    # ===============================
    try:

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=1200
        )

        raw = response.choices[0].message.content

        print("\n===== AI RAW RESPONSE =====")
        print(raw)
        print("===========================\n")

        # ===============================
        # CLEAN MARKDOWN
        # ===============================
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Extract JSON safely
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)

        # ===============================
        # PARSE JSON
        # ===============================
        result = json.loads(raw)

        return result

    except json.JSONDecodeError:
        print("❌ JSON Parse Error")
        print(raw)
        return None

    except Exception as e:
        print("❌ AI Evaluation Error:", str(e))
        return None
# ======================================================
# UPLOAD THEORY EXAM FILES
# ======================================================
@api.route("/faculty/upload_theory_files/<int:exam_id>", methods=["POST"])
@jwt_required()
def upload_theory_files(exam_id):

    exam = TheoryExam.query.get(exam_id)

    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    answer_key = request.files.get("answer_key")
    question_paper = request.files.get("question_paper")

    if not answer_key:
        return jsonify({"error": "Answer key required"}), 400

    key_path = os.path.join(THEORY_UPLOAD_FOLDER, f"{exam_id}_key.pdf")
    answer_key.save(key_path)

    exam.answer_key_path = key_path

    if question_paper:
        qp_path = os.path.join(THEORY_UPLOAD_FOLDER, f"{exam_id}_qp.pdf")
        question_paper.save(qp_path)
        exam.question_paper_path = qp_path

    db.session.commit()

    return jsonify({"message": "Files uploaded successfully"})


# ======================================================
# UPLOAD THEORY ANSWER SHEETS (MAX 100)
# ======================================================
@api.route("/faculty/upload_theory_sheets/<int:exam_id>", methods=["POST"])
@jwt_required()
def upload_theory_sheets(exam_id):

    files = request.files.getlist("files")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    if len(files) > 100:
        return jsonify({"error": "Maximum 100 files allowed"}), 400

    uploaded = 0

    for file in files:

        roll = file.filename.split(".")[0]

        path = os.path.join(THEORY_UPLOAD_FOLDER, f"{exam_id}_{file.filename}")

        file.save(path)

        sheet = TheoryAnswerSheet(
            exam_id=exam_id, student_roll=roll, answer_sheet_path=path
        )

        db.session.add(sheet)

        uploaded += 1

    db.session.commit()

    return jsonify(
        {"message": "Sheets uploaded successfully", "uploaded_count": uploaded}
    )


# ======================================================
# EVALUATE THEORY BULK (IMPROVED)
# ======================================================


@api.route("/faculty/evaluate_theory/<int:exam_id>", methods=["POST"])
@jwt_required()
def evaluate_theory_bulk(exam_id):

    # ===============================
    # AUTHORIZATION CHECK
    # ===============================
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    # ===============================
    # EXAM CHECK
    # ===============================
    exam = TheoryExam.query.get(exam_id)

    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    if not exam.answer_key_path:
        return jsonify({"error": "Answer key not uploaded"}), 400

    # ===============================
    # FETCH UNEVALUATED SHEETS
    # ===============================
    sheets = TheoryAnswerSheet.query.filter_by(exam_id=exam_id, evaluated=False).all()

    if not sheets:
        return jsonify({"message": "No sheets to evaluate"}), 404

    evaluated_count = 0
    failed_count = 0

    print(f"Starting evaluation for {len(sheets)} sheets")

    # ===============================
    # EVALUATION LOOP
    # ===============================
    for sheet in sheets:

        try:

            # Prevent duplicate result
            existing = TheoryResult.query.filter_by(
                exam_id=exam_id, student_roll=sheet.student_roll
            ).first()

            if existing:
                print(f"Skipping existing result for {sheet.student_roll}")
                sheet.evaluated = True
                continue

            result_data = evaluate_theory(exam.answer_key_path, sheet.answer_sheet_path)

            if not result_data:
                failed_count += 1
                continue

            result = TheoryResult(
                exam_id=exam_id,
                student_roll=sheet.student_roll,
                total_score=result_data.get("total_score", 0),
                max_score=result_data.get("max_score", 0),
                percent=result_data.get("percent", 0),
                overall_comment=result_data.get("overall_comment", ""),
                result_json=json.dumps(result_data),
            )

            db.session.add(result)

            sheet.evaluated = True

            evaluated_count += 1

            print(f"Evaluated: {sheet.student_roll}")

            # Prevent AI API rate limit
            time.sleep(0.3)

        except Exception as e:

            print("Evaluation error:", str(e))

            failed_count += 1

            continue

    # ===============================
    # FINAL COMMIT
    # ===============================
    db.session.commit()

    return jsonify(
        {
            "message": "Theory evaluation completed",
            "evaluated_successfully": evaluated_count,
            "failed": failed_count,
        }
    )


# ======================================================
# GET THEORY RESULTS
# ======================================================
@api.route("/faculty/theory_results/<int:exam_id>", methods=["GET"])
@jwt_required()
def get_theory_results(exam_id):

    results = TheoryResult.query.filter_by(exam_id=exam_id).all()

    if not results:
        return jsonify({"message": "No results found"}), 404

    total = len(results)
    avg = sum(r.total_score for r in results) / total

    data = []

    for r in results:
        data.append(
            {
                "student_roll": r.student_roll,
                "score": r.total_score,
                "percent": r.percent,
            }
        )

    return jsonify(
        {"total_students": total, "average_score": round(avg, 2), "results": data}
    )


# ======================================================
# STUDENT THEORY RESULT DETAIL
# ======================================================


@api.route("/student/theory_result/<int:exam_id>", methods=["GET"])
@jwt_required()
def student_theory_result(exam_id):

    user_id = int(get_jwt_identity())

    student = Student.query.filter_by(user_id=user_id).first()

    if not student:
        return jsonify({"error": "Student not found"}), 404

    result = TheoryResult.query.filter_by(
        exam_id=exam_id, student_roll=student.roll_number
    ).first()

    if not result:
        return jsonify({"message": "Result not available"}), 404

    analysis = json.loads(result.result_json)

    return jsonify(
        {
            "student_roll": student.roll_number,
            "score": result.total_score,
            "percent": result.percent,
            "overall_comment": result.overall_comment,
            "analysis": analysis.get("per_question", []),
        }
    )


# ======================================================
# CREATE OMR EXAM (FACULTY)
# ======================================================
@api.route("/faculty/create_omr_exam", methods=["POST"])
@jwt_required()
def create_omr_exam():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}

    exam = OMRExam(
        exam_name=data.get("exam_name"),
        total_questions=data.get("total_questions"),
        options_per_question=data.get("options_per_question"),
        marks_per_question=data.get("marks_per_question"),
        faculty_id=user.id,
    )

    db.session.add(exam)
    db.session.commit()

    return jsonify({"message": "OMR Exam created successfully", "exam_id": exam.id})


# ======================================================
# UPLOAD OMR ANSWER KEY
# ======================================================
@api.route("/faculty/upload_answer_key", methods=["POST"])
@jwt_required()
def upload_answer_key():

    data = request.get_json() or {}

    exam_id = data.get("exam_id")
    answers = data.get("answers")

    if not exam_id or not answers:
        return jsonify({"error": "exam_id and answers required"}), 400

    for q_no, option in answers.items():

        key = OMRAnswerKey(
            exam_id=exam_id, question_number=int(q_no), correct_option=option
        )

        db.session.add(key)

    db.session.commit()

    return jsonify({"message": "Answer key uploaded successfully"})


# ======================================================
# UPLOAD MULTIPLE OMR SHEETS (PDF + IMAGE)
# ======================================================


@api.route("/faculty/upload_omr_sheets", methods=["POST"])
@jwt_required()
def upload_omr_sheets():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    exam_id = request.form.get("exam_id")

    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    files = request.files.getlist("sheets")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    uploaded = []

    for file in files:

        filename = file.filename
        ext = filename.split(".")[-1].lower()

        if ext not in ["jpg", "jpeg", "pdf"]:
            return jsonify({"error": "Only JPG PNG jpeg allowed"}), 400

        roll = filename.split(".")[0]

        save_path = os.path.join(OMR_UPLOAD_FOLDER, filename)

        file.save(save_path)

        sheet = OMRSheet(exam_id=exam_id, student_roll=roll, sheet_path=save_path)

        db.session.add(sheet)

        uploaded.append(roll)

    db.session.commit()

    return jsonify({"message": "OMR sheets uploaded", "students": uploaded})


# ======================================================
# EVALUATE OMR SHEETS
# ======================================================


@api.route("/faculty/evaluate_omr", methods=["POST"])
@jwt_required()
def evaluate_omr():

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()

    exam_id = data.get("exam_id")

    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    sheets = OMRSheet.query.filter_by(exam_id=exam_id, evaluated=False).all()

    if not sheets:
        return jsonify({"message": "No sheets to evaluate"}), 404

    results = []

    exam = OMRExam.query.get(exam_id)

    for sheet in sheets:

        existing = OMRResult.query.filter_by(
            exam_id=exam_id,
            student_roll=sheet.student_roll
        ).first()

        if existing:
            sheet.evaluated = True
            continue

        # ⭐ REAL OMR DETECTION
        correct_answers = detect_omr_score(sheet.sheet_path, exam_id)

        score = correct_answers * exam.marks_per_question

        percentage = (score / (exam.total_questions * exam.marks_per_question)) * 100

        if percentage >= 90:
            grade = "A+"
        elif percentage >= 75:
            grade = "A"
        elif percentage >= 60:
            grade = "B"
        elif percentage >= 50:
            grade = "C"
        elif percentage >= 40:
            grade = "D"
        else:
            grade = "F"

        result = OMRResult(
            exam_id=exam_id,
            student_roll=sheet.student_roll,
            score=score,
            percentage=percentage,
            grade=grade,
        )

        db.session.add(result)

        sheet.evaluated = True

        results.append({
            "student_roll": sheet.student_roll,
            "score": score,
            "percentage": percentage,
            "grade": grade
        })

    db.session.commit()

    return jsonify({
        "message": "OMR evaluation completed",
        "results": results
    })


# ======================================================
# GET OMR RESULTS (FACULTY DASHBOARD)
# ======================================================
@api.route("/faculty/omr_results/<int:exam_id>", methods=["GET"])
@jwt_required()
def get_omr_results(exam_id):

    user_id = int(get_jwt_identity())

    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    results = OMRResult.query.filter_by(exam_id=exam_id).all()

    if not results:
        return jsonify({"message": "No results found"}), 404

    total_students = len(results)

    total_score = sum(r.score for r in results)

    average_score = total_score / total_students if total_students > 0 else 0

    pass_count = sum(1 for r in results if r.percentage >= 40)

    fail_count = total_students - pass_count

    student_results = []

    for r in results:
        student_results.append(
            {
                "student_roll": r.student_roll,
                "score": r.score,
                "percentage": r.percentage,
                "grade": r.grade,
            }
        )

    return jsonify(
        {
            "exam_id": exam_id,
            "total_students": total_students,
            "average_score": round(average_score, 2),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "results": student_results,
        }
    )
#===================================================
#  STUDENT MY RESULT
#===================================================
@api.route("/student/my_results", methods=["GET"])
@jwt_required()
def student_my_results():

    user_id = int(get_jwt_identity())

    student = Student.query.filter_by(user_id=user_id).first()

    if not student:
        return jsonify({"error": "Student not found"}), 404

    roll = student.roll_number

    data = []

    theory_results = TheoryResult.query.filter_by(student_roll=roll).all()

    for r in theory_results:

        exam = TheoryExam.query.get(r.exam_id)

        data.append({
            "examId": r.exam_id,
            "examName": exam.exam_title,
            "percent": r.percent,
            "aiFeedback": r.ai_feedback,
            "performance":
                "Excellent" if r.percent >= 80
                else "Average" if r.percent >= 60
                else "Needs Improvement",
            "createdAt": r.created_at.strftime("%Y-%m-%d")
        })

    omr_results = OMRResult.query.filter_by(student_roll=roll).all()

    for r in omr_results:

        exam = OMRExam.query.get(r.exam_id)

        data.append({
            "examId": r.exam_id,
            "examName": exam.exam_name,
            "percent": r.percentage,
            "aiFeedback": "OMR Auto Evaluation",
            "performance":
                "Excellent" if r.percentage >= 80
                else "Average" if r.percentage >= 60
                else "Needs Improvement",
            "createdAt": r.created_at.strftime("%Y-%m-%d")
        })

    return jsonify(data)