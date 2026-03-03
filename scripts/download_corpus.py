"""
scripts/download_corpus.py
Downloads and extracts SpamAssassin public corpus into data/spam/ and data/ham/.
Run from the spam_agent_personal/ root:
    python scripts/download_corpus.py
"""
import sys, io
# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeError
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import urllib.request, tarfile, os, pathlib

CORPUS = [
    {
        "url":    "https://spamassassin.apache.org/old/publiccorpus/20030228_spam.tar.bz2",
        "dest":   "data/spam",
        "label":  "spam",
    },
    {
        "url":    "https://spamassassin.apache.org/old/publiccorpus/20030228_easy_ham.tar.bz2",
        "dest":   "data/ham",
        "label":  "ham (easy)",
    },
    {
        "url":    "https://spamassassin.apache.org/old/publiccorpus/20030228_hard_ham.tar.bz2",
        "dest":   "data/ham",
        "label":  "ham (hard)",
    },
]

def download_and_extract(url: str, dest: str, label: str):
    os.makedirs(dest, exist_ok=True)
    filename = url.split("/")[-1]
    tmp_path = f"/tmp/{filename}" if sys.platform != "win32" else f"C:\\Temp\\{filename}"
    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)

    if not os.path.exists(tmp_path):
        print(f"  Downloading {label}: {filename} …", flush=True)
        urllib.request.urlretrieve(url, tmp_path)
        print(f"  ✓ Downloaded {filename}")
    else:
        print(f"  Already exists: {filename}")

    print(f"  Extracting to {dest}/ …", flush=True)
    with tarfile.open(tmp_path, "r:bz2") as tar:
        # Strip the top-level folder name if present
        members = tar.getmembers()
        for m in members:
            # Skip directories
            if m.isdir():
                continue
            # Flatten: put all files directly into dest/
            m.name = os.path.basename(m.name)
            if not m.name or m.name.startswith("."):
                continue
            tar.extract(m, path=dest)
    print(f"  ✓ Extracted {label}")


if __name__ == "__main__":
    print("Downloading SpamAssassin public corpus…\n")
    for item in CORPUS:
        try:
            download_and_extract(item["url"], item["dest"], item["label"])
        except Exception as e:
            print(f"  ✗ Failed to download {item['label']}: {e}")

    # Count files
    spam_count = len(list(pathlib.Path("data/spam").glob("*")))
    ham_count  = len(list(pathlib.Path("data/ham").glob("*")))
    print(f"\nCorpus ready: {spam_count} spam · {ham_count} ham")
    print("Now run: python -m app.ml.train")
