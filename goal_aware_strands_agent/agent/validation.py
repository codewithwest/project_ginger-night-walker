"""Independent argument and safety checks; add schema validation without third-party deps."""
from urllib.parse import urlsplit

class ValidationError(ValueError): pass

def validate_arguments(args, schema):
    if not isinstance(args, dict): raise ValidationError("Tool arguments must be an object")
    schema = schema or {}
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args: raise ValidationError(f"Missing required argument: {key}")
    if schema.get("additionalProperties") is False:
        extra = set(args) - set(properties)
        if extra: raise ValidationError(f"Unsupported arguments: {sorted(extra)}")
    type_map = {"string": str, "integer": int, "number": (int,float), "boolean": bool,
                "array": list, "object": dict}
    for key, value in args.items():
        spec = properties.get(key, {})
        expected = spec.get("type")
        if expected in type_map and (not isinstance(value, type_map[expected]) or (expected in ("integer", "number") and isinstance(value, bool))):
            raise ValidationError(f"Wrong type for {key}: expected {expected}")
        if "enum" in spec and value not in spec["enum"]:
            raise ValidationError(f"Invalid enum for {key}")
    return args

def enforce_navigation_policy(name, args):
    if name == "browser_navigate":
        u = urlsplit(args.get("url", ""))
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValidationError("Navigation needs an http(s) URL")
        if u.username or u.password: raise ValidationError("URLs with embedded credentials are blocked")
    return args


def observed_refs(content):
    import re
    return set(re.findall(r"\bref[=:]\s*([A-Za-z0-9_-]+)", content or ""))

def enforce_grounded_reference(name, args, latest_snapshot):
    if name in ("browser_click", "browser_type", "browser_select_option"):
        ref = args.get("ref")
        if ref and ref not in observed_refs(latest_snapshot):
            raise ValidationError(f"Element ref {ref!r} not present in latest snapshot")
    return args
