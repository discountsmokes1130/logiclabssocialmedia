import os, io, base64, datetime as dt, random, textwrap, requests, subprocess, pathlib

# ====== Config from GitHub Secrets ======
OPENAI_API_KEY      = os.environ["OPENAI_API_KEY"]           # OpenAI key
FB_PAGE_ACCESS_TOKEN= os.environ["FB_PAGE_ACCESS_TOKEN"]     # Long-lived Page token (60d, refresh as needed)
FB_PAGE_ID          = os.environ["FB_PAGE_ID"]               # Your Page ID
IG_USER_ID          = os.environ["IG_USER_ID"]               # Linked Instagram Business/Creator ID

# ====== Constants ======
GRAPH = "https://graph.facebook.com/v20.0"
REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
IMAGES_DIR = REPO_DIR / "Images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

TOPICS = [
    "AI Agents", "Cloud", "Cybersecurity", "DevOps", "Kubernetes",
    "Serverless", "MLOps", "Observability", "Vector DBs", "RAG",
    "Edge Computing", "Zero Trust", "LLM Ops", "Data Engineering"
]

def pick_topic() -> str:
    # If you create a topics.txt file, we'll prefer that
    topics_file = REPO_DIR / "topics.txt"
    if topics_file.exists():
        lines = [l.strip() for l in topics_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            return random.choice(lines)
    return random.choice(TOPICS)

# ====== OpenAI image (gpt-image-1) ======
def make_image(topic: str) -> bytes:
    prompt = (
        f"Create a 1024x1024 modern, abstract tech background for '{topic}'. "
        f"Clean, geometric, professional; leave center low-detail for text overlay."
    )
    # OpenAI Images API (Responses in base64)
    import json, urllib.request
    data = json.dumps({
        "model": "gpt-image-1",
        "prompt": prompt,
        "size": "1024x1024"
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    b64 = body["data"][0]["b64_json"]
    return base64.b64decode(b64)

def save_image(jpeg_bytes: bytes) -> pathlib.Path:
    name = f"tech_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    out = IMAGES_DIR / name
    out.write_bytes(jpeg_bytes)
    return out

def git_commit_and_push(path: pathlib.Path, message: str):
    # Configure git user (Actions runner)
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "add", str(path)], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "push"], check=True)

# ====== Posting helpers ======
def post_facebook(page_id: str, page_token: str, image_url: str, message: str):
    r = requests.post(f"{GRAPH}/{page_id}/photos", data={
        "url": image_url,
        "message": message,
        "access_token": page_token
    }, timeout=60)
    print("FB:", r.status_code, r.text[:300])

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
    print("IG create:", c.status_code, str(data)[:300])
    creation_id = (data or {}).get("id")
    if not creation_id:
        return
    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id,
        "access_token": page_token
    }, timeout=60)
    print("IG publish:", p.status_code, p.text[:300])

def main():
    topic = pick_topic()
    caption = f"Tech Byte: {topic}\n#technology #ai #cloud #devops"
    print("Topic:", topic)

    img_bytes = make_image(topic)
    path = save_image(img_bytes)
    print("Saved:", path)

    # Public URL for the image (main branch)
    # raw.githubusercontent.com/{owner}/{repo}/{branch}/Images/filename.jpg
    owner = os.environ["GITHUB_REPOSITORY"].split("/")[0]
    repo  = os.environ["GITHUB_REPOSITORY"].split("/")[1]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/Images/{path.name}"
    print("Public URL:", raw_url)

    # Commit & push so the URL is valid
    git_commit_and_push(path, f"add image {path.name}")

    # Post to FB Page + IG
    post_facebook(FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN, raw_url, caption)
    post_instagram(IG_USER_ID, FB_PAGE_ACCESS_TOKEN, raw_url, caption)

if __name__ == "__main__":
    main()
