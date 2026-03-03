"""
test.py — Boundary condition tests for personal spam agent
Run with: python test.py  (server must be running on :8000)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx

BASE = "http://127.0.0.1:8000"

SPAM_EMAIL = """From: winner@lottery-scam.xyz
Reply-To: collect@different-domain.top
Subject: !!! YOU WON $10,000 !!! CLAIM IMMEDIATELY
Content-Type: text/plain

CONGRATULATIONS! You have been selected as our lucky winner!
CLICK HERE IMMEDIATELY to claim your FREE $10,000 prize: http://bit.ly/win-prize
This offer EXPIRES in 5 minutes. Act NOW! Verify your bank account to receive funds.
http://192.168.1.1/claim?token=abc123
"""

HAM_EMAIL = """From: alice@mycompany.com
Subject: Q4 Budget Review - Meeting Notes
Content-Type: text/plain

Hi team,

Please find the meeting notes from yesterday's Q4 budget review attached.
The key decisions were:
1. Increase R&D allocation by 8%
2. Defer office renovation to Q1 next year

Let me know if you have questions.

Best,
Alice
"""

UNCERTAIN_EMAIL = """From: newsletter@bigshop.com
Subject: New arrivals this week
Content-Type: text/plain

Check out our new products this week. Some great deals available.
Visit our site for more details. Limited quantities available.
"""


def test(name: str, email_text: str, expected_contains: list[str]):
    print(f"\n{'='*55}")
    print(f"TEST: {name}")
    r = httpx.post(f"{BASE}/triage", json={"email_raw": email_text})
    data = r.json()
    action = data.get("action", "ERROR")
    prob   = data.get("spam_probability", -1)
    model  = data.get("model_version", "?")
    print(f"  Action:      {action}")
    print(f"  Probability: {prob:.4f}")
    print(f"  Model:       {model}")
    print(f"  Explanation: {data.get('explanation','')[:80]}")
    feats = data.get("indicators", {})
    print(f"  Indicators:  reply_to_mismatch={feats.get('reply_to_mismatch')}  "
          f"urgency={feats.get('urgency_score')}  shortener={feats.get('url_shortener_detected')}")
    for exp in expected_contains:
        ok = exp in action
        print(f"  {'✓' if ok else '✗'} Expected action contains '{exp}': {ok}")
    return data


def test_health():
    print(f"\n{'='*55}")
    print("TEST: Health Check")
    r = httpx.get(f"{BASE}/health")
    d = r.json()
    print(f"  Status:       {d.get('status')}")
    print(f"  Model loaded: {d.get('model_loaded')}")
    print(f"  Model ver:    {d.get('model_version')}")
    print(f"  DB:           {d.get('db')}")


def test_feedback(email_id: str):
    print(f"\n{'='*55}")
    print("TEST: Feedback Submission")
    r = httpx.post(f"{BASE}/feedback", json={
        "email_id": email_id,
        "corrected_label": "HAM",
        "reason": "automated test false positive correction"
    })
    print(f"  Status: {r.status_code} — {r.json()}")


def test_audit(email_id: str):
    print(f"\n{'='*55}")
    print("TEST: Audit Record Retrieval")
    r = httpx.get(f"{BASE}/audit/{email_id}")
    d = r.json()
    print(f"  email_id:   {d.get('email_id','?')[:16]}…")
    print(f"  action:     {d.get('action')}")
    print(f"  body_hash:  {d.get('body_hash','?')[:20]}…")
    print(f"  header_hash:{d.get('header_hash','?')[:20]}…")


if __name__ == "__main__":
    print("Personal Spam Agent — Boundary Tests")

    test_health()

    spam_result   = test("High-Confidence SPAM (expect QUARANTINE)",   SPAM_EMAIL,     ["QUARANTINE"])
    ham_result    = test("High-Confidence HAM (expect DELIVER)",        HAM_EMAIL,      ["DELIVER"])
    unsure_result = test("Ambiguous email (expect UNCERTAIN or DELIVER)", UNCERTAIN_EMAIL, ["UNCERTAIN", "DELIVER"])

    # Use the spam email's ID for feedback + audit tests
    email_id = spam_result.get("email_id")
    if email_id:
        test_feedback(email_id)
        test_audit(email_id)

    print(f"\n{'='*55}")
    print("All tests complete.")
    print("\nNOTE: If action=MODEL_NOT_LOADED, run: python -m app.ml.train")
