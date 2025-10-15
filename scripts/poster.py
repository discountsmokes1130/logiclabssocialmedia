import os, io, base64, datetime as dt, random, pathlib, subprocess, sys, json
from typing import Optional

import requests
from PIL import Image
from openai import OpenAI

# ====== Config from GitHub Secrets ======
OPENAI_API_KEY       = os.environ.get("OPENAI_API_KEY", "")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
FB_PAGE_ID           = os.environ.get("FB_PAGE_ID", "")
IG_USER_ID           = os.environ.get("IG_USER_ID", "")

if not OPENAI_API_KEY:
    print("ERROR: OPENAI_API_KEY is missing (Repo → Settings → Secrets → Actions).", file=sys.stderr)
    sys.exit(1)
if not (FB_PAGE_ACCESS_TOKEN and FB_PAGE_ID and IG_USER_ID):
    print("ERROR: FB_PAGE_ACCESS_TOKEN, FB_PAGE_ID, or IG_USER_ID missing in Secrets.", file=sys.stderr)
    sys.exit(1)

# ====== Constants ======
GRAPH = "https://graph.facebook.com/v20.0"
REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
IMAGES_DIR = REPO_DIR / "Images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# ====== Topics ======
DEFAULT_TOPICS = [
    "AI Agents","Cloud","Cybersecurity","DevOps","Kubernetes","Serverless","MLOps","Observability",
    "Vector Databases","RAG","Edge Computing","Zero Trust","Data Engineering","LLM Fine-Tuning",
    "Prompt Engineering","Generative AI","API Gateways","Service Mesh","CI/CD","Infrastructure as Code"
]
def pick_topic() -> str:
    topics_file = REPO_DIR / "topics.txt"
    if topics_file.exists():
        lines = [l.strip() for l in topics_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            return random.choice(lines)
    return random.choice(DEFAULT_TOPICS)

# ====== OpenAI image (SDK) ======
def make_image_bytes(topic: str) -> bytes:
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = (
        f"Create a 1024x1024 modern, abstract tech background for '{topic}'. "
        f"Clean, geometric, professional; leave center low-detail for text overlay."
    )
    try:
        res = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
        )
        b64 = res.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        # Print full message for debugging
        print("OpenAI error:", e, file=sys.stderr)
        # Try to display structured message if possible
        try:
            from openai import OpenAIError  # type: ignore
        except Exception:
            pass
        sys.exit(1)

def save_image(jpeg_bytes: bytes) -> pathlib.Path:
    name = f"tech_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    out = IMAGES_DIR / name
    out.write_bytes(jpeg_bytes)
    return out

def git_commit_and_push(path: pathlib.Path, message: str):
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "add", str(path)], check=True)
    # commit may fail if nothing changed; ignore in that case
    subprocess.run(["git", "commit", "-m", message], check=False)
    subprocess.run(["git", "push"], check=True)

# ====== Posting helpers ======
def post_facebook(page_id: str, page_token: str, image_url: str, message: str):
    r = requests.post(f"{GRAPH}/{page_id}/photos", data={
        "url": image_url,
        "message": message,
        "access_token": page_token
    }, timeout=60)
    try:
        print("FB:", r.status_code, r.json())
    except Exception:
        print("FB:", r.status_code, r.text[:400])

def post_instagram(ig_user_id: str, page_token: str, image_url: str, caption: str):
    c = requests.post(f"{GRAPH}/{ig_user_id}/media", data={
        "image_url": image_url,
        "caption": caption,
        "access_token": page_token
    }, timeout=60)
    try:
        data = c.json()
    except Exception:
        data = {"raw": c.text}
    print("IG create:", c.status_code, str(data)[:400])
    creation_id = (data or {}).get("id")
    if not creation_id:
        print("IG error: no creation_id returned. Check IG_USER_ID and Page token scopes.", file=sys.stderr)
        return
    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id,
        "access_token": page_token
    }, timeout=60)
    try:
        print("IG publish:", p.status_code, p.json())
    except Exception:
        print("IG publish:", p.status_code, p.text[:400])

def main():
    topic = pick_topic()
    caption = f"Tech Byte: {topic}\n#technology #ai #cloud #devops"
    print("Topic:", topic)

    # Generate image
    img_bytes = make_image_bytes(topic)
    path = save_image(img_bytes)
    print("Saved:", path)

    # Public URL: raw.githubusercontent.com/{owner}/{repo}/{branch}/Images/filename.jpg
    owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/Images/{path.name}"
    print("Public URL:", raw_url)

    # Commit & push the new image so the URL is valid
    git_commit_and_push(path, f"add image {path.name}")

    # Post to FB & IG
    post_facebook(FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN, raw_url, caption)
    post_instagram(IG_USER_ID, FB_PAGE_ACCESS_TOKEN, raw_url, caption)

if __name__ == "__main__":
    main()
