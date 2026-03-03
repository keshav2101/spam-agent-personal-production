"""
app/features/extractors.py — Risk indicator extraction from parsed emails
"""
import re
from typing import Dict, Any, List
from urllib.parse import urlparse

# URL shortener hostnames
_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "cutt.ly", "rb.gy", "short.io",
}

# Known free webmail providers (deceptive if sender claims corp)
_FREE_PROVIDERS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "protonmail.com", "icloud.com",
}

# Urgency / phishing phrase patterns
_URGENCY_PHRASES = [
    r"urgent", r"immediately", r"act now", r"expires?\s+in",
    r"limited\s+time", r"claim\s+your", r"verify\s+your\s+account",
    r"confirm\s+your", r"suspended", r"unusual\s+(sign[\s-]?in|activity)",
    r"click\s+here", r"click\s+the\s+link", r"won\b", r"winner",
    r"congratulations", r"prize", r"free\s+gift", r"no\s+cost",
    r"100\s*%\s*free", r"risk[\s-]?free", r"guarantee", r"bank account",
    r"password", r"reset\s+your", r"login\s+attempt",
]

_URGENCY_RE = re.compile(
    "|".join(_URGENCY_PHRASES), re.IGNORECASE
)

# Simple IP-in-URL detector
_IP_URL_RE = re.compile(r"https?://(\d{1,3}\.){3}\d{1,3}")


def _extract_urls(text: str) -> List[str]:
    """Returns all URLs found in the text."""
    return re.findall(r"https?://[^\s\"'<>]+", text)


def _sender_domain(sender: str) -> str:
    """Extracts the domain from a From: header like 'Name <user@domain.com>'."""
    match = re.search(r"@([\w.\-]+)", sender)
    return match.group(1).lower() if match else ""


def _reply_to_domain(reply_to: str) -> str:
    match = re.search(r"@([\w.\-]+)", reply_to)
    return match.group(1).lower() if match else ""


def extract_indicators(parsed_email: dict) -> Dict[str, Any]:
    """
    Returns a flat dict of risk indicators for the email.
    All values are JSON-serialisable (bool, int, list of str).
    """
    body    = parsed_email.get("body_text", "")
    sender  = parsed_email.get("sender", "")
    reply_to = parsed_email.get("reply_to", "")
    subject = parsed_email.get("subject", "")

    full_text = f"{subject} {body}"

    # ── URL analysis ──────────────────────────────────────────────────────
    urls = _extract_urls(full_text)
    shortener_urls: List[str] = []
    ip_urls: List[str]         = []
    suspicious_tlds: List[str] = []

    for url in urls:
        host = urlparse(url).hostname or ""
        if host in _SHORTENERS:
            shortener_urls.append(url)
        tld = host.rsplit(".", 1)[-1].lower() if "." in host else ""
        if tld in {"xyz", "top", "click", "zip", "loan", "win", "gq", "tk", "cf", "ml"}:
            suspicious_tlds.append(url)

    ip_urls = _IP_URL_RE.findall(full_text)

    # ── Sender / Reply-To mismatch ────────────────────────────────────────
    s_domain  = _sender_domain(sender)
    rt_domain = _reply_to_domain(reply_to)
    reply_to_mismatch = bool(reply_to) and (s_domain != rt_domain)

    # ── Urgency score ─────────────────────────────────────────────────────
    urgency_hits = _URGENCY_RE.findall(full_text)
    urgency_score = len(urgency_hits)

    # ── Free provider sending as corporate ────────────────────────────────
    free_provider = s_domain in _FREE_PROVIDERS

    # ── Uppercase ratio (shouting) ────────────────────────────────────────
    if len(subject) > 0:
        upper_ratio = sum(1 for c in subject if c.isupper()) / len(subject)
    else:
        upper_ratio = 0.0

    return {
        "url_count":           len(urls),
        "shortener_urls":      shortener_urls,
        "ip_urls":             ip_urls,
        "suspicious_tlds":     suspicious_tlds,
        "reply_to_mismatch":   reply_to_mismatch,
        "urgency_score":       urgency_score,
        "urgency_phrases":     list(set(h.lower() for h in urgency_hits))[:5],
        "free_email_provider": free_provider,
        "subject_upper_ratio": round(upper_ratio, 2),
        "url_shortener_detected": bool(shortener_urls),
        "ip_url_detected":     bool(ip_urls),
    }


def indicators_to_features(indicators: Dict[str, Any]) -> List[str]:
    """Converts an indicators dict into a flat list of human-readable feature strings."""
    feats = []
    if indicators.get("reply_to_mismatch"):
        feats.append("reply_to_mismatch")
    if indicators.get("url_shortener_detected"):
        feats.append("url_shortener_detected")
    if indicators.get("ip_url_detected"):
        feats.append("ip_url_in_body")
    if indicators.get("suspicious_tlds"):
        feats.append("suspicious_tld")
    if indicators.get("urgency_score", 0) >= 2:
        feats.append("high_urgency")
    if indicators.get("free_email_provider"):
        feats.append("free_email_provider")
    if indicators.get("subject_upper_ratio", 0) > 0.5:
        feats.append("excessive_caps")
    return feats
