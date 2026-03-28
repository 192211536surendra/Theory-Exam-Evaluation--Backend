import os
import json
import re
import base64
import requests
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────
OLLAMA_URL        = "http://localhost:11434/api/chat"
MODEL_NAME        = "qwen2.5vl:7b"
NUM_GPU           = 0

OCR_NUM_CTX       = 2048
OCR_NUM_PREDICT   = 768
GRADE_NUM_CTX     = 3000
GRADE_NUM_PREDICT = 1500
IMAGE_MAX_PX      = 640
IMAGE_ZOOM        = 0.8
MAX_STU_PAGES     = 8

QUESTION_PAPER = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\question_paper.pdf"
ANSWER_KEY     = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\Key Sheet _model_answer_sheet.pdf"
STUDENT_ANSWER = r"C:\Users\ravin\OneDrive\Music\Desktop\theory\CS007.pdf"

# ── PDF HELPERS ───────────────────────────────────────────────
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

# ── OLLAMA API CALLS ──────────────────────────────────────────
def ollama_vision(prompt: str, image_b64: str) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [image_b64],
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
        r = requests.post(OLLAMA_URL, json=payload, timeout=300)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
    except Exception as e:
        print(f"  ⚠ ollama_vision error: {e}")
        return ""

def ollama_text(prompt: str) -> str:
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
    r = requests.post(OLLAMA_URL, json=payload, timeout=900)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")

# ── OCR ───────────────────────────────────────────────────────
def ocr_pages(images: list, label: str = "page") -> str:
    prompt = (
        "You are an OCR engine. Read this scanned exam page carefully. "
        "Extract ALL text exactly as written — question numbers, headings, "
        "and any handwritten or printed answers. "
        "Output only the raw extracted text. No commentary."
    )
    all_text = ""
    for i, img_b64 in enumerate(images):
        print(f"  OCR {label} {i+1}/{len(images)} ...", end=" ", flush=True)
        text = ollama_vision(prompt, img_b64)
        if text.strip():
            all_text += f"\n--- Page {i+1} ---\n{text.strip()}\n"
            print(f"✓ ({len(text)} chars)")
        else:
            print("⚠ empty")
    return all_text.strip()

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

# ── GRADE PROMPT ──────────────────────────────────────────────
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

# ── JSON CLEANER ──────────────────────────────────────────────
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

# ── MAIN EVALUATE FUNCTION (called from routes.py) ────────────
def evaluate_answers(
    answer_key_path: str,
    student_answer_path: str,
    question_paper_path: str = None,
    # ── Pre-extracted text ────────────────────────────────────
    # Pass these from main() to skip re-reading / re-OCR-ing files
    # that were already processed. Leave as None when calling
    # directly from routes.py so the function reads them itself.
    key_text: str = None,
    student_text: str = None,
    qp_text: str = None,
) -> tuple:
    """
    Returns (result_dict, 0).

    When key_text / student_text / qp_text are supplied the corresponding
    files are NOT read again, preventing the double-OCR that happened when
    main() already extracted them before calling this function.
    """

    # 1. Answer key — read only if not already supplied
    if key_text is None:
        print("\n  Reading answer key ...")
        key_text = read_pdf(answer_key_path, label="key", max_pages=6)
    if not key_text.strip():
        raise ValueError("Answer key is empty — cannot grade.")

    # 2. Question paper — read only if not already supplied
    if qp_text is None:
        qp_text = ""
        if question_paper_path and os.path.exists(question_paper_path):
            print("  Reading question paper ...")
            qp_text = read_pdf(question_paper_path, label="qp", max_pages=4)

    # 3. Student answers — use pre-extracted text if supplied
    if student_text is None:
        print("  OCR-ing student answer sheet ...")
        images = pdf_to_images(student_answer_path, max_pages=MAX_STU_PAGES)
        student_text = ocr_pages(images, label="student")
    if not student_text.strip():
        raise ValueError("No text extracted from student sheet.")

    # 4. Grade
    print("  Grading ...", end=" ", flush=True)
    prompt = GRADE_PROMPT.format(
        qp      = qp_text[:900]  or "Not provided.",
        key     = key_text[:1500],
        student = student_text[:3000],
    )
    raw    = ollama_text(prompt)
    result = clean_json(raw)
    print("✓")

    # 5. Fill defaults
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

    return result, 0  # 0 = no token count from Ollama

# ── Pretty print ──────────────────────────────────────────────
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

    if result["adjudication_needed"]:
        print(f"├{line}┤")
        print(f"│  ⚠  ADJUDICATION NEEDED{'':<{W-24}}│")
        for reason in result["adjudication_reasons"]:
            print(f"│    • {reason[:W-6]:<{W-6}}│")

    print(f"├{line}┤")
    print(f"│  {'Q':<4} {'Marks':>7}  {'Conf':>5}   {'Feedback':<{W-30}}│")
    print(f"│  {'─'*4} {'─'*7}  {'─'*5}   {'─'*16:<{W-30}}│")

    for q in result["per_question"]:
        qn = str(q.get("q_no", "?"))[:4]
        mk = f"{q.get('marks_awarded','?')}/{q.get('max_marks','?')}"
        cf = f"{q.get('confidence', 1.0):.0%}"
        fb = str(q.get("brief_feedback", ""))[:W-30]
        print(f"│  {qn:<4} {mk:>7}  {cf:>5}   {fb:<{W-30}}│")

    print(f"└{line}┘")

# ── Main ──────────────────────────────────────────────────────
def main():
    import time
    t0 = time.time()

    print("\n" + "=" * 62)
    print("  THEORY EVALUATOR  —  fully local via Ollama")
    print(f"  Model : {MODEL_NAME}")
    print(f"  Mode  : {'CPU / RAM  (num_gpu=0)' if NUM_GPU == 0 else 'GPU'}")
    print("=" * 62)

    # Pre-flight: is Ollama running and model pulled?
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        base   = MODEL_NAME.split(":")[0]
        if not any(base in m for m in models):
            print(f"\n⚠  '{MODEL_NAME}' not found locally.")
            print(f"   Run:  ollama pull {MODEL_NAME}\n")
            return
    except Exception:
        print("\n❌  Ollama is not running.")
        print("   Fix: open a terminal and run  →  ollama serve\n")
        return

    # Validate required files
    for label, path in [("Answer key", ANSWER_KEY), ("Student sheet", STUDENT_ANSWER)]:
        if not os.path.exists(path):
            print(f"\n❌  {label} not found:\n    {path}")
            return

    # ── 1. Answer key ─────────────────────────────────────────
    print("\n[1/4] Reading answer key ...")
    if ANSWER_KEY.lower().endswith(".pdf"):
        key_text = read_pdf(ANSWER_KEY, label="key", max_pages=6)
    else:
        key_text = open(ANSWER_KEY, encoding="utf-8").read().strip()
        print(f"  ✓  {len(key_text)} characters")

    if not key_text.strip():
        print("  ❌  Answer key is empty — cannot grade.")
        return

    # ── 2. Question paper (optional) ──────────────────────────
    qp_text = ""
    if QUESTION_PAPER and os.path.exists(QUESTION_PAPER):
        print("\n[2/4] Reading question paper ...")
        if QUESTION_PAPER.lower().endswith(".pdf"):
            qp_text = read_pdf(QUESTION_PAPER, label="qp", max_pages=4)
        else:
            qp_text = open(QUESTION_PAPER, encoding="utf-8").read().strip()
            print(f"  ✓  {len(qp_text)} characters")
    else:
        print("\n[2/4] Question paper not found — grading from answer key only.")

    # ── 3. Student answer sheet → OCR (SINGLE pass here) ──────
    print("\n[3/4] Loading & OCR-ing student answer sheet ...")
    ext = Path(STUDENT_ANSWER).suffix.lower()
    t_ocr = time.time()

    if ext == ".pdf":
        images = pdf_to_images(STUDENT_ANSWER, max_pages=MAX_STU_PAGES)
        print(f"  ✓  {len(images)} page(s) rasterised")
        student_text = ocr_pages(images, label="student")
    elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        images = [encode_image(STUDENT_ANSWER)]
        print("  ✓  1 image loaded")
        student_text = ocr_pages(images, label="student")
    else:
        raise ValueError(f"Unsupported student file type: {ext}")

    print(f"  OCR done in {time.time() - t_ocr:.0f}s")

    if not student_text.strip():
        print("\n❌  No text extracted from student sheet.")
        print("   • Check that Ollama is still running: ollama ps")
        print("   • Try increasing IMAGE_MAX_PX if pages are blurry")
        return

    print("\n── OCR preview (first 500 chars) ────────────────────────")
    print(student_text[:500])
    print("─────────────────────────────────────────────────────────")

    # ── 4. Grade (pass pre-extracted text — no re-OCR) ────────
    print("\n[4/4] Grading ...")
    t_grade = time.time()
    result, _ = evaluate_answers(
        answer_key_path=ANSWER_KEY,
        student_answer_path=STUDENT_ANSWER,
        question_paper_path=QUESTION_PAPER if QUESTION_PAPER and os.path.exists(QUESTION_PAPER) else None,
        # ↓ Hand the already-extracted text in so nothing is re-read
        key_text=key_text,
        student_text=student_text,
        qp_text=qp_text,
    )
    print(f"  Grading done in {time.time() - t_grade:.0f}s")

    print_results(result)

    out_path = Path(STUDENT_ANSWER).stem + "_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n  ✓  Total time  : {time.time() - t0:.0f}s")
    print(f"  ✓  Result saved: {out_path}\n")

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