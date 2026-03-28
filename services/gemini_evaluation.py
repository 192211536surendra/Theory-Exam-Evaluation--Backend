import os
import json
import re
import mimetypes
# from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types

# ================= LOAD ENV ===================

# load_dotenv(find_dotenv())
# API_KEY = os.getenv("GOOGLE_API_KEY")



# if not API_KEY:
#     raise ValueError("GOOGLE_API_KEY not found in .env")



API_KEY="AIzaSyAoDqO1qb70TMXWvszZsBYQtFcxzxgpCHQ"
client = genai.Client(api_key=API_KEY)

# ================= SYSTEM INSTRUCTION ===================

SYSTEM_INSTRUCTION = """
You are an automated exam-grader assistant.

You MUST output ONLY valid JSON matching the provided schema.

Rules:

1. Use ANSWER_KEY as absolute ground truth.
2. Evaluate each question independently.
3. Output per-question marks with brief feedback.
4. Allow partial credit.
5. Numeric answers must match key or tolerance.
6. Definitions may be paraphrased.
7. Long answers graded on relevance, correctness, completeness.
8. Confidence must be 0–1 float.
9. If answer unclear / missing → 0 marks.
10. If confidence <0.45 OR OCR unclear → adjudication_needed=true.
11. Do NOT output student names or IDs.
12. Never include text outside JSON.
"""

# ================= PROMPT TEMPLATE ===================

PROMPT_TEMPLATE_RAW = """
Evaluate the student answer sheet.

Inputs:

QUESTION PAPER:
{question_paper}

ANSWER KEY:
{answer_key}


STUDENT ANSWER:uploaded in the contents please refer

Return JSON:

- total_score
- max_score
- percent
- overall_comment
- adjudication_needed
- adjudication_reasons
- per_question array:

Each question:

q_no
max_marks
marks_awarded
brief_feedback
confidence

Rules:
- Partial credit allowed.
- Numeric tolerance only if mentioned.
- Multi-step → partial marks.
- Essay → rubric scoring.

Return ONLY JSON.
"""

# ================= JSON SCHEMA ===================

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "total_score": {"type": "number"},
        "max_score": {"type": "number"},
        "percent": {"type": "number"},
        "overall_comment": {"type": "string"},
        "adjudication_needed": {"type": "boolean"},
        "adjudication_reasons": {
            "type": "array",
            "items": {"type": "string"}
        },
        "per_question": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q_no": {"type": "string"},
                    "max_marks": {"type": "number"},
                    "marks_awarded": {"type": "number"},
                    "brief_feedback": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": [
                    "q_no",
                    "max_marks",
                    "marks_awarded",
                    "brief_feedback",
                    "confidence"
                ]
            }
        }
    },
    "required": [
        "total_score",
        "max_score",
        "percent",
        "per_question",
        "adjudication_needed",
        "overall_comment"
    ]
}

# ================= CONFIG ===================

CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    temperature=0.0,
    seed=42,
    response_mime_type="application/json"
)

# ================= FILE UPLOAD ===================




import fitz  # PyMuPDF
from docx import Document

def extract_text(file_path):
    ext = os.path.splitext(file_path)[1].lower().strip()

    if ext == ".pdf":
        text = ""
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text()
        return text

    elif ext == ".docx":
        doc = Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])

    else:
        # Try PDF anyway as fallback
        try:
            text = ""
            doc = fitz.open(file_path)
            for page in doc:
                text += page.get_text()
            return text
        except:
            raise ValueError(f"Unsupported file type: {ext}")

# ================= MAIN EVALUATION ===================
def upload_file(path):
    return client.files.upload(file=path)


def evaluate_answers(answer_key_path, student_answer_path, question_paper_path):
    
    answer_key_text = extract_text(answer_key_path)
    question_paper_text = extract_text(question_paper_path)

    sa = upload_file(student_answer_path)

    prompt = PROMPT_TEMPLATE_RAW.format(
        question_paper=question_paper_text,
        answer_key=answer_key_text,
        student_answer="Refer to uploaded student answer file"
    )

    contents = [
    prompt,
    sa
    ]

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=CONFIG
    )

    total_tokens = response.usage_metadata.total_token_count  # ← added tokens
    data = json.loads(response.text)

    return data, total_tokens  # ← return both values


# ================= RUN EXAMPLE ===================


if __name__ == "__main__":
    
    QUESTION_PAPER = r"C:\Users\ravin\Downloads\Question Paper.pdf"
    ANSWER_KEY = r"C:\Users\ravin\Downloads\Key sheet.pdf"
    STUDENT_ANSWER = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\CS016.pdf"
    result, tokens = evaluate_answers(
        question_paper_path=QUESTION_PAPER,
        answer_key_path=ANSWER_KEY,
        student_answer_path=STUDENT_ANSWER
    )

    print("\n====== RESULT ======\n")
    print(json.dumps(result, indent=2))
    print("\nTokens Used:", tokens)