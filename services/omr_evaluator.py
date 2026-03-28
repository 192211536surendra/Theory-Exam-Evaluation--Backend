"""
omr_evaluator.py  —  FINAL PRODUCTION VERSION
==============================================
Grid-based OMR reader. Instead of unreliable contour detection,
this samples the grayscale pixel mean at fixed known bubble positions.

Supports both JPEG/PNG and PDF files.

Calibrated for the actual sheet: 1240×1755px scan.
  • 100 questions  (5 columns × 20 rows)
  • 4 choices per row (A B C D)
  • Filled bubble mean gray ≈ 55–70  |  Empty bubble mean gray ≈ 195–210

Roll number format: CS001, CS007, CS123 etc.
  • Strategy 1: Extract from filename  (CS007.pdf → CS007)  ← most reliable
  • Strategy 2: OCR the roll box (pytesseract, whitelist CS+digits)
  • Strategy 3: Use filename as-is

Public API (used by routes.py):
    result = evaluate_omr_sheet(key_path, sheet_path)
    # → { roll_number, correct, wrong, unattempted, answers, details }

Standalone test:
    python omr_evaluator.py <image_path> [key_path]
    python omr_evaluator.py sheet.pdf answer_key.txt
"""

import os
import re
import sys
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  GRID CONFIGURATION  (pixel positions for 1240×1755 scan)
# ──────────────────────────────────────────────────────────────────────────────

# Left-edge X of each choice bubble, per column  (A, B, C, D)
BUBBLE_COL_X = [
    [291, 313, 334, 356],   # Col 1  →  Q1  – Q20
    [439, 460, 482, 504],   # Col 2  →  Q21 – Q40
    [587, 608, 630, 651],   # Col 3  →  Q41 – Q60
    [736, 757, 779, 800],   # Col 4  →  Q61 – Q80
    [883, 905, 927, 948],   # Col 5  →  Q81 – Q100
]

# Top-edge Y of each answer row  (20 rows per column)
BUBBLE_ROW_Y = [
     595,  631,  667,  703,  739,
     775,  811,  847,  883,  920,
     956,  992, 1028, 1064, 1100,
    1136, 1172, 1208, 1244, 1280,
]

# Bubble bounding box size (pixels)
BUBBLE_W = 21
BUBBLE_H = 21

# Mean-gray threshold: cell mean BELOW this → bubble is filled
FILLED_THRESHOLD = 150   # filled≈60, empty≈200

# Roll-number box region (fraction of full image)
ROLL_BOX = dict(top=0.155, bottom=0.215, left=0.43, right=0.96)

# Expected sheet dimensions (used to scale grids if resolution differs)
REF_W = 1240
REF_H = 1755

# Roll number prefix (change if your institution uses a different prefix)
ROLL_PREFIX = "CS"


# ──────────────────────────────────────────────────────────────────────────────
#  IMAGE LOADING  (supports JPG, PNG, PDF)
# ──────────────────────────────────────────────────────────────────────────────

def _load_gray(image_path: str):
    """
    Load image from JPG, PNG, or PDF file.
    For PDFs, extracts the first page as an image.
    Returns (img, gray) tuple.
    """
    if image_path.lower().endswith('.pdf'):
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(image_path, first_page=1, last_page=1, dpi=150)
            if not images:
                raise FileNotFoundError(f"Could not extract image from PDF: {image_path}")
            pil_image = images[0]
            img  = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            logger.info(f"Loaded PDF: {image_path} → {img.shape}")
            return img, gray
        except ImportError:
            raise ImportError(
                "pdf2image library not found. Install with:\n"
                "  pip install pdf2image pillow\n\n"
                "Also install poppler:\n"
                "  Windows: conda install -c conda-forge poppler\n"
                "  macOS:   brew install poppler\n"
                "  Linux:   sudo apt-get install poppler-utils"
            )
    else:
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot open image: {image_path}")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        logger.info(f"Loaded image: {image_path} → {img.shape}")
        return img, gray


def _scale_grid(img_w: int, img_h: int):
    """
    Scale all grid coordinates proportionally if image resolution
    differs from the reference (REF_W × REF_H).
    """
    sx = img_w / REF_W
    sy = img_h / REF_H

    col_x = [[int(x * sx) for x in col] for col in BUBBLE_COL_X]
    row_y = [int(y * sy) for y in BUBBLE_ROW_Y]
    bw    = max(1, int(BUBBLE_W * sx))
    bh    = max(1, int(BUBBLE_H * sy))
    return col_x, row_y, bw, bh


# ──────────────────────────────────────────────────────────────────────────────
#  BUBBLE READING
# ──────────────────────────────────────────────────────────────────────────────

def _read_bubble(gray, y: int, x: int, bw: int, bh: int) -> float:
    """Return mean grayscale value of the bubble cell. Lower = darker = filled."""
    y1, y2 = max(0, y), min(gray.shape[0], y + bh)
    x1, x2 = max(0, x), min(gray.shape[1], x + bw)
    cell = gray[y1:y2, x1:x2]
    if cell.size == 0:
        return 255.0
    return float(cell.mean())


def _detect_answers(gray) -> list:
    """
    Read all 100 bubble answers.
    Returns list of 100 strings: 'A'/'B'/'C'/'D' or '-' (unattempted).
    """
    img_h, img_w = gray.shape
    col_x, row_y, bw, bh = _scale_grid(img_w, img_h)

    answers = []
    for col_i, col_xs in enumerate(col_x):
        for row_i, ry in enumerate(row_y):
            means = [_read_bubble(gray, ry, cx, bw, bh) for cx in col_xs]
            min_mean = min(means)
            if min_mean < FILLED_THRESHOLD:
                answers.append("ABCD"[int(np.argmin(means))])
            else:
                answers.append("-")
    return answers


# ──────────────────────────────────────────────────────────────────────────────
#  ROLL NUMBER EXTRACTION  (CS001 format)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_roll(img, gray, sheet_path: str = "") -> str:
    """
    Extract roll number in CS001 / CS007 format.

    Strategy 1 — Filename (most reliable, zero OCR error risk):
        CS007.pdf  →  CS007
        OMR_CS007.pdf  →  CS007

    Strategy 2 — OCR the roll number box using pytesseract
        (whitelist: CS + digits only, prevents garbage output)

    Strategy 3 — Return filename stem as-is (last resort fallback)
    """

    # ── Strategy 1: Extract CS+digits from filename ───────────
    if sheet_path:
        fname = os.path.splitext(os.path.basename(sheet_path))[0].upper()

        # Direct match: file is named exactly CS007
        if re.fullmatch(rf'{ROLL_PREFIX}\d{{3,}}', fname):
            logger.info(f"Roll from filename (exact): {fname}")
            return fname

        # Embedded match: file is named OMR_CS007 or EXAM_CS007_2024 etc.
        match = re.search(rf'({ROLL_PREFIX}\d{{3,}})', fname)
        if match:
            logger.info(f"Roll from filename (embedded): {match.group(1)}")
            return match.group(1)

    # ── Strategy 2: OCR the roll number box ───────────────────
    try:
        h, w = gray.shape
        y1 = int(h * ROLL_BOX["top"])
        y2 = int(h * ROLL_BOX["bottom"])
        x1 = int(w * ROLL_BOX["left"])
        x2 = int(w * ROLL_BOX["right"])
        roi = gray[y1:y2, x1:x2]

        if roi.size == 0:
            raise ValueError("Roll ROI is empty — check ROLL_BOX coordinates")

        # Scale up 3× for better OCR accuracy
        roi_big = cv2.resize(roi, None, fx=3, fy=3,
                             interpolation=cv2.INTER_LANCZOS4)

        # Binarise — white background, dark text
        _, bw_img = cv2.threshold(roi_big, 0, 255,
                                  cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Add padding so Tesseract doesn't clip edge characters
        padded = cv2.copyMakeBorder(bw_img, 20, 20, 20, 20,
                                    cv2.BORDER_CONSTANT, value=255)

        try:
            import pytesseract
            # PSM 7 = single line of text
            # Whitelist: only CS prefix letters + digits — prevents garbage
            whitelist = f"{ROLL_PREFIX}0123456789"
            cfg = f'--psm 7 --oem 3 -c tessedit_char_whitelist={whitelist}'
            raw = pytesseract.image_to_string(padded, config=cfg).strip()

            # Strip anything that isn't CS + digits
            cleaned = re.sub(rf'[^{ROLL_PREFIX}0-9]', '', raw.upper())

            match = re.search(rf'({ROLL_PREFIX}\d{{3,}})', cleaned)
            if match:
                logger.info(f"Roll from OCR: {match.group(1)}")
                return match.group(1)
            else:
                logger.warning(f"OCR produced no valid roll. Raw='{raw}' Cleaned='{cleaned}'")

        except ImportError:
            logger.warning("pytesseract not installed — skipping OCR roll extraction")
        except Exception as e:
            logger.warning(f"pytesseract error: {e}")

    except Exception as exc:
        logger.warning(f"Roll box OCR error: {exc}")

    # ── Strategy 3: Filename stem as-is ───────────────────────
    if sheet_path:
        fallback = os.path.splitext(os.path.basename(sheet_path))[0].upper()
        logger.info(f"Roll fallback (filename stem): {fallback}")
        return fallback

    return ""


# ──────────────────────────────────────────────────────────────────────────────
#  ANSWER KEY LOADER
# ──────────────────────────────────────────────────────────────────────────────

def _load_key(key_path: str) -> dict:
    """
    Parse 'Q_NO:OPTION' lines.
    Supports letters (A/B/C/D) and digits (0/1/2/3).
    Returns {q_no (int): option (str)}.
    """
    mapping = {
        "0": "A", "1": "B", "2": "C", "3": "D",
        "A": "A", "B": "B", "C": "C", "D": "D",
    }
    key_map = {}
    with open(key_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or ":" not in line:
                continue
            parts = line.split(":", 1)
            try:
                q_no   = int(parts[0].strip())
                option = mapping.get(parts[1].strip().upper(),
                                     parts[1].strip().upper())
                key_map[q_no] = option
            except ValueError:
                pass
    return key_map


# ──────────────────────────────────────────────────────────────────────────────
#  ANNOTATED OUTPUT IMAGE
# ──────────────────────────────────────────────────────────────────────────────

def _draw_marks(img, gray, answers: list, key_map: dict) -> np.ndarray:
    """Draw green circle = correct, red circle = wrong on a copy of the image."""
    img_h, img_w = gray.shape
    col_x, row_y, bw, bh = _scale_grid(img_w, img_h)
    result = img.copy()

    for q_idx, selected in enumerate(answers):
        if selected == "-":
            continue
        q_no     = q_idx + 1
        expected = key_map.get(q_no)
        if expected is None:
            continue

        col_i  = q_idx // 20
        row_i  = q_idx %  20
        cx     = col_x[col_i]["ABCD".index(selected)]
        cy     = row_y[row_i]
        color  = (0, 200, 0) if selected == expected else (0, 0, 220)
        cx_mid = cx + bw // 2
        cy_mid = cy + bh // 2
        cv2.circle(result, (cx_mid, cy_mid), max(bw, bh) // 2 + 2, color, 2)

    return result


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC API  ← called by routes.py
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_omr_sheet(key_path: str, sheet_path: str) -> dict:
    """
    Evaluate one OMR sheet image against an answer key file.

    Parameters
    ----------
    key_path   : str  Path to answer key text file  (format: "1:A\\n2:B\\n...")
    sheet_path : str  Path to the OMR image (JPEG / PNG / PDF)

    Returns
    -------
    dict
      {
        "roll_number":   str,         # e.g. "CS007"
        "correct":       int,
        "wrong":         int,
        "unattempted":   int,
        "answers":       list[str],   # ["A","B","-", ...]  length 100
        "details":       list[dict],  # per-question breakdown
      }

    Examples
    --------
    >>> result = evaluate_omr_sheet("answer_key.txt", "CS007.jpg")
    >>> result = evaluate_omr_sheet("answer_key.txt", "CS007.pdf")
    """
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Answer key not found: {key_path}")
    if not os.path.exists(sheet_path):
        raise FileNotFoundError(f"OMR sheet not found: {sheet_path}")

    img, gray = _load_gray(sheet_path)
    key_map   = _load_key(key_path)
    answers   = _detect_answers(gray)

    # ── Score ─────────────────────────────────────────────────
    correct = wrong = unattempted = 0
    details = []

    for q_idx, selected in enumerate(answers):
        q_no     = q_idx + 1
        expected = key_map.get(q_no)

        if selected == "-":
            unattempted += 1
            status = "unattempted"
        elif expected is None:
            status = "no_key"
        elif selected == expected:
            correct += 1
            status = "correct"
        else:
            wrong += 1
            status = "wrong"

        details.append({
            "question": q_no,
            "selected": selected,
            "correct":  expected or "?",
            "status":   status,
        })

    # ── Roll number ───────────────────────────────────────────
    roll = ""
    try:
        roll = _extract_roll(img, gray, sheet_path)   # ← pass sheet_path
    except Exception as exc:
        logger.warning("Roll extraction error: %s", exc)

    # Final safety net — should never reach here with filename strategy
    if not roll or len(roll) < 2:
        roll = os.path.splitext(os.path.basename(sheet_path))[0].upper()
        logger.info("Last-resort fallback roll: %s", roll)

    logger.info("OMR | roll=%s correct=%d wrong=%d unattempted=%d",
                roll, correct, wrong, unattempted)

    return {
        "roll_number":  roll,
        "correct":      correct,
        "wrong":        wrong,
        "unattempted":  unattempted,
        "answers":      answers,
        "details":      details,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  CALIBRATION HELPER  (run once when sheet format/scanner changes)
# ──────────────────────────────────────────────────────────────────────────────

def calibrate(image_path: str):
    """
    Auto-detect bubble grid from image and print updated CONFIG values.
    Run this if the sheet or scanner resolution changes.
    Works with both JPG and PDF files.
    """
    img, gray = _load_gray(image_path)
    h, w = gray.shape

    print(f"\n=== CALIBRATING  ({w}×{h}) ===\n")

    _, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        bx, by, bw2, bh2 = cv2.boundingRect(c)
        if bh2 == 0: continue
        ar   = bw2 / float(bh2)
        area = cv2.contourArea(c)
        if 0.5 <= ar <= 1.5 and 150 < area < 600 and by > h * 0.3:
            candidates.append((by, bx, int(area), bw2, bh2))

    candidates.sort()
    if not candidates:
        print("No candidates found. Try threshold<80 or lower.")
        return

    # Cluster Y into rows
    prev_y, rows, row = -100, [], []
    for c in candidates:
        if c[0] - prev_y > 15:
            if row: rows.append(row)
            row = [c]; prev_y = c[0]
        else:
            row.append(c); prev_y = c[0]
    if row: rows.append(row)

    print(f"Detected {len(rows)} rows")
    if rows:
        print(f"\nBUBBLE_ROW_Y = [")
        for r in rows:
            y = min(b[0] for b in r)
            print(f"    {y},  # ({y/h:.3f}h)")
        print("]")

    print(f"\nREF_W = {w}")
    print(f"REF_H = {h}")
    print(f"\nBUBBLE_W ≈ {candidates[0][3]}")
    print(f"BUBBLE_H ≈ {candidates[0][4]}")


# ──────────────────────────────────────────────────────────────────────────────
#  STANDALONE CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="OMR Sheet Evaluator (JPG/PNG/PDF)")
    ap.add_argument("image",       help="Path to OMR image (JPG, PNG, or PDF)")
    ap.add_argument("key",         nargs="?", default=None,
                    help="Path to answer key file (optional)")
    ap.add_argument("--calibrate", action="store_true",
                    help="Run calibration mode to detect grid positions")
    ap.add_argument("--save-graded", default=None,
                    help="Save annotated graded image to this path")
    args = ap.parse_args()

    if args.calibrate:
        calibrate(args.image)
        sys.exit(0)

    # ── Full evaluation mode ──────────────────────────────────
    img, gray = _load_gray(args.image)
    answers   = _detect_answers(gray)

    if args.key:
        key_map     = _load_key(args.key)
        correct     = sum(1 for i, a in enumerate(answers)
                          if a != "-" and key_map.get(i+1) == a)
        wrong       = sum(1 for i, a in enumerate(answers)
                          if a != "-" and key_map.get(i+1) not in (None, a))
        unattempted = answers.count("-")
        total       = len(key_map)
        pct         = correct / total * 100 if total else 0

        # ── Roll number (filename strategy first) ─────────────
        roll = _extract_roll(img, gray, args.image)
        if not roll or len(roll) < 2:
            roll = os.path.splitext(os.path.basename(args.image))[0].upper()

        print(f"\n{'='*55}")
        print(f"  Roll No     : {roll}")
        print(f"  Score       : {correct}/{total}  ({pct:.1f}%)")
        print(f"  Correct     : {correct}")
        print(f"  Wrong       : {wrong}")
        print(f"  Unattempted : {unattempted}")
        print(f"{'='*55}")

        print("\nPer-question:")
        for i, a in enumerate(answers):
            q  = i + 1
            ex = key_map.get(q, "?")
            if a == "-":
                mk = "–"
            elif a == ex:
                mk = "✓"
            else:
                mk = "✗"
            print(f"  Q{q:>3}: selected={a}  expected={ex}  {mk}")

        if args.save_graded:
            out = _draw_marks(img, gray, answers, key_map)
            cv2.imwrite(args.save_graded, out)
            print(f"\nGraded image → {args.save_graded}")

    else:
        # No key — just show detected answers
        roll = _extract_roll(img, gray, args.image)
        print(f"\nRoll No : {roll}")
        print(f"Detected answers (no key provided):")
        print(f"  {answers}")