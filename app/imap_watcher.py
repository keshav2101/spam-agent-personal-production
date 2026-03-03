"""
app/imap_watcher.py — Gmail IMAP polling loop for personal spam agent
──────────────────────────────────────────────────────────────────────
Polls Gmail INBOX for UNSEEN messages, triages each via the ML model,
then moves to Gmail labels (Quarantine / Review) using IMAP COPY+DELETE.

Gmail IMAP quirks handled:
  - Gmail labels appear as IMAP folders; COPY to label name moves the email.
  - "[Gmail]/All Mail" always holds all messages; we only touch INBOX + labels.
  - After COPY + \\Deleted + EXPUNGE the email disappears from INBOX but
    remains visible in Gmail under the target label.

Configure via .env:
  IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD (App Password!)
  IMAP_INBOX, GMAIL_LABEL_QUARANTINE, GMAIL_LABEL_REVIEW, POLL_SECONDS
"""

import imaplib
import logging
import os
import time
import uuid
import asyncio
from email import message_from_bytes

from dotenv import load_dotenv
load_dotenv()

from app.parsing.email_parser   import parse_raw_email
from app.features.extractors    import extract_indicators, indicators_to_features
from app.ml.model               import classifier
from app.db.session             import SessionLocal
from app.db.crud                import create_audit

logger = logging.getLogger("imap_watcher")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
IMAP_HOST   = os.getenv("IMAP_HOST",   "imap.gmail.com")
IMAP_PORT   = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER   = os.getenv("IMAP_USER",   "")
IMAP_PASS   = os.getenv("IMAP_PASSWORD", "")   # never logged
IMAP_INBOX  = os.getenv("IMAP_INBOX",  "INBOX")
LABEL_SPAM  = os.getenv("GMAIL_LABEL_QUARANTINE", "Quarantine")
LABEL_REVIEW= os.getenv("GMAIL_LABEL_REVIEW",     "Review")
POLL_SECS   = int(os.getenv("POLL_SECONDS", "30"))


def _connect() -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(IMAP_USER, IMAP_PASS)   # password never printed
    logger.info(f"IMAP connected as {IMAP_USER}")
    return mail


def _ensure_label(mail: imaplib.IMAP4_SSL, label: str):
    """Creates the Gmail label/folder if it doesn't already exist."""
    typ, _ = mail.select(label)
    if typ != "OK":
        mail.create(label)
        logger.info(f"Created Gmail label: {label}")


def _move_to_label(mail: imaplib.IMAP4_SSL, uid: bytes, label: str):
    """Gmail COPY+DELETE+EXPUNGE = move to label."""
    mail.uid("COPY", uid, label)
    mail.uid("STORE", uid, "+FLAGS", "\\Deleted")
    mail.expunge()


def _triage_email(raw_bytes: bytes) -> dict:
    """
    Runs the full triage pipeline on a raw email.
    Returns result dict with action, email_id, etc.
    """
    raw_text  = raw_bytes.decode("utf-8", errors="ignore")
    parsed    = parse_raw_email(raw_text)
    indicators = extract_indicators(parsed)
    features  = indicators_to_features(indicators)

    email_id = str(uuid.uuid4())

    try:
        result = classifier.predict(parsed["body_text"] + " " + parsed["subject"])
        prob   = result["spam_probability"]
        ver    = result["model_version"]
    except RuntimeError:
        # MODEL_NOT_LOADED — do not label email, log and skip
        logger.error("MODEL_NOT_LOADED — skipping IMAP label action for this email.")
        return {
            "email_id": email_id,
            "action":   "MODEL_NOT_LOADED",
            "prob":     0.5,
            "version":  "none",
            "parsed":   parsed,
            "indicators": indicators,
            "features": features,
        }

    if prob >= 0.90:
        action = "QUARANTINE"
    elif prob <= 0.10:
        action = "DELIVER"
    else:
        action = "UNCERTAIN"

    explanation = _make_explanation(prob, action, features)

    # Persist audit record
    db = SessionLocal()
    try:
        create_audit(
            db=db,
            email_id=email_id,
            message_id=parsed.get("message_id"),
            sender=parsed.get("sender"),
            subject=parsed.get("subject"),
            spam_probability=prob,
            action=action,
            model_version=ver,
            indicators=indicators,
            explanation=explanation,
            header_hash=parsed.get("header_hash"),
            body_hash=parsed.get("body_hash"),
            body_text=parsed.get("body_text"),
        )
    finally:
        db.close()

    return {
        "email_id": email_id,
        "action":   action,
        "prob":     prob,
        "version":  ver,
        "parsed":   parsed,
        "indicators": indicators,
        "features": features,
    }


def _make_explanation(prob: float, action: str, features: list) -> str:
    feat_str = ", ".join(features) if features else "none detected"
    if action == "QUARANTINE":
        return f"High spam probability ({prob*100:.1f}%). Indicators: {feat_str}."
    elif action == "DELIVER":
        return f"Low spam probability ({prob*100:.1f}%). Message appears safe."
    else:
        return f"Uncertain ({prob*100:.1f}%). Routed for manual review. Signals: {feat_str}."


def process_unseen(mail: imaplib.IMAP4_SSL):
    mail.select(IMAP_INBOX)
    _, uids_data = mail.uid("SEARCH", None, "UNSEEN")
    uid_list = uids_data[0].split()

    if not uid_list:
        logger.debug("No new messages.")
        return

    logger.info(f"Processing {len(uid_list)} new message(s)…")
    for uid in uid_list:
        try:
            _, data = mail.uid("FETCH", uid, "(RFC822)")
            raw_bytes = data[0][1]
            result = _triage_email(raw_bytes)

            action   = result["action"]
            email_id = result["email_id"]
            sender   = result["parsed"].get("sender", "?")
            prob     = result["prob"]
            logger.info(f"  [{email_id[:8]}] {action} p={prob:.2f}  from={sender[:40]}")

            if action == "QUARANTINE":
                _move_to_label(mail, uid, LABEL_SPAM)
            elif action == "UNCERTAIN":
                _move_to_label(mail, uid, LABEL_REVIEW)
            elif action == "DELIVER":
                mail.uid("STORE", uid, "+FLAGS", "\\Seen")
            # MODEL_NOT_LOADED → do nothing, leave email unread

        except Exception as exc:
            logger.error(f"  Failed to process UID {uid}: {exc}")


def run_watcher():
    """Main loop with exponential backoff on IMAP errors."""
    if not IMAP_USER:
        logger.error("IMAP_USER not set in .env — IMAP watcher not started.")
        return

    logger.info(f"Starting IMAP watcher — poll every {POLL_SECS}s")
    backoff = 30

    while True:
        try:
            mail = _connect()
            _ensure_label(mail, LABEL_SPAM)
            _ensure_label(mail, LABEL_REVIEW)
            backoff = 30  # reset on success

            while True:
                process_unseen(mail)
                time.sleep(POLL_SECS)

        except imaplib.IMAP4.abort:
            logger.warning("IMAP connection aborted — reconnecting…")
        except Exception as exc:
            logger.error(f"IMAP error: {exc}. Retrying in {backoff}s…")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)  # max 5-minute backoff


if __name__ == "__main__":
    run_watcher()
