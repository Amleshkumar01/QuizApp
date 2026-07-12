"""AI helpers for question generation, explanations, and practice suggestions."""

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_SECTION_LABELS = dict(
    aptitude="Quantitative Aptitude",
    reasoning="Logical Reasoning",
    english="Verbal Ability / English",
    coding="Programming & Coding",
    technical="Technical / CS Fundamentals",
)

_COMPANY_PATTERNS = {
    "TCS": "TCS NQT / Ninja pattern — mix of aptitude, logic, and verbal",
    "Infosys": "Infosys SP/DSA — analytical aptitude with strong reasoning",
    "Wipro": "Wipro NLTH — aptitude-heavy with English comprehension",
    "Accenture": "Accenture — cognitive + technical fundamentals",
    "Cognizant": "Cognizant GenC — aptitude, reasoning, and CS basics",
    "Capgemini": "Capgemini — aptitude, English, and technical MCQs",
}


def _gemini_chat(prompt: str) -> str | None:
    """Call Google Gemini (AI Studio) API. Uses GEMINI_API_KEY from settings/env."""
    api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
    if not api_key:
        return None

    model = getattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "You are a senior placement trainer who creates original MCQs for Indian IT "
                            "campus drives (TCS, Infosys, Wipro, etc.). Questions must be clear, unambiguous, "
                            "with one clearly correct option and three plausible distractors. "
                            "Return valid JSON only — no markdown fences, no explanation outside JSON.\n\n"
                            + prompt
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Gemini response: candidates[0].content.parts[0].text
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return text
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("Gemini request failed: %s", exc)
        return None


def _openai_chat(prompt: str) -> str | None:
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not api_key:
        return None

    model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior placement trainer who creates original MCQs for Indian IT "
                    "campus drives (TCS, Infosys, Wipro, etc.). Questions must be clear, unambiguous, "
                    "with one clearly correct option and three plausible distractors. "
                    "Return valid JSON only — no markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.65,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("OpenAI request failed: %s", exc)
        return None


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(text)
    if isinstance(data, dict) and "questions" in data:
        return data["questions"]
    if isinstance(data, list):
        return data
    raise ValueError("Unexpected AI response shape")


def strip_question_number(text: str) -> str:
    return re.sub(r"^Q\d+\.\s*", "", (text or "").strip(), flags=re.IGNORECASE)


def number_question_items(items: list[dict], start: int = 1) -> list[dict]:
    """Prefix each question text with Q1., Q2., …"""
    numbered = []
    for i, item in enumerate(items):
        clean = strip_question_number(str(item.get("text", "")))
        numbered.append({**item, "text": f"Q{start + i}. {clean}"})
    return numbered


def _section_question_bank(section: str, company_name: str, difficulty: str) -> list[dict]:
    """High-quality fallback MCQs per section (used when OpenAI is unavailable)."""
    banks: dict[str, list[dict]] = {
        "aptitude": [
            {
                "text": "A shopkeeper marks an article 40% above cost price and gives 10% discount. Profit % is?",
                "topic": "Profit & Loss",
                "options": ["26%", "30%", "36%", "40%"],
                "correct_index": 0,
                "explanation": "MP = 140, SP = 140×0.9 = 126 → profit 26% on CP 100.",
            },
            {
                "text": "Pipe A fills a tank in 12 h, Pipe B in 18 h. Together they fill it in?",
                "topic": "Time & Work",
                "options": ["7.2 h", "8 h", "9 h", "10 h"],
                "correct_index": 0,
                "explanation": "Combined rate 1/12 + 1/18 = 5/36 → time = 36/5 = 7.2 h.",
            },
            {
                "text": "Average of 5 numbers is 27. If one number 35 is replaced by 15, new average is?",
                "topic": "Averages",
                "options": ["23", "25", "27", "29"],
                "correct_index": 0,
                "explanation": "Sum drops by 20 → new sum 135-20=115 → avg 115/5=23.",
            },
            {
                "text": "Simple interest on ₹8000 at 5% for 3 years is?",
                "topic": "Simple Interest",
                "options": ["₹1000", "₹1200", "₹1400", "₹1600"],
                "correct_index": 1,
                "explanation": "SI = P×R×T/100 = 8000×5×3/100 = ₹1200.",
            },
            {
                "text": "HCF of 36 and 84 is?",
                "topic": "HCF & LCM",
                "options": ["6", "12", "18", "24"],
                "correct_index": 1,
                "explanation": "36=2²×3², 84=2²×3×7 → HCF = 2²×3 = 12.",
            },
        ],
        "reasoning": [
            {
                "text": "If CLOUD is coded as DMPVE, how is RAIN coded?",
                "topic": "Coding-Decoding",
                "options": ["SBJO", "QZHM", "SBKO", "TCKP"],
                "correct_index": 0,
                "explanation": "Each letter +1: R→S, A→B, I→J, N→O.",
            },
            {
                "text": "Find the odd one out: Square, Triangle, Circle, Rectangle",
                "topic": "Classification",
                "options": ["Square", "Triangle", "Circle", "Rectangle"],
                "correct_index": 2,
                "explanation": "Circle has no straight sides; others are polygons.",
            },
            {
                "text": "A is taller than B. C is shorter than B. Who is shortest?",
                "topic": "Ranking",
                "options": ["A", "B", "C", "Cannot say"],
                "correct_index": 2,
                "explanation": "A > B > C → C is shortest.",
            },
            {
                "text": "Series: 3, 9, 27, 81, ?",
                "topic": "Number Series",
                "options": ["162", "243", "324", "108"],
                "correct_index": 1,
                "explanation": "Multiply by 3 each time → 81×3 = 243.",
            },
            {
                "text": "If South-East becomes North, North-East becomes West, what does South become?",
                "topic": "Direction Sense",
                "options": ["North", "East", "West", "North-West"],
                "correct_index": 0,
                "explanation": "135° anti-clockwise rotation → South (180°) becomes North (0°).",
            },
        ],
        "english": [
            {
                "text": "Choose the word nearest in meaning to 'PRAGMATIC'.",
                "topic": "Synonyms",
                "options": ["Idealistic", "Practical", "Aggressive", "Lazy"],
                "correct_index": 1,
                "explanation": "Pragmatic means practical and realistic.",
            },
            {
                "text": "Fill in the blank: He abstained _____ voting in the election.",
                "topic": "Prepositions",
                "options": ["from", "for", "to", "with"],
                "correct_index": 0,
                "explanation": "Correct phrase: abstain from (something).",
            },
            {
                "text": "Identify the error: 'Each of the boys have submitted their forms.'",
                "topic": "Grammar",
                "options": ["Each of the boys", "have submitted", "their forms", "No error"],
                "correct_index": 1,
                "explanation": "'Each' takes singular verb → 'has submitted'.",
            },
            {
                "text": "Antonym of 'VERBOSE' is?",
                "topic": "Antonyms",
                "options": ["Talkative", "Concise", "Lengthy", "Wordy"],
                "correct_index": 1,
                "explanation": "Verbose = using too many words; concise = brief.",
            },
            {
                "text": "Rearrange meaning: P / nation / the / built / leaders / by / was / great",
                "topic": "Para Jumbles",
                "options": [
                    "The nation was built by great leaders",
                    "Great leaders was built the nation",
                    "The great nation built by leaders",
                    "Leaders built nation the great",
                ],
                "correct_index": 0,
                "explanation": "Correct sentence: The nation was built by great leaders.",
            },
        ],
        "coding": [
            {
                "text": "What is the output of: print(2 ** 3 ** 2)?",
                "topic": "Python Operators",
                "options": ["64", "512", "256", "128"],
                "correct_index": 1,
                "explanation": "Exponent is right-associative: 2**(3**2) = 2^9 = 512.",
            },
            {
                "text": "Which data structure uses FIFO order?",
                "topic": "Data Structures",
                "options": ["Stack", "Queue", "Tree", "Graph"],
                "correct_index": 1,
                "explanation": "Queue: First In First Out.",
            },
            {
                "text": "Time complexity of accessing an element in array by index?",
                "topic": "Complexity",
                "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"],
                "correct_index": 0,
                "explanation": "Arrays allow O(1) random access by index.",
            },
            {
                "text": "Which keyword is used to define a constant in C?",
                "topic": "C Programming",
                "options": ["const", "static", "final", "define only"],
                "correct_index": 0,
                "explanation": "C uses 'const' for read-only variables.",
            },
            {
                "text": "Output of: for(i=0;i<3;i++); printf('%d',i); in C?",
                "topic": "Loops",
                "options": ["012", "3", "2", "123"],
                "correct_index": 1,
                "explanation": "Loop runs i=0,1,2 then i becomes 3 → prints 3.",
            },
        ],
        "technical": [
            {
                "text": "Which layer of OSI model handles routing?",
                "topic": "Networking",
                "options": ["Data Link", "Network", "Transport", "Session"],
                "correct_index": 1,
                "explanation": "Network layer (Layer 3) handles routing.",
            },
            {
                "text": "Primary key in a database table must be?",
                "topic": "DBMS",
                "options": ["Nullable", "Unique & Not Null", "Duplicate allowed", "Optional"],
                "correct_index": 1,
                "explanation": "Primary key uniquely identifies rows; cannot be NULL.",
            },
            {
                "text": "Deadlock occurs when processes are?",
                "topic": "Operating Systems",
                "options": [
                    "Waiting for CPU only",
                    "Waiting for each other's resources",
                    "Terminated",
                    "In ready queue",
                ],
                "correct_index": 1,
                "explanation": "Circular wait for resources causes deadlock.",
            },
            {
                "text": "HTTP status code 404 means?",
                "topic": "Web Technologies",
                "options": ["OK", "Forbidden", "Not Found", "Server Error"],
                "correct_index": 2,
                "explanation": "404 = requested resource not found.",
            },
            {
                "text": "In OOP, hiding internal details is called?",
                "topic": "OOP",
                "options": ["Inheritance", "Polymorphism", "Encapsulation", "Abstraction only"],
                "correct_index": 2,
                "explanation": "Encapsulation bundles data and hides implementation.",
            },
        ],
    }

    pool = banks.get(section, banks["aptitude"])
    diff_note = {"easy": "straightforward", "medium": "standard placement", "hard": "challenging"}.get(
        difficulty, "standard placement"
    )
    out = []
    for i in range(len(pool)):
        q = dict(pool[i % len(pool)])
        q["topic"] = f"{company_name} — {q['topic']}"
        q["explanation"] = f"{q['explanation']} ({diff_note} level for {company_name}.)"
        out.append(q)
    return out


def _fallback_questions(company_name: str, section: str, difficulty: str, count: int) -> list[dict]:
    items = _section_question_bank(section, company_name, difficulty)
    return items[:count]


def generate_questions(company_name: str, section: str, difficulty: str, count: int) -> tuple[list[dict], str]:
    """
    Returns (questions, source) where source is 'ai' or 'fallback'.
    Each question: text, topic, options, correct_index, explanation (numbered Q1…).
    """
    count = max(1, min(int(count), 20))
    label = _SECTION_LABELS.get(section, section.title())
    pattern = _COMPANY_PATTERNS.get(company_name, f"{company_name} campus placement test pattern")

    prompt = f"""Create {count} original multiple-choice questions for {company_name} campus placement.

Test pattern: {pattern}
Section: {label}
Difficulty: {difficulty}

Requirements:
- Realistic questions similar to actual {company_name} drives
- Clear stem, exactly 4 options, only ONE correct answer
- Distractors must be plausible (common mistakes)
- Include brief explanation with solution steps
- Topics varied within {label}
- Do NOT number questions in text (numbering added separately)

Return JSON array:
[
  {{
    "text": "question without Q number",
    "topic": "short topic e.g. Percentages",
    "options": ["A", "B", "C", "D"],
    "correct_index": 0,
    "explanation": "step-by-step solution"
  }}
]
"""
    # Try Gemini (Google AI Studio) first, then OpenAI as fallback.
    raw = _gemini_chat(prompt) or _openai_chat(prompt)
    if raw:
        try:
            items = _parse_json_array(raw)
            cleaned = []
            for item in items[:count]:
                opts = [str(o).strip() for o in item.get("options", []) if str(o).strip()]
                if len(opts) < 2:
                    continue
                while len(opts) < 4:
                    opts.append(f"Option {len(opts) + 1}")
                ci = int(item.get("correct_index", 0))
                ci = max(0, min(ci, len(opts) - 1))
                text = strip_question_number(str(item.get("text", "")).strip())
                if not text:
                    continue
                cleaned.append(
                    {
                        "text": text,
                        "topic": str(item.get("topic", label)).strip() or label,
                        "options": opts[:4],
                        "correct_index": ci,
                        "explanation": str(item.get("explanation", "")).strip(),
                    }
                )
            if cleaned:
                return cleaned[:count], "ai"
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("Failed to parse AI questions: %s", exc)

    return _fallback_questions(company_name, section, difficulty, count), "fallback"


def explain_answer(question_text: str, correct_option: str, selected_option: str) -> str:
    if not selected_option:
        return "You did not answer this question."
    if selected_option == correct_option:
        return "Correct! Well done."

    prompt = f"""Student answered a placement MCQ incorrectly.

Question: {strip_question_number(question_text)}
Correct answer: {correct_option}
Student chose: {selected_option}

Write a clear 2-3 sentence explanation for the student (friendly, educational). Plain text only."""
    raw = _openai_chat(prompt)
    if raw:
        return raw.strip()
    return (
        f"The correct answer is «{correct_option}». "
        f"You selected «{selected_option}». Review the concept and try similar practice questions."
    )


def personalized_suggestions(weak_topics: list[str], company_name: str | None = None) -> list[str]:
    topics = ", ".join(weak_topics[:8]) or "general aptitude"
    company_part = f" for {company_name}" if company_name else ""
    prompt = f"""A campus placement student is weak in: {topics}.
Suggest 5 specific practice actions{company_part} (short bullet strings, actionable).
Return JSON array of 5 strings."""
    raw = _openai_chat(prompt)
    if raw:
        try:
            items = _parse_json_array(raw)
            return [str(x).strip() for x in items if str(x).strip()][:5]
        except (ValueError, json.JSONDecodeError):
            pass

    return [
        f"Practice 15 {weak_topics[0]} questions daily" if weak_topics else "Start with Easy Aptitude tests",
        "Review solutions and note formulas or rules you miss",
        "Attempt one timed mock test every 2 days",
        "Focus on accuracy first, then improve speed",
        f"Re-attempt {company_name} Medium tests after improving weak topics" if company_name else "Pick one company and follow its test pattern",
    ]
