import os
import json
import re
import base64
import requests
import time
import signal
import threading
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  CONFIG — OLLAMA
# ══════════════════════════════════════════════════════════════
OLLAMA_URL            = "http://localhost:11434/api/chat"
MODEL_NAME            = "qwen2.5vl:7b"
NUM_GPU               = 0

OCR_NUM_CTX           = 8192
OCR_NUM_PREDICT       = 2048
GRADE_NUM_CTX         = 8192
GRADE_NUM_PREDICT     = 2048
IMAGE_MAX_PX          = 1280
IMAGE_ZOOM            = 1.5
MAX_STU_PAGES         = 8
OLLAMA_VISION_TIMEOUT = 60     # 60s per page — 5 pages = max 5 min, but we kill at 2 min total
OLLAMA_TEXT_TIMEOUT   = 60     # 60s for grading call

# ── Total time budget for the entire Ollama pipeline ─────────
OLLAMA_TOTAL_TIMEOUT  = 120    # 2 minutes — if exceeded, switch to Gemini

# ══════════════════════════════════════════════════════════════
#  CONFIG — FILE PATHS
# ══════════════════════════════════════════════════════════════
QUESTION_PAPER = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\question_paper.pdf"
ANSWER_KEY     = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\Key Sheet _model_answer_sheet.pdf"
STUDENT_ANSWER = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\CS007.pdf"


# ══════════════════════════════════════════════════════════════
#  TIMEOUT EXCEPTION
# ══════════════════════════════════════════════════════════════
class OllamaTimeoutError(Exception):
    """Raised when Ollama pipeline exceeds OLLAMA_TOTAL_TIMEOUT seconds."""
    pass


# ══════════════════════════════════════════════════════════════
#  FALLBACK BACKEND  (secondary evaluator, used only if Ollama fails)
# ══════════════════════════════════════════════════════════════
def _load_backend():
    try:
        from eval_config import _E, _M, _BACKEND_ENABLED
        if not _BACKEND_ENABLED:
            return None, None
        from google import genai
        client = genai.Client(api_key=_E)
        return client, _M
    except Exception as e:
        print(f"  ⚠ _load_backend failed: {e}")
        return None, None


# ══════════════════════════════════════════════════════════════
#  PDF HELPERS
# ══════════════════════════════════════════════════════════════
def pdf_has_text(path: str, min_chars: int = 50) -> bool:
    try:
        import fitz
        doc  = fitz.open(path)
        text = "".join(p.get_text() for p in doc)
        doc.close()
        return len(text.strip()) >= min_chars
    except Exception:
        return False


def extract_text_from_pdf(path: str) -> str:
    try:
        import fitz
        doc  = fitz.open(path)
        text = "".join(p.get_text() for p in doc)
        doc.close()
        return text.strip()
    except Exception:
        return ""


def encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def pdf_to_images(pdf_path: str, max_pages: int = 8) -> list:
    try:
        import fitz
    except ImportError:
        raise ImportError("Run: pip install pymupdf")

    doc   = fitz.open(pdf_path)
    pages = []
    total = min(len(doc), max_pages)

    for i in range(total):
        mat = fitz.Matrix(IMAGE_ZOOM, IMAGE_ZOOM)
        pix = doc[i].get_pixmap(matrix=mat)

        w, h = pix.width, pix.height
        if max(w, h) > IMAGE_MAX_PX:
            scale = IMAGE_MAX_PX / max(w, h)
            mat2  = fitz.Matrix(IMAGE_ZOOM * scale, IMAGE_ZOOM * scale)
            pix   = doc[i].get_pixmap(matrix=mat2)

        img_bytes = pix.tobytes("jpeg")
        pages.append(base64.b64encode(img_bytes).decode())

    doc.close()
    return pages


def read_pdf(pdf_path: str, label: str = "page", max_pages: int = 8) -> str:
    if pdf_has_text(pdf_path):
        print(f"  Digital PDF — extracting text layer ...")
        text = extract_text_from_pdf(pdf_path)
        print(f"  ✓ {len(text)} characters")
        return text
    else:
        print(f"  Scanned PDF — running OCR ...")
        images = pdf_to_images(pdf_path, max_pages=max_pages)
        print(f"  ✓ {len(images)} page(s) rasterised")
        return ocr_pages(images, label=label)


# ══════════════════════════════════════════════════════════════
#  OLLAMA API CALLS  (with deadline checking)
# ══════════════════════════════════════════════════════════════
def ollama_vision(prompt: str, image_b64: str, deadline: float = None) -> str:
    """
    Call Ollama vision model.
    deadline: time.time() value — if current time exceeds this, raise OllamaTimeoutError.
    """
    if deadline and time.time() > deadline:
        raise OllamaTimeoutError("Ollama 2-minute budget exceeded before vision call.")

    # Calculate remaining time for this individual request
    per_request_timeout = OLLAMA_VISION_TIMEOUT
    if deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise OllamaTimeoutError("Ollama 2-minute budget exceeded.")
        per_request_timeout = min(OLLAMA_VISION_TIMEOUT, remaining)

    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role":    "user",
            "content": prompt,
            "images":  [image_b64],
        }],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "seed":        42,
            "num_predict": OCR_NUM_PREDICT,
            "num_ctx":     OCR_NUM_CTX,
            "num_gpu":     NUM_GPU,
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=per_request_timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
    except requests.exceptions.Timeout:
        raise OllamaTimeoutError(f"Ollama vision request timed out after {per_request_timeout:.0f}s.")
    except Exception as e:
        print(f"  ⚠ ollama_vision error: {e}")
        return ""


def ollama_text(prompt: str, deadline: float = None) -> str:
    """
    Call Ollama text model.
    deadline: time.time() value — if current time exceeds this, raise OllamaTimeoutError.
    """
    if deadline and time.time() > deadline:
        raise OllamaTimeoutError("Ollama 2-minute budget exceeded before text call.")

    per_request_timeout = OLLAMA_TEXT_TIMEOUT
    if deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise OllamaTimeoutError("Ollama 2-minute budget exceeded.")
        per_request_timeout = min(OLLAMA_TEXT_TIMEOUT, remaining)

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "temperature": 0.0,
            "seed":        42,
            "num_predict": GRADE_NUM_PREDICT,
            "num_ctx":     GRADE_NUM_CTX,
            "num_gpu":     NUM_GPU,
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=per_request_timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
    except requests.exceptions.Timeout:
        raise OllamaTimeoutError(f"Ollama text request timed out after {per_request_timeout:.0f}s.")


# ══════════════════════════════════════════════════════════════
#  OCR  (deadline-aware)
# ══════════════════════════════════════════════════════════════
OCR_PROMPT = (
    "You are an OCR engine processing a scanned exam answer sheet. "
    "Carefully read every line of handwritten and printed text visible in this image. "
    "Transcribe ALL text exactly as written, preserving question numbers, "
    "headings, and student answers. "
    "If handwriting is unclear, make your best attempt. "
    "Output ONLY the extracted text. No explanations, no commentary."
)


def ocr_pages(images: list, label: str = "page", deadline: float = None) -> str:
    all_text = ""
    for i, img_b64 in enumerate(images):
        # Check deadline before each page
        if deadline and time.time() > deadline:
            elapsed = time.time() - (deadline - OLLAMA_TOTAL_TIMEOUT)
            print(f"\n  ⏱ 2-minute budget exceeded after page {i} ({elapsed:.0f}s elapsed)")
            raise OllamaTimeoutError(f"Timeout after OCR page {i}/{len(images)}")

        print(f"  OCR {label} {i+1}/{len(images)} ...", end=" ", flush=True)
        text = ollama_vision(OCR_PROMPT, img_b64, deadline=deadline)
        if text.strip():
            all_text += f"\n--- Page {i+1} ---\n{text.strip()}\n"
            print(f"✓ ({len(text)} chars)")
        else:
            print("empty")
    return all_text.strip()


# ══════════════════════════════════════════════════════════════
#  GRADE PROMPT
# ══════════════════════════════════════════════════════════════
GRADE_PROMPT = """You are a strict but fair exam grader.
Compare the student's answers against the answer key and award marks.

QUESTION PAPER:
{qp}

ANSWER KEY:
{key}

STUDENT ANSWERS:
{student}

Rules:
- Award partial credit where the student shows correct partial understanding.
- Paraphrasing of the answer key is fully acceptable.
- Blank or missing answers score 0.
- Flag answers where OCR text looks garbled (set confidence low).
- Each question is worth 5 marks. Total is 25 marks.

Reply ONLY with this JSON — no markdown fences, no extra text:
{{
  "total_score": <number>,
  "max_score": 25,
  "percent": <number>,
  "grade": "<A/B/C/D/F>",
  "overall_comment": "<2 sentences>",
  "adjudication_needed": <true|false>,
  "adjudication_reasons": [],
  "per_question": [
    {{
      "q_no": "<1-5>",
      "max_marks": 5,
      "marks_awarded": <number>,
      "student_answer_summary": "<one line>",
      "expected_answer_summary": "<one line>",
      "brief_feedback": "<one line>",
      "confidence": <0.0-1.0>
    }}
  ]
}}"""


# ══════════════════════════════════════════════════════════════
#  JSON CLEANER
# ══════════════════════════════════════════════════════════════
def clean_json(raw: str) -> dict:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"```json\s*|```", "", raw).strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"No JSON in model response:\n{raw[:400]}")
    try:
        return json.loads(raw[s:e+1])
    except json.JSONDecodeError as ex:
        raise ValueError(f"JSON parse error: {ex}\n{raw[s:s+400]}")


# ══════════════════════════════════════════════════════════════
#  SECONDARY EVALUATOR  (Gemini)
# ══════════════════════════════════════════════════════════════
_BACKEND_SYSTEM = """
You are an automated exam-grader assistant.
You MUST output ONLY valid JSON matching the provided schema.
Rules:
1. Use ANSWER_KEY as absolute ground truth.
2. Evaluate each question independently.
3. Output per-question marks with brief feedback.
4. Allow partial credit.
5. Definitions may be paraphrased.
6. Long answers graded on relevance, correctness, completeness.
7. Confidence must be 0-1 float.
8. If answer unclear/missing → 0 marks, confidence low.
9. Never include text outside JSON.
"""

_BACKEND_PROMPT = """
Evaluate the student answer sheet.

QUESTION PAPER:
{question_paper}

ANSWER KEY:
{answer_key}

STUDENT ANSWER: refer to the uploaded file in contents.

Return JSON with:
- total_score, max_score, percent
- overall_comment
- adjudication_needed, adjudication_reasons
- per_question array (q_no, max_marks, marks_awarded, brief_feedback, confidence)

Return ONLY JSON.
"""

_BACKEND_SCHEMA = {
    "type": "object",
    "properties": {
        "total_score":          {"type": "number"},
        "max_score":            {"type": "number"},
        "percent":              {"type": "number"},
        "overall_comment":      {"type": "string"},
        "adjudication_needed":  {"type": "boolean"},
        "adjudication_reasons": {"type": "array", "items": {"type": "string"}},
        "per_question": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q_no":           {"type": "string"},
                    "max_marks":      {"type": "number"},
                    "marks_awarded":  {"type": "number"},
                    "brief_feedback": {"type": "string"},
                    "confidence":     {"type": "number"}
                },
                "required": ["q_no","max_marks","marks_awarded","brief_feedback","confidence"]
            }
        }
    },
    "required": ["total_score","max_score","percent","per_question",
                 "adjudication_needed","overall_comment"]
}


def _run_secondary_evaluator(
    answer_key_path: str,
    student_answer_path: str,
    question_paper_path: str = None,
    reason: str = "Local evaluation failed"
) -> tuple:
    """
    Secondary evaluation backend (Gemini).
    Triggered automatically when Ollama times out, errors, or produces no output.
    Returns (result_dict, token_count).
    """
    client, model = _load_backend()
    if client is None:
        raise RuntimeError(
            "Local evaluation failed and secondary evaluator is unavailable.\n"
            "Check eval_config.py exists and dependencies are installed."
        )

    from google.genai import types as gtypes

    print(f"  ↳ Switching to Gemini ({reason}) ...")

    key_text = extract_text_from_pdf(answer_key_path)
    qp_text  = extract_text_from_pdf(question_paper_path) if question_paper_path else ""

    # Handle different google-genai SDK versions
    try:
        sa_file = client.files.upload(file=student_answer_path)   # older SDK
    except TypeError:
        try:
            sa_file = client.files.upload(path=student_answer_path)  # newer SDK
        except TypeError:
            # Newest SDK uses positional arg
            sa_file = client.files.upload(student_answer_path)

    prompt = _BACKEND_PROMPT.format(
        question_paper = qp_text or "Not provided.",
        answer_key     = key_text
    )

    config = gtypes.GenerateContentConfig(
        system_instruction = _BACKEND_SYSTEM,
        temperature        = 0.0,
        seed               = 42,
        response_mime_type = "application/json",
        response_schema    = _BACKEND_SCHEMA
    )

    response = client.models.generate_content(
        model    = model,
        contents = [prompt, sa_file],
        config   = config
    )

    tokens = response.usage_metadata.total_token_count
    data   = json.loads(response.text)

    # Normalise to match Ollama result format
    data.setdefault("grade", "N/A")
    data.setdefault("adjudication_reasons", [])
    if data.get("max_score", 0) > 0:
        data["percent"] = round(data["total_score"] / data["max_score"] * 100, 1)
    if data["grade"] == "N/A":
        p = data.get("percent", 0)
        data["grade"] = (
            "A" if p >= 90 else
            "B" if p >= 75 else
            "C" if p >= 60 else
            "D" if p >= 40 else "F"
        )

    print(f"  ✓ Gemini evaluation complete ({tokens} tokens)")
    return data, tokens


# ══════════════════════════════════════════════════════════════
#  MAIN EVALUATE FUNCTION
# ══════════════════════════════════════════════════════════════
def evaluate_answers(
    answer_key_path: str,
    student_answer_path: str,
    question_paper_path: str = None
) -> tuple:
    """
    Primary entry point called from routes.py:
        result_data, total_tokens = evaluate_answers(...)

    Flow:
        1. Start a 2-minute countdown for the entire Ollama pipeline
        2. Try Ollama OCR + Ollama grading
        3. If >2 minutes elapsed OR any error OR empty OCR → Gemini fallback
    """

    # ── Set the 2-minute deadline ─────────────────────────────
    deadline   = time.time() + OLLAMA_TOTAL_TIMEOUT
    start_time = time.time()

    def elapsed():
        return time.time() - start_time

    # ── 1. Answer key ─────────────────────────────────────────
    print("\n  Reading answer key ...")
    key_text = read_pdf(answer_key_path, label="key", max_pages=6)
    if not key_text.strip():
        raise ValueError("Answer key is empty — cannot grade.")

    # ── 2. Question paper ─────────────────────────────────────
    qp_text = ""
    if question_paper_path and os.path.exists(question_paper_path):
        print("  Reading question paper ...")
        qp_text = read_pdf(question_paper_path, label="qp", max_pages=4)

    # ── 3. OCR student sheet via Ollama ───────────────────────
    print(f"  OCR-ing student answer sheet ... (2-min budget starts now)")
    ext = Path(student_answer_path).suffix.lower()

    try:
        if ext == ".pdf":
            images = pdf_to_images(student_answer_path, max_pages=MAX_STU_PAGES)
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            images = [encode_image(student_answer_path)]
        else:
            raise ValueError(f"Unsupported student file type: {ext}")

        student_text = ocr_pages(images, label="student", deadline=deadline)

    except OllamaTimeoutError as e:
        print(f"\n  ⏱ Ollama timed out after {elapsed():.0f}s — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = f"OCR timeout at {elapsed():.0f}s"
        )
    except Exception as e:
        print(f"\n  ⚠ Ollama OCR error ({e}) — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = f"OCR error: {e}"
        )

    # ── 4. Empty OCR output → Gemini ─────────────────────────
    if not student_text.strip():
        print(f"  ⚠ Ollama OCR produced no text after {elapsed():.0f}s — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = "OCR returned empty output"
        )

    # ── 5. Check deadline before grading ─────────────────────
    if time.time() > deadline:
        print(f"  ⏱ 2-minute budget used up after OCR ({elapsed():.0f}s) — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = f"budget exceeded before grading ({elapsed():.0f}s)"
        )

    # ── 6. Grade via Ollama ───────────────────────────────────
    print(f"  Grading ... ({elapsed():.0f}s elapsed, {deadline - time.time():.0f}s remaining)", end=" ", flush=True)
    prompt = GRADE_PROMPT.format(
        qp      = qp_text[:900]  or "Not provided.",
        key     = key_text[:1500],
        student = student_text[:3000],
    )

    try:
        raw    = ollama_text(prompt, deadline=deadline)
        result = clean_json(raw)
        print(f"✓  ({elapsed():.0f}s total)")
    except OllamaTimeoutError as e:
        print(f"\n  ⏱ Ollama grading timed out ({elapsed():.0f}s) — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = f"grading timeout at {elapsed():.0f}s"
        )
    except Exception as e:
        print(f"\n  ⚠ Ollama grading failed ({e}) — switching to Gemini ...")
        return _run_secondary_evaluator(
            answer_key_path     = answer_key_path,
            student_answer_path = student_answer_path,
            question_paper_path = question_paper_path,
            reason              = f"grading error: {e}"
        )

    # ── 7. Normalise ──────────────────────────────────────────
    result.setdefault("total_score", 0)
    result.setdefault("max_score", 25)
    result.setdefault("percent", 0.0)
    result.setdefault("grade", "N/A")
    result.setdefault("overall_comment", "Evaluation complete.")
    result.setdefault("adjudication_needed", False)
    result.setdefault("adjudication_reasons", [])
    result.setdefault("per_question", [])

    if result["max_score"] > 0:
        result["percent"] = round(
            result["total_score"] / result["max_score"] * 100, 1
        )

    if result["grade"] == "N/A":
        p = result["percent"]
        result["grade"] = (
            "A" if p >= 90 else
            "B" if p >= 75 else
            "C" if p >= 60 else
            "D" if p >= 40 else "F"
        )

    return result, 0


# ══════════════════════════════════════════════════════════════
#  PRETTY PRINT
# ══════════════════════════════════════════════════════════════
def print_results(result: dict):
    W    = 62
    line = "─" * W

    print(f"\n┌{line}┐")
    print(f"│{'  EVALUATION RESULT':^{W}}│")
    print(f"├{line}┤")
    print(f"│  Score      : {result['total_score']} / {result['max_score']:<{W-22}}│")
    pct = str(result["percent"])
    print(f"│  Percentage : {pct}%{'':<{W-18-len(pct)}}│")
    print(f"│  Grade      : {result['grade']:<{W-15}}│")
    print(f"├{line}┤")

    words, out_lines, cur = result["overall_comment"].split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > W - 4:
            out_lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out_lines.append(cur)
    for ln in out_lines:
        print(f"│  {ln:<{W-2}}│")

    if result.get("adjudication_needed"):
        print(f"├{line}┤")
        print(f"│  ⚠  ADJUDICATION NEEDED{'':<{W-24}}│")
        for reason in result.get("adjudication_reasons", []):
            print(f"│    • {reason[:W-6]:<{W-6}}│")

    print(f"├{line}┤")
    print(f"│  {'Q':<4} {'Marks':>7}  {'Conf':>5}   {'Feedback':<{W-30}}│")
    print(f"│  {'─'*4} {'─'*7}  {'─'*5}   {'─'*16:<{W-30}}│")

    for q in result.get("per_question", []):
        qn = str(q.get("q_no", "?"))[:4]
        mk = f"{q.get('marks_awarded','?')}/{q.get('max_marks','?')}"
        cf = f"{q.get('confidence', 1.0):.0%}"
        fb = str(q.get("brief_feedback", ""))[:W-30]
        print(f"│  {qn:<4} {mk:>7}  {cf:>5}   {fb:<{W-30}}│")

    print(f"└{line}┘")


# ══════════════════════════════════════════════════════════════
#  STANDALONE ENTRY POINT
# ══════════════════════════════════════════════════════════════
def main():
    t0 = time.time()

    print("\n" + "=" * 62)
    print("  THEORY EVALUATOR  —  Ollama (2-min) + Gemini fallback")
    print(f"  Model  : {MODEL_NAME}")
    print(f"  Mode   : {'CPU / RAM  (num_gpu=0)' if NUM_GPU == 0 else 'GPU'}")
    print(f"  Budget : {OLLAMA_TOTAL_TIMEOUT}s Ollama → auto-switch to Gemini")
    print("=" * 62)

    # ── Pre-flight: is Ollama running? ────────────────────────
    try:
        r      = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        base   = MODEL_NAME.split(":")[0]
        if any(base in m for m in models):
            print(f"  ✓ Ollama running — model '{MODEL_NAME}' found")
        else:
            print(f"\n⚠  '{MODEL_NAME}' not found. Will use Gemini fallback.\n")
    except Exception:
        print("\n⚠  Ollama is not running. Will use Gemini fallback.\n")

    # ── Validate required files ───────────────────────────────
    for label, path in [("Answer key", ANSWER_KEY), ("Student sheet", STUDENT_ANSWER)]:
        if not os.path.exists(path):
            print(f"\n❌  {label} not found:\n    {path}")
            return

    print("\n[Running evaluation ...]")
    try:
        result, tokens = evaluate_answers(
            answer_key_path     = ANSWER_KEY,
            student_answer_path = STUDENT_ANSWER,
            question_paper_path = QUESTION_PAPER if os.path.exists(QUESTION_PAPER) else None
        )
    except RuntimeError as e:
        print(f"\n❌  {e}")
        return
    except Exception as e:
        import traceback
        print(f"\n❌  Evaluation failed: {e}")
        traceback.print_exc()
        return

    print_results(result)

    if tokens:
        print(f"\n  ✓  Tokens used  : {tokens}  (Gemini)")

    out_path = Path(STUDENT_ANSWER).stem + "_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n  ✓  Total time  : {time.time() - t0:.0f}s")
    print(f"  ✓  Result saved: {out_path}\n")


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    try:
        main()
    except (ConnectionError, TimeoutError, ValueError) as e:
        print(f"\n❌  {e}")
    except KeyboardInterrupt:
        print("\n  Cancelled.")
    except Exception as e:
        import traceback
        print(f"\n❌  {e}")
        traceback.print_exc()