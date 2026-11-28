"""Validate untrusted Telegram Mini App messages; only local demo actions."""
import json
from urllib.parse import urlsplit


def safe_webapp_url(raw):
    try:
        value = raw.strip()
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and not url.username and not url.password and not url.fragment:
            return value
    except (AttributeError, ValueError):
        pass
    return None


def parse_payload(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        raise ValueError("Invalid mini app payload size")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid mini app payload")
    action = data.get("action")
    if action in ("open_chat", "open_profile") and set(data) == {"action"}:
        return data
    if action == "preview" and set(data) == {"action", "prompt", "ratio"}:
        prompt, ratio = data["prompt"], data["ratio"]
        if isinstance(prompt, str) and 1 <= len(prompt.strip()) <= 1000 and ratio in ("1:1", "16:9", "9:16"):
            return {"action": action, "prompt": prompt.strip(), "ratio": ratio}
    raise ValueError("Unsupported mini app action")
