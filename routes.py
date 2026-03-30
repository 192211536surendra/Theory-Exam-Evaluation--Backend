# ======================================================
# IMPORTS
# ======================================================

import os
import json
from datetime import datetime

# FLASK
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename

# DATABASE / EXTENSIONS
from extensions import db, bcrypt

# SERVICES
from services.ollama_evaluation import evaluate_answers 
from services.omr_evaluator import evaluate_omr_sheet   # wrapper we added

# JWT AUTH
from flask_jwt_extended import (
    create_access_token,
    jwt_required,
    get_jwt_identity
)

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
    StudentPerformance 

    
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

    # Check duplicate email
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already exists"}), 400

    # Find department
    department = Department.query.filter_by(code=department_code).first()

    if not department:
        return jsonify({"error": "Department not found"}), 404

    hashed_pw = bcrypt.generate_password_hash(DEFAULT_PASSWORD).decode("utf-8")

    try:

        # =================================================
        # CREATE USER
        # =================================================
        user = User(
            full_name=full_name,
            email=email,
            role=role,
            department_id=department.id,
            password=hashed_pw,
            is_active=True
        )

        db.session.add(user)
        db.session.flush()

        # =================================================
        # STUDENT PROFILe
        # =================================================
        if role == "student":

            roll_number = data.get("roll_number")
            semester = data.get("semester")

            if not roll_number or not semester:
                return jsonify({"error": "Roll number and semester required"}), 400

            student = Student(
                user_id=user.id,
                roll_number=roll_number,
                semester=semester,
                cgpa=data.get("cgpa", 0)
            )

            db.session.add(student)

        # =================================================
        # FACULTY PROFILE
        # =================================================
        elif role == "faculty":

            faculty_id = data.get("faculty_id")  # from form
            designation = data.get("designation")
            qualification = data.get("qualification")
            experience_years = data.get("experience_years")

            if not faculty_id:
                faculty_id = f"FAC{user.id:04}"

            faculty = Faculty(
                faculty_id=faculty_id,
                user_id=user.id,
                designation=designation,
                qualification=qualification,
                experience_years=experience_years
            )

            db.session.add(faculty)

            faculty_details = FacultyDetails(
                faculty_id=faculty_id,
                user_id=user.id,
                department_id=department.id,
                designation=designation,
                qualification=qualification,
                experience_years=experience_years
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
    # AGGREGATE RESULTS
    # ===============================
    normal_results = StudentExamResult.query.filter_by(student_id=student.id).all()
    theory_results = TheoryResult.query.filter_by(student_roll=student.roll_number).all()
    omr_results = OMRResult.query.filter_by(student_roll=student.roll_number).all()

    combined_results = []
    
    # Process Normal results
    for r in normal_results:
        exam = Exam.query.get(r.exam_id)
        if exam:
            combined_results.append({
                "exam_id": exam.id,
                "exam_name": exam.exam_name,
                "date": exam.exam_date.strftime("%Y-%m-%d") if exam.exam_date else "N/A",
                "score": r.marks_obtained,
                "percent": r.percentage,
                "status": "Evaluated",
                "type": "normal",
                "timestamp": exam.created_at or datetime.utcnow()
            })

    # Process Theory results
    for r in theory_results:
        exam = TheoryExam.query.get(r.exam_id)
        if exam:
            combined_results.append({
                "exam_id": exam.id,
                "exam_name": exam.exam_title,
                "date": exam.created_at.strftime("%Y-%m-%d") if exam.created_at else "N/A",
                "score": r.total_score,
                "percent": r.percent,
                "status": "Evaluated",
                "type": "theory",
                "timestamp": exam.created_at or datetime.utcnow()
            })

    # Process OMR results
    for r in omr_results:
        exam = OMRExam.query.get(r.exam_id)
        if exam:
            combined_results.append({
                "exam_id": exam.id,
                "exam_name": exam.exam_name,
                "date": exam.created_at.strftime("%Y-%m-%d") if exam.created_at else "N/A",
                "score": r.score,
                "percent": r.percentage,
                "status": "Evaluated",
                "type": "omr",
                "timestamp": exam.created_at or datetime.utcnow()
            })

    # Sort and take last 5
    combined_results.sort(key=lambda x: x['timestamp'], reverse=True)
    recent_exams = combined_results[:5]

    # Stats calculation
    all_scores = [r['percent'] for r in combined_results if r['percent'] > 0]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    exams_taken = len(combined_results)

    # ===============================
    # DASHBOARD RESPONSE
    # ===============================
    dashboard_data = {
        "full_name": user.full_name,
        "student_id": student.roll_number,
        "cgpa": student.cgpa,
        "semester": student.semester,
        "rank": 12, # Placeholder or calculation
        "metrics": {
            "average_score": round(avg_score, 2),
            "exams_completed": exams_taken,
            "exams_pending": 0, # Placeholder
            "improvement": "+6.2%", # Placeholder
            "class_average": 82.3 # Placeholder
        },
        "recent_exams": recent_exams,
    }

    return jsonify(dashboard_data)

@api.route("/student/exams", methods=["GET"])
@jwt_required()
def get_student_exams():
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    student = Student.query.filter_by(user_id=user.id).first()

    if not student:
        return jsonify({"error": "Student profile not found"}), 404

    # 1. FETCH ALL RESULTS (COMPLETED)
    normal_results = StudentExamResult.query.filter_by(student_id=student.id).all()
    theory_results = TheoryResult.query.filter_by(student_roll=student.roll_number).all()
    omr_results = OMRResult.query.filter_by(student_roll=student.roll_number).all()

    completed = []
    completed_exam_ids = {"normal": [], "theory": [], "omr": []}

    for r in normal_results:
        exam = Exam.query.get(r.exam_id)
        if exam:
            completed.append({
                "id": exam.id,
                "name": exam.exam_name,
                "date": exam.exam_date.strftime("%Y-%m-%d") if exam.exam_date else "N/A",
                "score": f"{int(r.marks_obtained)}", # Matches frontend expected display
                "percent": r.percentage,
                "type": "Normal",
                "status": "Evaluated",
                "timestamp": exam.created_at or datetime.utcnow()
            })
            completed_exam_ids["normal"].append(exam.id)

    for r in theory_results:
        exam = TheoryExam.query.get(r.exam_id)
        if exam:
            completed.append({
                "id": exam.id,
                "name": exam.exam_title,
                "date": exam.created_at.strftime("%Y-%m-%d") if exam.created_at else "N/A",
                "score": f"{int(r.total_score)}/{int(r.max_score or 100)}",
                "percent": r.percent,
                "type": "Theory",
                "status": "Evaluated",
                "timestamp": exam.created_at or datetime.utcnow()
            })
            completed_exam_ids["theory"].append(exam.id)

    for r in omr_results:
        exam = OMRExam.query.get(r.exam_id)
        if exam:
            completed.append({
                "id": exam.id,
                "name": exam.exam_name,
                "date": exam.created_at.strftime("%Y-%m-%d") if exam.created_at else "N/A",
                "score": f"{int(r.score)}",
                "percent": r.percentage,
                "type": "OMR",
                "status": "Evaluated",
                "timestamp": exam.created_at or datetime.utcnow()
            })
            completed_exam_ids["omr"].append(exam.id)

    completed.sort(key=lambda x: x['timestamp'], reverse=True)

    # 2. FETCH UPCOMING (NOT IN COMPLETED)
    upcoming = []
    
    all_normal = Exam.query.all()
    all_theory = TheoryExam.query.all()
    all_omr = OMRExam.query.all()

    for e in all_normal:
        if e.id not in completed_exam_ids["normal"]:
            upcoming.append({
                "id": e.id,
                "name": e.exam_name,
                "type": "Normal",
                "date": e.exam_date.strftime("%Y-%m-%d") if e.exam_date else "N/A",
                "duration": f"{e.duration} mins" if e.duration else "N/A",
                "status": "Scheduled"
            })

    for e in all_theory:
        if e.id not in completed_exam_ids["theory"]:
            upcoming.append({
                "id": e.id,
                "name": e.exam_title,
                "type": "Theory",
                "date": e.created_at.strftime("%Y-%m-%d") if e.created_at else "N/A",
                "duration": "N/A",
                "status": "Scheduled"
            })

    for e in all_omr:
        if e.id not in completed_exam_ids["omr"]:
            upcoming.append({
                "id": e.id,
                "name": e.exam_name,
                "type": "OMR",
                "date": e.created_at.strftime("%Y-%m-%d") if e.created_at else "N/A",
                "duration": "N/A",
                "status": "Scheduled"
            })

    return jsonify({
        "completed": completed,
        "upcoming": upcoming
    })
@api.route("/faculty/exams", methods=["GET"])
@jwt_required()
def get_all_exams():

    user_id = int(get_jwt_identity())

    omr_exams = OMRExam.query.filter_by(faculty_id=user_id).all()
    theory_exams = TheoryExam.query.filter_by(faculty_id=user_id).all()

    exams = []

    for e in omr_exams:
        # Calculate status
        sheets_count = OMRSheet.query.filter_by(exam_id=e.id).count()
        results_count = OMRResult.query.filter_by(exam_id=e.id).count()
        
        status = "Pending"
        if results_count > 0:
            status = "Evaluated"
        elif sheets_count > 0:
            status = "Ready for Evaluation"

        exams.append({
            "id": e.id,
            "exam_id": e.id,
            "exam_name": e.exam_name,
            "exam_type": "OMR",
            "type": "OMR",
            "subject_code": e.subject_code or "N/A",
            "exam_date": e.exam_date.strftime("%Y-%m-%d") if e.exam_date else "N/A",
            "duration": e.duration or 0,
            "created_at": e.created_at.strftime("%Y-%m-%d") if e.created_at else "N/A",
            "status": status,
            "sheets_count": sheets_count,
            "results_count": results_count
        })

    for e in theory_exams:
        # Calculate status
        sheets_count = TheoryAnswerSheet.query.filter_by(exam_id=e.id).count()
        results_count = TheoryResult.query.filter_by(exam_id=e.id).count()
        
        status = "Pending"
        if results_count > 0:
            status = "Evaluated"
        elif sheets_count > 0:
            status = "Ready for Evaluation"

        exams.append({
            "id": e.id,
            "exam_id": e.id,
            "exam_name": e.exam_title,
            "exam_type": "THEORY",
            "type": "THEORY",
            "subject_code": e.subject_code or "N/A",
            "exam_date": e.exam_date.strftime("%Y-%m-%d") if e.exam_date else "N/A",
            "duration": e.duration or 0,
            "created_at": e.created_at.strftime("%Y-%m-%d") if e.created_at else "N/A",
            "status": status,
            "sheets_count": sheets_count,
            "results_count": results_count
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

    data = request.get_json() or {}

    exam_type = data.get("exam_type", "").upper()

    try:
        exam_date_str = data.get("exam_date")
        exam_date = None
        if exam_date_str:
            try:
                # Convert string 'YYYY-MM-DD' to date object
                exam_date = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                exam_date = None

        duration = data.get("duration")
        try:
            duration = int(duration) if duration else 0
        except (ValueError, TypeError):
            duration = 0

        if exam_type == "OMR":
            exam = OMRExam(
                exam_name=data.get("exam_name"),
                subject_code=data.get("subject_code") or "N/A",
                exam_date=exam_date,
                duration=duration,
                total_questions=data.get("total_questions"),
                options_per_question=data.get("options_per_question", 4),
                marks_per_question=data.get("marks_per_question") or data.get("marksPerQuestion"),
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

            total_marks = data.get("total_marks")
            if total_marks is not None:
                try:
                    total_marks = float(total_marks)
                except (ValueError, TypeError):
                    total_marks = None

            marks_per_q = data.get("marks_per_question")
            if marks_per_q is not None:
                try:
                    marks_per_q = float(marks_per_q)
                except (ValueError, TypeError):
                    marks_per_q = 0

            exam = TheoryExam(
                exam_title=data.get("exam_name"),
                subject_code=data.get("subject_code") or "N/A",
                exam_date=exam_date,
                duration=duration,
                total_marks=total_marks,
                marks_per_question=marks_per_q,
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

@api.route("/faculty/delete_exam", methods=["DELETE"])
@jwt_required()
def delete_exam():
    user_id = int(get_jwt_identity())
    exam_id = request.args.get("exam_id")
    exam_type = request.args.get("exam_type", "").upper()

    if not exam_id or not exam_type:
        return jsonify({"error": "exam_id and exam_type required"}), 400

    try:
        if exam_type == "OMR":
            exam = OMRExam.query.get(exam_id)
            if not exam or exam.faculty_id != user_id:
                return jsonify({"error": "Exam not found or unauthorized"}), 404
            db.session.delete(exam)
        elif exam_type == "THEORY":
            exam = TheoryExam.query.get(exam_id)
            if not exam or exam.faculty_id != user_id:
                return jsonify({"error": "Exam not found or unauthorized"}), 404
            db.session.delete(exam)
        else:
            return jsonify({"error": "Invalid exam type"}), 400

        db.session.commit()
        return jsonify({"message": f"{exam_type} exam deleted successfully"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
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

    # Accept both naming styles
    exam_title = data.get("exam_title") or data.get("exam_name")
    subject_code = data.get("subject_code")
    total_marks = data.get("total_marks") or data.get("max_marks")

    if not exam_title or not subject_code or not total_marks:
        return jsonify({
            "error": "exam_title/exam_name, subject_code, total_marks/max_marks required"
        }), 400

    # Convert marks to integer safely
    try:
        total_marks = int(total_marks)
    except:
        return jsonify({"error": "total_marks must be a number"}), 400

    exam = TheoryExam(
        exam_title=exam_title,
        subject_code=subject_code,
        total_marks=total_marks,
        faculty_id=user.id,
    )

    db.session.add(exam)
    db.session.commit()

    return jsonify({
        "message": "Theory exam created successfully",
        "exam_id": exam.id
    }), 201


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

    # ensure folder exists
    os.makedirs(THEORY_UPLOAD_FOLDER, exist_ok=True)

    # validate file type
    if not answer_key.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Answer key must be PDF"}), 400

    key_path = os.path.join(THEORY_UPLOAD_FOLDER, f"{exam_id}_key.pdf")
    answer_key.save(key_path)

    exam.answer_key_path = key_path

    if question_paper:

        if not question_paper.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Question paper must be PDF"}), 400

        qp_path = os.path.join(THEORY_UPLOAD_FOLDER, f"{exam_id}_qp.pdf")

        question_paper.save(qp_path)

        exam.question_paper_path = qp_path

    db.session.commit()

    return jsonify({
        "message": "Files uploaded successfully",
        "answer_key_path": exam.answer_key_path,
        "question_paper_path": exam.question_paper_path
    })

# ======================================================
# UPLOAD THEORY ANSWER SHEETS (MAX 100)
# =====================================================

@api.route("/faculty/upload_theory_sheets/<int:exam_id>", methods=["POST"])
@jwt_required()
def upload_theory_sheets(exam_id):

    exam = TheoryExam.query.get(exam_id)

    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    files = request.files.getlist("files")

    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    if len(files) > 100:
        return jsonify({"error": "Maximum 100 files allowed"}), 400

    uploaded = 0

    for file in files:

        filename = secure_filename(file.filename)

        # extract roll number
        roll = os.path.splitext(filename)[0]

        # check duplicate
        existing = TheoryAnswerSheet.query.filter_by(
            exam_id=exam_id,
            student_roll=roll
        ).first()

        if existing:
            continue

        path = os.path.join(
            THEORY_UPLOAD_FOLDER,
            f"{exam_id}_{filename}"
        )

        file.save(path)

        sheet = TheoryAnswerSheet(
            exam_id=exam_id,
            student_roll=roll,
            answer_sheet_path=path
        )

        db.session.add(sheet)

        uploaded += 1

    db.session.commit()

    return jsonify({
        "message": "Sheets uploaded successfully",
        "uploaded_count": uploaded
    })



# ======================================================
# FACULTY THEORY EVALUATION — UPDATED ROUTE
# Replace your existing evaluate_theory_combined route
# in routes.py with this code
# ======================================================

@api.route("/faculty/evaluate_theory_combined", methods=["POST"])
@jwt_required()
def evaluate_theory_combined():

    try:
        print("\n--- [START] COMBINED EVALUATION ---")

        # ── Auth check ────────────────────────────────────────
        user_id = int(get_jwt_identity())
        user    = User.query.get(user_id)

        if not user or user.role != "faculty":
            return jsonify({"error": "Unauthorized"}), 403

        # ── Get exam_id ───────────────────────────────────────
        exam_id_raw = request.form.get("exam_id")

        if not exam_id_raw:
            return jsonify({"error": "exam_id required"}), 400

        try:
            exam_id = int(exam_id_raw)
        except:
            return jsonify({"error": "Invalid exam_id"}), 400

        exam = TheoryExam.query.get(exam_id)

        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        # ── Receive uploaded files ────────────────────────────
        # Frontend sends:
        #   qp_pdf       → question paper (optional)
        #   ma_pdf       → model answer / answer key (required)
        #   student_pdfs → one or more student answer sheets (required)

        qp_file       = request.files.get("qp_pdf")
        ma_file       = request.files.get("ma_pdf")
        student_files = request.files.getlist("student_pdfs")

        # ── Validate required files ───────────────────────────
        if not ma_file:
            return jsonify({"error": "Model answer (ma_pdf) is required"}), 400

        if not student_files or student_files[0].filename == "":
            return jsonify({"error": "At least one student PDF is required"}), 400

        if len(student_files) > 100:
            return jsonify({"error": "Maximum 100 student sheets allowed"}), 400

        # ── Create folder structure ───────────────────────────
        # uploads/theory/exam_<id>/sheets/
        exam_folder   = os.path.join(THEORY_UPLOAD_FOLDER, f"exam_{exam_id}")
        sheets_folder = os.path.join(exam_folder, "sheets")

        os.makedirs(sheets_folder, exist_ok=True)

        # ── Save question paper (optional) ────────────────────
        qp_path = None
        if qp_file and qp_file.filename != "":
            if not qp_file.filename.lower().endswith(".pdf"):
                return jsonify({"error": "Question paper must be a PDF"}), 400
            qp_path = os.path.join(exam_folder, secure_filename(qp_file.filename))
            qp_file.save(qp_path)
            exam.question_paper_path = qp_path
            print(f"  Question paper saved: {qp_path}")

        # ── Save model answer / answer key (required) ─────────
        if not ma_file.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Model answer must be a PDF"}), 400

        ma_path = os.path.join(exam_folder, secure_filename(ma_file.filename))
        ma_file.save(ma_path)
        exam.answer_key_path = ma_path
        print(f"  Model answer saved: {ma_path}")

        db.session.commit()

        # ── Verify answer key saved correctly ─────────────────
        if not os.path.exists(ma_path):
            return jsonify({"error": "Model answer file failed to save"}), 500

        # ── Pre-extract Answer Key and Question Paper text (Optimization) ──
        print("  Pre-extracting master files ...")
        from services.ollama_evaluation import read_pdf
        
        key_text = read_pdf(ma_path, label="key", max_pages=6)
        qp_text = ""
        if qp_path:
            qp_text = read_pdf(qp_path, label="qp", max_pages=4)

        # ── Process each student sheet ────────────────────────
        results_summary  = []
        evaluated_count  = 0
        failed_count     = 0

        for file in student_files:

            if file.filename == "":
                continue

            if not file.filename.lower().endswith(".pdf"):
                failed_count += 1
                results_summary.append({
                    "roll":   file.filename,
                    "status": "Failed",
                    "error":  "Only PDF files accepted"
                })
                continue

            try:
                filename    = secure_filename(file.filename)
                sheet_path  = os.path.join(sheets_folder, filename)

                # Save student sheet to disk
                file.save(sheet_path)

                # Roll number = filename without extension (e.g. CS001.pdf → CS001)
                roll_number = os.path.splitext(filename)[0].upper()

                print(f"\n===== STUDENT: {roll_number} =====")

                # ── Remove old sheet record if exists ─────────
                existing_sheet = TheoryAnswerSheet.query.filter_by(
                    exam_id      = exam_id,
                    student_roll = roll_number
                ).first()

                if existing_sheet:
                    db.session.delete(existing_sheet)
                    db.session.flush()

                # ── Remove old result if exists ───────────────
                existing_result = TheoryResult.query.filter_by(
                    exam_id      = exam_id,
                    student_roll = roll_number
                ).first()

                if existing_result:
                    db.session.delete(existing_result)
                    db.session.flush()

                # ── Save new sheet record ─────────────────────
                sheet = TheoryAnswerSheet(
                    exam_id           = exam_id,
                    student_roll      = roll_number,
                    answer_sheet_path = sheet_path,
                    evaluated         = True
                )
                db.session.add(sheet)

                # ── Verify files exist before evaluation ──────
                if not os.path.exists(sheet_path):
                    raise Exception(f"Student sheet not saved: {sheet_path}")

                if not os.path.exists(ma_path):
                    raise Exception("Answer key not found on disk")

                # ── Call evaluate_answers() ───────────────────
                # This handles:
                #   1. Reading student sheet OCR via Ollama
                #   2. Grade and return result JSON
                # Using pre-extracted key_text and qp_text to save time.

                print(f"  Answer key  : {ma_path}")
                print(f"  Student PDF : {sheet_path}")
                print(f"  Question P  : {qp_path or 'Not provided'}")

                result_data, total_tokens = evaluate_answers(
                    answer_key_path     = ma_path,
                    student_answer_path = sheet_path,
                    question_paper_path = qp_path,  # None if not uploaded
                    key_text            = key_text,
                    qp_text             = qp_text
                )

                print(f"  Tokens used : {total_tokens}")

                # ── Get student name from DB ───────────────────
                student      = Student.query.filter_by(roll_number=roll_number).first()
                student_name = "Unknown"

                if student:
                    user_obj = User.query.get(student.user_id)
                    if user_obj:
                        student_name = user_obj.full_name

                # ── Save result to DB ─────────────────────────
                result = TheoryResult(
                    exam_id         = exam_id,
                    student_roll    = roll_number,
                    full_name       = student_name,
                    total_score     = result_data["total_score"],
                    max_score       = result_data["max_score"],
                    percent         = result_data["percent"],
                    overall_comment = result_data["overall_comment"],
                    result_json     = json.dumps(result_data)
                )

                db.session.add(result)
                evaluated_count += 1

                # ── Terminal log ──────────────────────────────
                status = "PASS" if result_data["percent"] >= 50 else "FAIL"

                print(f"  Student  : {student_name}")
                print(f"  Roll     : {roll_number}")
                print(f"  Score    : {result_data['total_score']} / {result_data['max_score']}")
                print(f"  Percent  : {result_data['percent']}%")
                print(f"  Status   : {status}")

                results_summary.append({
                    "roll":    roll_number,
                    "name":    student_name,
                    "score":   result_data["total_score"],
                    "percent": result_data["percent"],
                    "status":  "Success"
                })

            except Exception as e:
                print(f"  ERROR evaluating {file.filename}: {str(e)}")
                import traceback
                traceback.print_exc()

                failed_count += 1
                results_summary.append({
                    "roll":   file.filename,
                    "status": "Failed",
                    "error":  str(e)
                })

        # ── Commit all results ────────────────────────────────
        db.session.commit()

        print(f"\n--- [END] Success={evaluated_count}, Failed={failed_count} ---")

        return jsonify({
            "message":                "Evaluation completed",
            "total_uploaded":         len(student_files),
            "evaluated_successfully": evaluated_count,
            "failed":                 failed_count,
            "results":                results_summary
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"CRITICAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
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
    pass_count = 0
    fail_count = 0

    for r in results:
        # Prioritize full_name from table, fallback to student/user lookup
        student_name = r.full_name
        if not student_name:
            student = Student.query.filter_by(roll_number=r.student_roll).first()
            if student:
                user = User.query.get(student.user_id)
                if user:
                    student_name = user.full_name
        
        if not student_name:
            student_name = "Unknown"

        grade = "A+" if r.percent >= 90 else "A" if r.percent >= 75 else "B" if r.percent >= 60 else "C" if r.percent >= 50 else "F"
        status = "Passed" if r.percent >= 50 else "Failed"

        if status == "Passed":
            pass_count += 1
        else:
            fail_count += 1

        data.append({
            "student_roll": r.student_roll,
            "student_name": student_name,
            "score": r.total_score,
            "max_marks": r.max_score,
            "percentage": r.percent,
            "grade": grade,
            "status": status
        })

    return jsonify({
        "total_students": total,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "average_score": round(avg, 2),
        "results": data
    })


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

    # get user name
    user = User.query.get(user_id)

    result = TheoryResult.query.filter_by(
        exam_id=exam_id,
        student_roll=student.roll_number
    ).first()

    if not result:
        return jsonify({"message": "Result not available"}), 404

    # safe JSON parsing
    analysis = []
    if result.result_json:
        try:
            analysis = json.loads(result.result_json).get("per_question", [])
        except:
            analysis = []

    # Fetch Exam details
    exam = TheoryExam.query.get(exam_id)

    # Calculate Rank and Class Metrics
    all_results = TheoryResult.query.filter_by(exam_id=exam_id).order_by(TheoryResult.total_score.desc()).all()
    
    total_students = len(all_results)
    student_rank = 0
    scores = [r.percent for r in all_results]
    highest_score = max(scores) if scores else 0
    avg_score = sum(scores) / total_students if total_students > 0 else 0

    for i, r in enumerate(all_results):
        if r.student_roll == student.roll_number:
            student_rank = i + 1
            break
    
    # Percentile calculation
    percentile = 0
    if total_students > 1:
        percentile = ((total_students - student_rank) / (total_students - 1)) * 100
    elif total_students == 1:
        percentile = 100

    return jsonify({
        "exam_title": exam.exam_title if exam else "N/A",
        "subject_code": exam.subject_code if exam else "N/A",
        "date": exam.created_at.strftime("%b %d, %Y") if exam and exam.created_at else "N/A",
        "student_name": user.full_name if user else "Unknown",
        "student_roll": student.roll_number,
        "score": result.total_score,
        "max_score": result.max_score or (exam.total_marks if exam else 100),
        "percent": result.percent,
        "grade": "A+" if result.percent >= 90 else "A" if result.percent >= 75 else "B" if result.percent >= 60 else "C" if result.percent >= 50 else "F",
        "rank": student_rank,
        "total_students": total_students,
        "percentile": f"{round(percentile, 1)}th",
        "class_average": round(avg_score, 1),
        "highest_score": round(highest_score, 1),
        "overall_comment": result.overall_comment,
        "questions": analysis
    })

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
# UPDATE OMR EXAM STRUCTURE (FACULTY)
# ======================================================
@api.route("/faculty/update_omr_exam/<int:exam_id>", methods=["PUT"])
@jwt_required()
def update_omr_exam(exam_id):
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    exam = OMRExam.query.get(exam_id)
    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    data = request.get_json() or {}
    
    if "total_questions" in data:
        exam.total_questions = data["total_questions"]
    if "options_per_question" in data:
        exam.options_per_question = data["options_per_question"]
    if "marks_per_question" in data:
        exam.marks_per_question = data["marks_per_question"]

    db.session.commit()
    return jsonify({"message": "OMR structure updated successfully"})
@api.route("/faculty/update_theory_exam/<int:exam_id>", methods=["PUT"])
@jwt_required()
def update_theory_exam(exam_id):
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    exam = TheoryExam.query.get(exam_id)
    if not exam:
        return jsonify({"error": "Exam not found"}), 404

    data = request.get_json() or {}
    
    if "total_marks" in data:
        exam.total_marks = float(data["total_marks"])
    if "marks_per_question" in data:
        exam.marks_per_question = float(data["marks_per_question"])
    if "subject_code" in data:
        exam.subject_code = data["subject_code"]

    db.session.commit()
    return jsonify({"message": "Theory structure updated successfully"})


# ======================================================
# UPLOAD OMR ANSWER KEY (FIXED VERSION)
# ======================================================
@api.route("/faculty/upload_answer_key", methods=["POST"])
@jwt_required()
def upload_answer_key():

    data = request.get_json() or {}

    exam_id = data.get("exam_id")
    answers = data.get("answers")

    if not exam_id or not answers:
        return jsonify({"error": "exam_id and answers required"}), 400

    # ==========================================
    # REMOVE OLD ANSWER KEYS
    # ==========================================
    OMRAnswerKey.query.filter_by(exam_id=exam_id).delete()

    # ==========================================
    # MAPPING (IMPORTANT FIX)
    # ==========================================
    mapping = {
        "0": "A", "1": "B", "2": "C", "3": "D",
        0: "A", 1: "B", 2: "C", 3: "D",
        "A": "A", "B": "B", "C": "C", "D": "D"
    }

    # ==========================================
    # INSERT NEW ANSWER KEYS
    # ==========================================
    if isinstance(answers, dict):
        for q_no, option in answers.items():

            option = str(option).strip().upper()

            # Convert number → letter
            option = mapping.get(option, option)

            key = OMRAnswerKey(
                exam_id=exam_id,
                question_number=int(q_no),
                correct_option=option
            )
            db.session.add(key)

            # DEBUG (optional)
            print(f"Q{q_no} -> {option}")

    else:
        # For list format
        for i, option in enumerate(answers):

            option = str(option).strip().upper()

            option = mapping.get(option, option)

            key = OMRAnswerKey(
                exam_id=exam_id,
                question_number=i + 1,
                correct_option=option
            )
            db.session.add(key)

            # DEBUG (optional)
            print(f"Q{i+1} -> {option}")

    db.session.commit()

    return jsonify({
        "message": "Answer key uploaded successfully",
        "total_questions": len(answers)
    })

# ======================================================
# UPLOAD MULTIPLE OMR SHEETS (PDF + IMAGE)
# ======================================================


# ======================================================
# UPLOAD OMR SHEETS
# ======================================================
@api.route("/faculty/upload_omr_sheets", methods=["POST"])
@jwt_required()
def upload_omr_sheets():

    exam_id = request.form.get("exam_id")

    if not exam_id:
        return jsonify({"error": "exam_id required"}), 400

    exam = OMRExam.query.get(exam_id)

    if not exam:
        return jsonify({"error": "Exam not found"}), 404


    # IMPORTANT: read files from request
    files = request.files.getlist("files")

    print("FILES RECEIVED:", request.files)

    if not files or files[0].filename == "":
        return jsonify({"error": "No files uploaded"}), 400


    # ── Create exam-specific folder (Isolation Fix) ──────────
    exam_folder = os.path.join(OMR_UPLOAD_FOLDER, f"exam_{exam_id}")
    os.makedirs(exam_folder, exist_ok=True)

    uploaded = []

    for file in files:

        filename = secure_filename(file.filename)
        save_path = os.path.join(exam_folder, filename)

        file.save(save_path)

        # Remove old sheet if exists for this roll number and exam
        roll_number = os.path.splitext(filename)[0].upper()
        existing = OMRSheet.query.filter_by(exam_id=exam_id, sheet_path=save_path).first()
        if existing:
            db.session.delete(existing)

        sheet = OMRSheet(
            exam_id=exam_id,
            sheet_path=save_path
        )

        db.session.add(sheet)
        uploaded.append(filename)

    db.session.commit()

    return jsonify({
        "message": f"OMR sheets uploaded successfully to exam_{exam_id}",
        "uploaded_files": uploaded
    }), 200
#===========================================================
# EVALUATE_OMR
#===============================================
@api.route("/faculty/evaluate_omr", methods=["POST"])
@jwt_required()
def evaluate_omr():

    try:
        user_id = int(get_jwt_identity())
        faculty_user = User.query.get(user_id)

        if not faculty_user or faculty_user.role != "faculty":
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        exam_id = data.get("exam_id")

        if not exam_id:
            return jsonify({"error": "exam_id required"}), 400

        exam = OMRExam.query.get(exam_id)

        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        sheets = OMRSheet.query.filter_by(exam_id=exam_id).all()

        if not sheets:
            return jsonify({"message": "No sheets to evaluate"}), 404

        # ==========================================
        # FETCH ANSWER KEY
        # ==========================================
        keys = OMRAnswerKey.query.filter_by(exam_id=exam_id).all()

        if not keys:
            return jsonify({"error": "Answer key not uploaded"}), 400

        exam_folder = os.path.join(OMR_UPLOAD_FOLDER, f"exam_{exam_id}")
        os.makedirs(exam_folder, exist_ok=True)

        key_path = os.path.join(exam_folder, f"key_{exam_id}.txt")

        with open(key_path, "w") as f:
            for k in keys:
                f.write(f"{k.question_number}:{k.correct_option}\n")

        print("\n========== OMR EVALUATION STARTED ==========\n")

        results = []

        # ==========================================
        # PROCESS EACH SHEET
        # ==========================================
        for sheet in sheets:

            try:
                print(f"Processing Sheet: {sheet.sheet_path}")

                # STEP 1: Evaluate OMR sheet
                result = evaluate_omr_sheet(
                    key_path,
                    sheet.sheet_path
                )

                # STEP 2: Extract roll number cleanly
                raw_roll = str(
                    result.get("roll_number") or
                    os.path.splitext(os.path.basename(sheet.sheet_path))[0]
                ).upper().strip()

                # Fix: avoid double "CS" prefix like CSCS001
                if raw_roll.startswith("CS"):
                    roll = raw_roll
                else:
                    roll = "CS" + raw_roll

                print(f"DEBUG raw_roll  : {raw_roll}")
                print(f"DEBUG final roll: {roll}")

                # STEP 3: Check duplicate
                existing = OMRResult.query.filter_by(
                    exam_id=exam_id,
                    student_roll=roll
                ).first()

                if existing:
                    print(f"Skipping {roll} (already evaluated)\n")
                    continue

                correct = int(result.get("correct", 0))

                # ==================================
                # SCORE
                # ==================================
                score = correct * exam.marks_per_question
                total = exam.total_questions * exam.marks_per_question
                percentage = round((score / total) * 100, 2) if total > 0 else 0

                # ==================================
                # STATUS & GRADE
                # ==================================
                status = "PASS" if percentage >= 50 else "FAIL"

                if percentage >= 90:
                    grade = "A+"
                elif percentage >= 75:
                    grade = "A"
                elif percentage >= 60:
                    grade = "B"
                elif percentage >= 50:
                    grade = "C"
                else:
                    grade = "F"

                # ==================================
                # STEP 4: Fetch student name from DB
                # ==================================
                student_name = "Unknown"

                student = Student.query.filter_by(roll_number=roll).first()
                print(f"DEBUG student found: {student}")

                if student:
                    user_obj = User.query.get(student.user_id)
                    print(f"DEBUG user_obj found: {user_obj}")
                    if user_obj:
                        student_name = user_obj.full_name
                        print(f"DEBUG student name: {student_name}")
                else:
                    # Try without CS prefix as fallback
                    fallback_roll = raw_roll.replace("CS", "").strip()
                    print(f"DEBUG trying fallback roll: {fallback_roll}")
                    student = Student.query.filter(
                        Student.roll_number.ilike(f"%{fallback_roll}%")
                    ).first()
                    if student:
                        user_obj = User.query.get(student.user_id)
                        if user_obj:
                            student_name = user_obj.full_name
                            roll = student.roll_number  # use exact roll from DB
                            print(f"DEBUG fallback name found: {student_name}")

                # ==================================
                # STEP 5: Save result
                # ==================================
                result_db = OMRResult(
                    exam_id=exam_id,
                    student_roll=roll,
                    full_name=student_name,
                    score=score,
                    percentage=percentage,
                    grade=grade
                )

                db.session.add(result_db)

                results.append({
                    "roll": roll,
                    "name": student_name,
                    "score": score,
                    "percentage": percentage,
                    "grade": grade,
                    "status": status
                })

                # ==================================
                # TERMINAL OUTPUT
                # ==================================
                print("----- RESULT -----")
                print(f"Student Name : {student_name}")
                print(f"Roll Number  : {roll}")
                print(f"Correct Ans  : {correct}")
                print(f"Score        : {score}")
                print(f"Percentage   : {percentage}%")
                print(f"Grade        : {grade}")
                print(f"Status       : {status}")
                print("------------------\n")

            except Exception as e:
                print("OMR Evaluation Error:", str(e))
                db.session.rollback()
                continue

        db.session.commit()

        if os.path.exists(key_path):
            os.remove(key_path)

        print("========== OMR EVALUATION COMPLETED ==========\n")

        return jsonify({
            "message": "OMR evaluation completed",
            "results": results
        })

    except Exception as e:
        db.session.rollback()
        print("CRITICAL ERROR:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
            "aiFeedback": r.overall_comment,
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
#===================================================
#  THEORY RESULT DELECT
#===========================================================
@api.route("/faculty/delete_theory_result/<int:exam_id>/<string:roll>", methods=["DELETE"])
@jwt_required()
def delete_theory_result(exam_id, roll):

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    result = TheoryResult.query.filter_by(
        exam_id=exam_id,
        student_roll=roll
    ).first()

    if not result:
        return jsonify({"error": "Result not found"}), 404

    db.session.delete(result)

    # reset answer sheet evaluation
    sheet = TheoryAnswerSheet.query.filter_by(
        exam_id=exam_id,
        student_roll=roll
    ).first()

    if sheet:
        sheet.evaluated = False

    db.session.commit()

    return jsonify({"message": "Result deleted successfully"})
#+================================================
# OMR RESULT DELETE
#=====================================================
@api.route("/faculty/delete_omr_result/<int:exam_id>/<string:roll>", methods=["DELETE"])
@jwt_required()
def delete_omr_result(exam_id, roll):

    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    result = OMRResult.query.filter_by(
        exam_id=exam_id,
        student_roll=roll
    ).first()

    if not result:
        return jsonify({"error": "Result not found"}), 404

    db.session.delete(result)

    sheet = OMRSheet.query.filter_by(
        exam_id=exam_id,
        student_roll=roll
    ).first()

    if sheet:
        sheet.evaluated = False

    db.session.commit()

    return jsonify({"message": "Result deleted successfully"})
# ======================================================
# STUDENT PERFORMANCE ANALYTICS
# ======================================================
@api.route("/student/performance_analytics", methods=["GET"])
@jwt_required()
def student_performance_analytics():

    from datetime import datetime

    user_id = int(get_jwt_identity())

    # =========================
    # GET STUDENT
    # =========================
    student = Student.query.filter_by(user_id=user_id).first()

    if not student:
        return jsonify({"error": "Student not found"}), 404

    roll = student.roll_number

    # =========================
    # FETCH RESULTS
    # =========================
    theory_results = TheoryResult.query.filter_by(student_roll=roll).all()
    omr_results = OMRResult.query.filter_by(student_roll=roll).all()

    results = []

    for r in theory_results:
        results.append((r.percent, r.created_at))

    for r in omr_results:
        results.append((r.percentage, r.created_at))

    # =========================
    # NO RESULTS
    # =========================
    if not results:
        return jsonify({
            "average": 0,
            "highest": 0,
            "lowest": 0,
            "performance": "No Data",
            "month": {"average":0,"highest":0,"lowest":0},
            "semester": {"average":0,"highest":0,"lowest":0},
            "year": {"average":0,"highest":0,"lowest":0},
            "trend":[]
        })

    # =========================
    # OVERALL ANALYTICS
    # =========================
    scores = [r[0] for r in results]

    avg = sum(scores) / len(scores)
    highest = max(scores)
    lowest = min(scores)

    # Performance label
    if avg >= 80:
        performance = "Excellent"
    elif avg >= 60:
        performance = "Good"
    else:
        performance = "Needs Improvement"

    # =========================
    # MONTH ANALYSIS
    # =========================
    now = datetime.utcnow()

    month_scores = [
        s for s, d in results
        if d.month == now.month and d.year == now.year
    ]

    # =========================
    # YEAR ANALYSIS
    # =========================
    year_scores = [
        s for s, d in results
        if d.year == now.year
    ]

    # =========================
    # SEMESTER ANALYSIS
    # =========================
    if student.semester % 2 == 1:
        sem_months = [6,7,8,9,10,11]
    else:
        sem_months = [12,1,2,3,4,5]

    semester_scores = [
        s for s, d in results
        if d.month in sem_months
    ]

    # =========================
    # CALCULATION FUNCTION
    # =========================
    def calc(data):
        if not data:
            return {"average":0,"highest":0,"lowest":0}

        return {
            "average": round(sum(data)/len(data),2),
            "highest": max(data),
            "lowest": min(data)
        }

    # =========================
    # TREND GRAPH DATA
    # =========================
    trend = []

    for i in range(1,13):

        monthly = [s for s,d in results if d.month == i]

        if monthly:
            trend.append({
                "month": i,
                "percent": round(sum(monthly)/len(monthly),2)
            })

    # =========================
    # SAVE PERFORMANCE SNAPSHOT
    # =========================
    perf = StudentPerformance(
        student_roll=roll,
        average_score=round(avg, 2),
        highest_score=highest,
        lowest_score=lowest,
        performance_label=performance
    )

    db.session.add(perf)
    db.session.commit()

    # =========================
    # FINAL RESPONSE
    # =========================
    return jsonify({

        "average": round(avg,2),
        "highest": highest,
        "lowest": lowest,
        "performance": performance,

        "month": calc(month_scores),
        "semester": calc(semester_scores),
        "year": calc(year_scores),

        "trend": trend
    })

# ======================================================
# COMBINED OMR EVALUATION (FACULTY)
# ======================================================
@api.route("/faculty/evaluate_omr_combined", methods=["POST"])
@jwt_required()
def evaluate_omr_combined():

    try:

        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)

        if not user or user.role != "faculty":
            return jsonify({"error": "Unauthorized"}), 403

        exam_id = request.form.get("exam_id")

        if not exam_id:
            return jsonify({"error": "exam_id required"}), 400

        exam_id = int(exam_id)

        exam = OMRExam.query.get(exam_id)

        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        files = request.files.getlist("student_omrs")

        if not files:
            return jsonify({"error": "No OMR sheets uploaded"}), 400


        # ======================================
        # FETCH ANSWER KEY
        # ======================================

        keys = OMRAnswerKey.query.filter_by(exam_id=exam_id).all()

        if not keys:
            return jsonify({"error": "Answer key not found"}), 400

        answer_key_text = ""

        for k in keys:
            answer_key_text += f"{k.question_number}:{k.correct_option}\n"


        key_path = os.path.join(OMR_UPLOAD_FOLDER, f"key_{exam_id}.txt")

        os.makedirs(OMR_UPLOAD_FOLDER, exist_ok=True)

        with open(key_path, "w") as f:
            f.write(answer_key_text)


        results_summary = []


        # ======================================
        # PROCESS EACH OMR SHEET
        # ======================================

        for file in files:

            if file.filename == "":
                continue

            filename = secure_filename(file.filename)

            save_path = os.path.join(OMR_UPLOAD_FOLDER, filename)

            file.save(save_path)


            # Save Sheet Record
            sheet = OMRSheet(
                exam_id=exam_id,
                sheet_path=save_path,
                evaluated=True
            )

            db.session.add(sheet)


            # ======================================
            # OMR EVALUATION (OpenCV)
            # ======================================

            ai_result = evaluate_omr_sheet(key_path, save_path)

            roll = ai_result.get("roll_number")

            if not roll:
                roll = os.path.splitext(filename)[0].upper()


            correct_count = int(ai_result.get("correct", 0))

            score = correct_count * exam.marks_per_question

            total_possible = exam.total_questions * exam.marks_per_question

            percentage = (score / total_possible * 100) if total_possible > 0 else 0


            # ======================================
            # GRADE
            # ======================================

            if percentage >= 90:
                grade = "A+"
            elif percentage >= 75:
                grade = "A"
            elif percentage >= 60:
                grade = "B"
            elif percentage >= 50:
                grade = "C"
            else:
                grade = "F"


            # ======================================
            # FETCH STUDENT NAME
            # ======================================

            student_name = roll

            student = Student.query.filter_by(
                roll_number=roll.upper()
            ).first()

            if student:

                s_user = User.query.get(student.user_id)

                if s_user:
                    student_name = s_user.full_name


            # ======================================
            # SAVE RESULT
            # ======================================

            existing_res = OMRResult.query.filter_by(
                exam_id=exam_id,
                student_roll=roll.upper()
            ).first()


            if existing_res:

                existing_res.score = score
                existing_res.percentage = round(percentage, 2)
                existing_res.grade = grade
                existing_res.full_name = student_name

            else:

                res_db = OMRResult(
                    exam_id=exam_id,
                    student_roll=roll.upper(),
                    full_name=student_name,
                    score=score,
                    percentage=round(percentage, 2),
                    grade=grade
                )

                db.session.add(res_db)


            results_summary.append({
                "roll": roll,
                "score": score,
                "status": "Success"
            })


        db.session.commit()


        return jsonify({
            "message": "Evaluation completed",
            "results": results_summary
        })


    except Exception as e:

        db.session.rollback()

        print("OMR Evaluation Error:", str(e))

        import traceback
        traceback.print_exc()

        return jsonify({"error": str(e)}), 500
# ======================================================
# FETCH OMR RESULTS
# ======================================================
@api.route("/faculty/omr_results/<int:exam_id>", methods=["GET"])
@jwt_required()
def get_omr_results(exam_id):
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)

    if not user or user.role != "faculty":
        return jsonify({"error": "Unauthorized"}), 403

    exam = OMRExam.query.get(exam_id)
    if not exam or exam.faculty_id != user_id:
        return jsonify({"error": "Exam not found or unauthorized"}), 404

    results = OMRResult.query.filter_by(exam_id=exam_id).all()

    if not results:
        return jsonify({
            "totalStudents": 0,
            "passCount": 0,
            "failCount": 0,
            "averageScore": 0,
            "results": []
        }), 200

    pass_count = sum(1 for r in results if r.percentage >= 50)
    fail_count = len(results) - pass_count
    avg_score = sum(r.percentage for r in results) / len(results)

    results_data = []
    for r in results:
        # Fallback for full name if null in OMRResult table
        student_name = r.full_name
        if not student_name:
            student = Student.query.filter_by(roll_number=r.student_roll).first()
            if student:
                user = User.query.get(student.user_id)
                if user:
                    student_name = user.full_name
        
        results_data.append({
            "studentRoll": r.student_roll,
            "fullName": student_name or r.student_roll,
            "score": float(r.score),
            "percentage": float(r.percentage),
            "grade": r.grade
        })

    return jsonify({
        "totalStudents": len(results),
        "passCount": pass_count,
        "failCount": fail_count,
        "averageScore": avg_score,
        "results": results_data
    }), 200
