"""
app/parsing/email_parser.py — MIME + HTML email parser with SHA-256 hashing
"""
import email
import hashlib
from bs4 import BeautifulSoup
from typing import Dict, Any


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def parse_raw_email(raw_content: str) -> Dict[str, Any]:
    """
    Parses a raw RFC-822 email string. Returns:
      sender, reply_to, subject, message_id, body_text,
      header_hash, body_hash
    """
    msg = email.message_from_string(raw_content)

    # ── Headers ───────────────────────────────────────────────────────────────
    sender     = msg.get("From", "")
    reply_to   = msg.get("Reply-To", "")
    subject    = msg.get("Subject", "")
    message_id = msg.get("Message-ID", "").strip("<>")

    # Hash the raw header block (everything before first blank line)
    header_block = raw_content.split("\n\n", 1)[0]
    header_hash  = _sha256(header_block)

    # ── Body extraction ───────────────────────────────────────────────────────
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore"
                    )
                    if ct == "text/html":
                        payload = BeautifulSoup(payload, "html.parser").get_text(
                            separator=" ", strip=True
                        )
                    body += payload + "\n"
                except Exception:
                    pass
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="ignore"
            )
            if msg.get_content_type() == "text/html":
                body = BeautifulSoup(body, "html.parser").get_text(
                    separator=" ", strip=True
                )

    body_hash = _sha256(body)

    return {
        "sender":     sender,
        "reply_to":   reply_to,
        "subject":    subject,
        "message_id": message_id,
        "body_text":  body.strip(),
        "header_hash": header_hash,
        "body_hash":   body_hash,
    }
