from extensions import db
from datetime import datetime


# ======================================================
# DEPARTMENT TABLE
# ======================================================
class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    users = db.relationship("User", backref="department", lazy=True)
    faculty_details = db.relationship("FacultyDetails", backref="department", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "created_at": self.created_at
        }


# ======================================================
# USER TABLE
# ======================================================
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)

    role = db.Column(db.String(20), nullable=False, index=True)

    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))

    is_first_login = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    student_profile = db.relationship(
        "Student",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    faculty_profile = db.relationship(
        "Faculty",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    faculty_details = db.relationship(
        "FacultyDetails",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan"
    )

    exams_created = db.relationship(
        "Exam",
        backref="faculty",
        lazy=True
    )

    theory_exams = db.relationship(
        "TheoryExam",
        backref="faculty",
        lazy=True
    )

    omr_exams = db.relationship(
        "OMRExam",
        backref="faculty",
        lazy=True
    )

    def to_dict(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "email": self.email,
            "role": self.role,
            "department": self.department.name if self.department else None,
            "is_first_login": self.is_first_login,
            "is_active": self.is_active,
            "created_at": self.created_at
        }


# ======================================================
# STUDENT TABLE
# ======================================================
class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    roll_number = db.Column(db.String(50), unique=True, index=True)
    semester = db.Column(db.Integer)
    cgpa = db.Column(db.Float)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    results = db.relationship(
        "StudentExamResult",
        backref="student",
        cascade="all, delete-orphan",
        lazy=True
    )

# ======================================================
# FACULTY TABLE
# ======================================================
class Faculty(db.Model):
    __tablename__ = "faculty"

    id = db.Column(db.Integer, primary_key=True)

    # Added faculty_id
    faculty_id = db.Column(db.String(50), unique=True, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    designation = db.Column(db.String(100))
    experience_years = db.Column(db.Integer)
    qualification = db.Column(db.String(100))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ======================================================
# FACULTY DETAILS
# ======================================================
class FacultyDetails(db.Model):
    __tablename__ = "faculty_details"

    id = db.Column(db.Integer, primary_key=True)

    # Added faculty_id
    faculty_id = db.Column(db.String(50), unique=True, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False)

    designation = db.Column(db.String(100))
    qualification = db.Column(db.String(100))
    experience_years = db.Column(db.Integer)

    phone = db.Column(db.String(20))
    address = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ======================================================
# NORMAL EXAMS TABLE
# ======================================================
class Exam(db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)

    exam_name = db.Column(db.String(200), nullable=False)
    course_code = db.Column(db.String(20), nullable=False, index=True)

    exam_date = db.Column(db.Date)
    duration = db.Column(db.Integer)

    faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    results = db.relationship(
        "StudentExamResult",
        backref="exam",
        cascade="all, delete-orphan",
        lazy=True
    )


# ======================================================
# STUDENT EXAM RESULTS
# ======================================================
class StudentExamResult(db.Model):
    __tablename__ = "student_exam_results"

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False, index=True)

    marks_obtained = db.Column(db.Float, default=0)
    grade = db.Column(db.String(5))
    percentage = db.Column(db.Float, default=0)

    __table_args__ = (
        db.UniqueConstraint('student_id', 'exam_id', name='unique_student_exam'),
    )


# ======================================================
# OMR EXAMS
# ======================================================
class OMRExam(db.Model):
    __tablename__ = "omr_exams"

    id = db.Column(db.Integer, primary_key=True)

    exam_name = db.Column(db.String(200), nullable=False)
    total_questions = db.Column(db.Integer)
    options_per_question = db.Column(db.Integer)
    marks_per_question = db.Column(db.Float)

    faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sheets = db.relationship("OMRSheet", cascade="all, delete-orphan", lazy=True)
    results = db.relationship("OMRResult", cascade="all, delete-orphan", lazy=True)


# ======================================================
# OMR ANSWER KEY
# ======================================================
class OMRAnswerKey(db.Model):
    __tablename__ = "omr_answer_keys"

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(db.Integer, db.ForeignKey("omr_exams.id"), index=True)
    question_number = db.Column(db.Integer)
    correct_option = db.Column(db.String(1))

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'question_number', name='unique_exam_question'),
    )


# ======================================================
# OMR SHEETS
# ======================================================
class OMRSheet(db.Model):
    __tablename__ = "omr_sheets"

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(db.Integer, db.ForeignKey("omr_exams.id"), index=True)
    student_roll = db.Column(db.String(50), index=True)
    sheet_path = db.Column(db.String(200))

    evaluated = db.Column(db.Boolean, default=False)


# ======================================================
# OMR RESULTS
# ======================================================
class OMRResult(db.Model):
    __tablename__ = "omr_results"

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(db.Integer, db.ForeignKey("omr_exams.id"), index=True)
    student_roll = db.Column(db.String(50), index=True)

    score = db.Column(db.Float, default=0)
    percentage = db.Column(db.Float, default=0)
    grade = db.Column(db.String(5))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'student_roll', name='unique_exam_student_omr'),
    )


# ======================================================
# THEORY EXAMS
# ======================================================
class TheoryExam(db.Model):
    __tablename__ = "theory_exams"

    id = db.Column(db.Integer, primary_key=True)

    exam_title = db.Column(db.String(200), nullable=False)
    subject_code = db.Column(db.String(20), nullable=False, index=True)
    total_marks = db.Column(db.Float, nullable=False)

    faculty_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    question_paper_path = db.Column(db.String(300))
    answer_key_path = db.Column(db.String(300))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    answer_sheets = db.relationship(
    "TheoryAnswerSheet",
    backref="exam",
    cascade="all, delete-orphan",
    lazy=True
)

    results = db.relationship(
        "TheoryResult",
        cascade="all, delete-orphan",
        lazy=True
    )


# ======================================================
# THEORY ANSWER SHEETS
# ======================================================
class TheoryAnswerSheet(db.Model):
    __tablename__ = "theory_answer_sheets"

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(db.Integer, db.ForeignKey("theory_exams.id"), index=True)
    student_roll = db.Column(db.String(50), index=True)

    answer_sheet_path = db.Column(db.String(300))
    evaluated = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    

# ======================================================
# THEORY RESULTS
# ======================================================
class TheoryResult(db.Model):
    __tablename__ = "theory_results"

    id = db.Column(db.Integer, primary_key=True)

    exam_id = db.Column(
        db.Integer,
        db.ForeignKey("theory_exams.id"),
        index=True,
        nullable=False
    )

    student_roll = db.Column(
        db.String(50),
        index=True,
        nullable=False
    )

    total_score = db.Column(db.Float, default=0)
    max_score = db.Column(db.Float, default=0)
    percent = db.Column(db.Float, default=0)

    overall_comment = db.Column(db.Text)

    # AI detailed analysis JSON
    result_json = db.Column(db.Text)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "exam_id",
            "student_roll",
            name="unique_exam_student_theory"
        ),
    )