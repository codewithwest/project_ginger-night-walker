"""Permissive parsing for a 0.6B model; never infer completion from ambiguous output."""
import json
import re


def parse_object(text):
    if isinstance(text, dict): return text
    if not isinstance(text, str): return {}
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I)
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict): return parsed
    except (ValueError, TypeError): pass
    start = s.find("{")
    if start < 0: return {}
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(s[start:])
        return parsed if isinstance(parsed, dict) else {}
    except ValueError: return {}


def strict_bool(value):
    if isinstance(value, bool): return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def short_string(value, limit=400):
    return str(value or "")[:limit]
