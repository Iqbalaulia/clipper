"""
virality.py — Virality scoring for clips without requiring an API key.

Uses local heuristics based on:
  - Clip duration (ideal 30-60s)
  - Hook title strength (length, power words, casing)
  - Transcript content (power keywords, questions, numbers, emotion)
  - Structural quality (sentence boundaries, speaking pace)

Public API:
    score_moment(start_sec, end_sec, hook_title, transcript_segments=None, video_title="")
"""

import re
from typing import Optional

# Ideal clip duration window for short-form platforms (TikTok/Reels/Shorts)
_OPTIMAL_MIN = 30
_OPTIMAL_MAX = 60
_OK_MIN = 15
_OK_MAX = 90

# Power words that tend to drive clicks / retention in Indonesian and English
_POWER_WORDS = {
    # Indonesian
    "syok", "kaget", "terbongkar", "rahasia", "ketaahuan", "ketahuan", "bohong",
    "fakta", "gila", "hancur", "viral", "menghancurkan", "mengejutkan", "panas",
    "api", "terkuak", "dibongkar", "misteri", "tragis", "kontroversial", "skandal",
    "bukan main", "ngeri", "sadis", "luar biasa", "tak terduga", "plot twist",
    "jangan skip", "tonton sampai", "pasti kaget", "bakal kaget", "auto viral",
    # English
    "shocking", "revealed", "secret", "exposed", "insane", "destroyed", "viral",
    "surprising", "mystery", "controversial", "scandal", "unbelievable", "must watch",
    "plot twist", "mind blowing", "you won't believe", "wait for it", "don't skip",
    "crazy", "brutal", "unexpected", "truth",
}

# Question words that indicate curiosity gap
_QUESTION_WORDS = {"apa", "siapa", "mengapa", "kenapa", "bagaimana", "berapa",
                   "what", "who", "why", "how", "when", "where", "which"}

# Emotion markers
_EMOTION_MARKS = {"!", "?", "…", ".."}


def _word_count(text: str) -> int:
    return len(text.split())


def _contains_any(text: str, words: set) -> bool:
    lowered = text.lower()
    for w in words:
        if w in lowered:
            return True
    return False


def _contains_word(text: str, words: set) -> bool:
    """Check if any whole word from the set exists in text (word boundary aware)."""
    lowered = text.lower()
    for w in words:
        if re.search(r"\b" + re.escape(w) + r"\b", lowered):
            return True
    return False


def _duration_score(duration: float) -> int:
    if _OPTIMAL_MIN <= duration <= _OPTIMAL_MAX:
        return 30
    if _OK_MIN <= duration < _OPTIMAL_MIN or _OPTIMAL_MAX < duration <= _OK_MAX:
        return 20
    if 5 <= duration < _OK_MIN or _OK_MAX < duration <= 180:
        return 10
    return 0


def _hook_score(hook_title: str) -> int:
    if not hook_title:
        return 0
    wc = _word_count(hook_title)
    score = 0

    # Length sweet spot for short-form: 3-6 words
    if 3 <= wc <= 6:
        score += 15
    elif 7 <= wc <= 10:
        score += 10
    elif wc > 0:
        score += 5

    # Power words / clickbait-positive terms
    if _contains_any(hook_title, _POWER_WORDS):
        score += 12

    # All-caps impact
    alpha_chars = [c for c in hook_title if c.isalpha()]
    if alpha_chars and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) >= 0.7:
        score += 3

    return min(score, 30)


def _transcript_score(transcript_segments: Optional[list], duration: float) -> int:
    if not transcript_segments:
        return 10  # neutral baseline when transcript unavailable

    texts = [seg.get("text", "") for seg in transcript_segments if seg.get("text")]
    full_text = " ".join(texts).strip()
    if not full_text:
        return 10

    score = 10  # baseline

    # Keyword density
    power_hits = sum(1 for w in _POWER_WORDS if re.search(r"\b" + re.escape(w) + r"\b", full_text.lower()))
    score += min(power_hits * 3, 12)

    # Questions create curiosity gap
    if _contains_word(full_text, _QUESTION_WORDS):
        score += 4

    # Numbers / concrete facts drive credibility
    if re.search(r"\b\d+(?:\.\d+)?(?:%|rb|jt|miliar|juta|million|billion|k)?\b", full_text, re.IGNORECASE):
        score += 4

    # Emotional punctuation / intensity
    if any(m in full_text for m in _EMOTION_MARKS):
        score += 4

    # Complete clip starts and ends with sentences
    first_text = (texts[0] or "").strip()
    last_text = (texts[-1] or "").strip()
    if first_text and first_text[0].isupper():
        score += 3
    if last_text and re.search(r"[.?!…]\s*$", last_text):
        score += 3

    return min(score, 30)


def _structural_score(transcript_segments: Optional[list], duration: float) -> int:
    if not transcript_segments or duration <= 0:
        return 3

    full_text = " ".join(seg.get("text", "") for seg in transcript_segments).strip()
    word_count = _word_count(full_text)
    wpm = (word_count / duration) * 60.0 if duration > 0 else 0

    score = 0
    # Complete thought: starts with capitalized word and ends with sentence punctuation
    starts_sentence = bool(full_text and full_text[0].isupper())
    ends_sentence = bool(re.search(r"[.?!…]\s*$", full_text))
    if starts_sentence and ends_sentence:
        score += 5
    elif starts_sentence or ends_sentence:
        score += 3

    # Good speaking pace ~100-170 WPM
    if 100 <= wpm <= 170:
        score += 5
    elif 80 <= wpm < 100 or 170 < wpm <= 200:
        score += 3
    else:
        score += 1

    return min(score, 10)


def _badge_for_score(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _reason_from_breakdown(breakdown: dict, hook_title: str) -> str:
    """Generate a one-sentence human-readable reason."""
    parts = []
    duration = breakdown.get("duration", 0)
    if duration >= 30:
        parts.append(f"durasi {duration:.0f} detik pas untuk short-form")
    else:
        parts.append(f"durasi {duration:.0f} detik")

    hook = breakdown.get("hook", 0)
    if hook >= 20:
        parts.append("hook title kuat")
    elif hook >= 10:
        parts.append("hook title cukup menarik")

    content = breakdown.get("content", 0)
    if content >= 20:
        parts.append("konten penuh momen emosional atau FOMO")
    elif content >= 12:
        parts.append("konten memiliki daya tarik tersendiri")

    structure = breakdown.get("structure", 0)
    if structure >= 7:
        parts.append("struktur kalimat utuh dan pacing bagus")

    if hook_title:
        return f"\"{hook_title}\" — {', '.join(parts)}."
    return f"{', '.join(parts)}."


def score_moment(
    start_sec: float,
    end_sec: float,
    hook_title: str = "",
    transcript_segments: Optional[list] = None,
    video_title: str = "",
) -> dict:
    """
    Score a clip moment from 0-100 based on local heuristics.

    Args:
        start_sec: clip start in seconds
        end_sec: clip end in seconds
        hook_title: generated hook title for the clip
        transcript_segments: list of {start, end, text} dicts overlapping the clip
        video_title: original video title (currently unused, reserved for future AI hybrid)

    Returns:
        dict with keys: score (int), reason (str), badge (str), breakdown (dict)
    """
    duration = max(0.0, end_sec - start_sec)

    duration_score = _duration_score(duration)
    hook_score = _hook_score(hook_title)
    content_score = _transcript_score(transcript_segments, duration)
    structure_score = _structural_score(transcript_segments, duration)

    total = duration_score + hook_score + content_score + structure_score
    total = max(0, min(100, total))

    breakdown = {
        "duration": duration,
        "duration_score": duration_score,
        "hook": hook_score,
        "content": content_score,
        "structure": structure_score,
    }

    return {
        "score": total,
        "reason": _reason_from_breakdown(breakdown, hook_title),
        "badge": _badge_for_score(total),
        "breakdown": breakdown,
    }
