import os, io, base64, datetime as dt, random, pathlib, subprocess, sys
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

# ====== Config from GitHub Secrets ======
OPENAI_API_KEY       = os.environ.get("OPENAI_API_KEY", "")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
FB_PAGE_ID           = os.environ.get("FB_PAGE_ID", "")
IG_USER_ID           = os.environ.get("IG_USER_ID", "")

# ====== Constants ======
GRAPH = "https://graph.facebook.com/v20.0"
REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
IMAGES_DIR = REPO_DIR / "Images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TOPICS = [
    "AI Agents","Cloud","Cybersecurity","DevOps","Kubernetes","Serverless","MLOps","Observability",
    "Vector Databases","RAG","Edge Computing","Zero Trust","Data Engineering","LLM Fine-Tuning",
    "Prompt Engineering","Generative AI","API Gateways","Service Mesh","CI/CD","Infrastructure as Code",
    "Product Analytics","Quantization","On-Device AI","Feature Stores","Data Catalogs"
]

# ====== Topic selection ======
def pick_topic() -> str:
    topics_file = REPO_DIR / "topics.txt"
    if topics_file.exists():
        lines = [l.strip() for l in topics_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            return random.choice(lines)
    return random.choice(DEFAULT_TOPICS)

# ====== OpenAI -> image bytes (with graceful fallback) ======
def try_openai_image(topic: str) -> Optional[bytes]:
    """Return JPEG bytes from OpenAI, or None on any error."""
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY missing — will use fallback image.", file=sys.stderr)
        return None
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = (
        f"Create a 1024x1024 modern, abstract tech background for '{topic}'. "
        f"Clean, geometric, professional; leave center low-detail for text overlay."
    )
    try:
        res = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
        b64 = res.data[0].b64_json
        return base64.b64decode(b64)
    except Exception as e:
        print("OpenAI error:", e, file=sys.stderr)
        return None

def make_fallback_image(topic: str) -> bytes:
    """Create a 1024x1024 gradient image with topic text (no OpenAI needed)."""
    W = H = 1024
    img = Image.new("RGB", (W, H), (12, 22, 38))
    draw = ImageDraw.Draw(img)

    # gradient background
    for y in range(H):
        r = int(12 + (y / H) * 40)
        g = int(22 + (y / H) * 60)
        b = int(38 + (y / H) * 80)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # fonts
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 42)
        topic_font = ImageFont.truetype("DejaVuSans.ttf", 48)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        title_font = ImageFont.load_default()
        topic_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    # header
    draw.rectangle([0, 0, W, 100], fill=(20, 40, 80))
    title = "Tech Byte"
    tw, th = draw.textbbox((0, 0), title, font=title_font)[2:]
    draw.text(((W - tw) // 2, 30), title, font=title_font, fill=(255, 255, 255))

    # topic box
    box_w, box_h = int(W * 0.86), 300
    box_x, box_y = (W - box_w) // 2, (H - box_h) // 2
    draw.rounded_rectangle([box_x, box_y, box_x + box_w, box_y + box_h], radius=24, fill=(255, 255, 255))

    # wrap topic
    def wrap(text, width=20):
        words, lines, cur = text.split(), [], ""
        for w in words:
            if len((cur + " " + w).strip()) <= width:
                cur = (cur + " " + w).strip()
            else:
                lines.append(cur); cur = w
        if cur: lines.append(cur)
        return "\n".join(lines)

    topic_wrapped = wrap(topic, width=22)
    tw2, th2 = draw.multiline_textbbox((0, 0), topic_wrapped, font=topic_font, spacing=6)[2:]
    draw.multiline_text((W // 2 - tw2 // 2, box_y + (box_h - th2) // 2),
                        topic_wrapped, font=topic_font, fill=(20, 20, 20), spacing=6)

    # footer date
    date_txt = dt.datetime.utcnow().strftime("%b %d, %Y")
    draw.rectangle([0, H - 70, W, H], fill=(0, 0, 0))
    draw.text((20, H - 50), date_txt, font=small_font, fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()

def make_image_bytes(topic: str) -> bytes:
    b = try_openai_image(topic)
    if b is not None:
        return b
    print("Using fallback image (Pillow) due to OpenAI error/quota.", file=sys.stderr)
    return make_fallback_image(topic)

def save_image(jpeg_bytes: bytes) -> pathlib.Path:
    name = f"tech_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg"
    out = IMAGES_DIR / name
    out.write_bytes(jpeg_bytes)
    return out

def git_commit_and_push(path: pathlib.Path, message: str):
    subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True)
    subprocess.run(["git", "add", str(path)], check=True)
    subprocess.run(["git", "commit", "-m", message], check=False)
    subprocess.run(["git", "push"], check=True)

# ====== Facebook / Instagram posting ======
def post_facebook_upload(page_id: str, page_token: str, image_path: str, message: str):
    """Upload the local image file directly to the Page (reliable)."""
    with open(image_path, "rb") as f:
        files = {"source": ("post.jpg", f, "image/jpeg")}
        data = {"message": message, "access_token": page_token, "published": "true"}
        r = requests.post(f"{GRAPH}/{page_id}/photos", data=data, files=files, timeout=120)
    try:
        print("FB upload:", r.status_code, r.json())
    except Exception:
        print("FB upload:", r.status_code, r.text[:400])

def post_instagram(ig_user_id: str, page_token: str, image_url: str, caption: str):
    c = requests.post(f"{GRAPH}/{ig_user_id}/media", data={
        "image_url": image_url, "caption": caption, "access_token": page_token
    }, timeout=60)
    try:
        data = c.json()
    except Exception:
        data = {"raw": c.text}
    print("IG create:", c.status_code, str(data)[:400])
    creation_id = (data or {}).get("id")
    if not creation_id:
        print("IG error: no creation_id returned. Check IG_USER_ID and token scopes.", file=sys.stderr)
        return
    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id, "access_token": page_token
    }, timeout=60)
    try:
        print("IG publish:", p.status_code, p.json())
    except Exception:
        print("IG publish:", p.status_code, p.text[:400])

def main():
    # Validate FB secrets early
    if not (FB_PAGE_ACCESS_TOKEN and FB_PAGE_ID and IG_USER_ID):
        print("ERROR: FB_PAGE_ACCESS_TOKEN, FB_PAGE_ID, IG_USER_ID must be set as repo secrets.", file=sys.stderr)
        sys.exit(1)

    topic = pick_topic()
    caption = f"Tech Byte: {topic}\n#technology #ai #cloud #devops"
    print("Topic:", topic)

    # 1) Make & save image
    img_bytes = make_image_bytes(topic)
    path = save_image(img_bytes)
    print("Saved:", path)

    # 2) Commit & push so the raw URL is valid (for IG)
    git_commit_and_push(path, f"add image {path.name}")

    # 3) Facebook — upload file directly (no URL fetch needed)
    post_facebook_upload(FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN, str(path), caption)

    # 4) Instagram — requires a public URL
    owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/Images/{path.name}"
    print("Public URL:", raw_url)
    post_instagram(IG_USER_ID, FB_PAGE_ACCESS_TOKEN, raw_url, caption)

if __name__ == "__main__":
    main()
