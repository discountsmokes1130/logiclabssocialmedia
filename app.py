import os, io, base64, datetime as dt, random, textwrap, secrets, sqlite3, zoneinfo
from pathlib import Path
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

# ---------- App & Scheduler ----------
app = FastAPI(title="YourBrand Social Poster (Multi-tenant)")
SCHED = BackgroundScheduler()

# ---------- Env (set via GitHub Repository Secrets or host env) ----------
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
PORT = int(os.getenv("PORT", "8080"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FB_APP_ID = os.getenv("FB_APP_ID", "")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")

# ---------- Paths / constants ----------
IMG_DIR = Path("Images"); IMG_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = Path("assets"); ASSETS_DIR.mkdir(parents=True, exist_ok=True)
LOGO_PATH = ASSETS_DIR / "logo.png"
DB = "poster_multi.db"
GRAPH = "https://graph.facebook.com/v20.0"

# serve images publicly (Instagram fetches from here)
app.mount("/images", StaticFiles(directory="Images"), name="images")

# ---------- DB (sqlite) ----------
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fb_user_id TEXT UNIQUE,
        name TEXT,
        user_long_token TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        page_id TEXT,
        page_name TEXT,
        page_access_token TEXT,
        ig_user_id TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        mode TEXT DEFAULT 'daily',        -- 'daily' | 'weekly' | 'monthly'
        tz TEXT DEFAULT 'Asia/Kolkata',
        hour INTEGER DEFAULT 9,
        minute INTEGER DEFAULT 30,
        weekdays TEXT,                    -- CSV of 0-6 (Mon=0)
        monthdays TEXT,                   -- CSV of 1-31
        last_post_utc TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
]

def db_conn():
    return sqlite3.connect(DB)

def db_init():
    with db_conn() as c:
        for s in SCHEMA:
            c.execute(s)
        c.commit()

# ---------- OpenAI image generation ----------
TOPICS = [
    "AI Agents", "Cloud", "Cybersecurity", "DevOps", "Kubernetes",
    "Serverless", "MLOps", "Observability", "Vector DBs", "RAG"
]

def _ensure_openai():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing.")

def generate_image(topic: str) -> Image.Image:
    _ensure_openai()
    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = (
        f"Create a 1024x1024 modern, abstract tech background for '{topic}'. "
        f"Clean, geometric, professional, leave center low-detail for text overlay."
    )
    res = client.images.generate(model="gpt-image-1", prompt=prompt, size="1024x1024")
    img_bytes = base64.b64decode(res.data[0].b64_json)
    return Image.open(io.BytesIO(img_bytes)).convert("RGBA")

def compose(img: Image.Image, topic: str) -> Image.Image:
    canvas = Image.new("RGBA", img.size, (255,255,255,255))
    canvas.alpha_composite(img)
    draw = ImageDraw.Draw(canvas)
    W, H = canvas.size
    margin = 40
    title_font = ImageFont.load_default()
    topic_font = ImageFont.load_default()
    small_font = ImageFont.load_default()

    # Header
    hdr = Image.new("RGBA", (W, 110), (20,40,80,230))
    canvas.alpha_composite(hdr, (0, 0))
    draw.text((W//2 - 40, 40), "Tech Byte", font=title_font, fill=(255,255,255))

    # Topic box
    txt = textwrap.fill(topic, width=22)
    box = Image.new("RGBA", (int(W*0.88), 250), (255,255,255,200))
    canvas.alpha_composite(box, (int(W*0.06), int(H*0.42)))
    tw, th = draw.multiline_textbbox((0,0), txt, font=topic_font)[2:]
    draw.multiline_text((int(W/2 - tw/2), int(H*0.46)), txt, font=topic_font, fill=(0,0,0))

    # Footer
    ftr = Image.new("RGBA", (W, 90), (0,0,0,160))
    canvas.alpha_composite(ftr, (0, H-90))
    draw.text((margin, H-60), dt.datetime.now().strftime("%b %d, %Y"), font=small_font, fill=(255,255,255))

    # Logo
    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo.thumbnail((160,160))
            canvas.alpha_composite(logo, (margin, 10))
        except Exception as e:
            print("Logo overlay skipped:", e)

    return canvas.convert("RGB")

def save_image(img: Image.Image) -> Path:
    name = f"tech_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    out = IMG_DIR / name
    img.save(out, "JPEG", quality=90)
    return out

# ---------- Meta Graph helpers ----------
def exchange_long_lived(short_user_token: str) -> str:
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "fb_exchange_token": short_user_token,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def get_me(user_token: str):
    r = requests.get(f"{GRAPH}/me", params={"access_token": user_token, "fields": "id,name"}, timeout=30)
    r.raise_for_status()
    return r.json()

def get_pages(user_token: str):
    r = requests.get(f"{GRAPH}/me/accounts", params={"access_token": user_token}, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])

def get_ig_user_id(page_id: str, page_token: str) -> Optional[str]:
    r = requests.get(f"{GRAPH}/{page_id}", params={"fields": "instagram_business_account", "access_token": page_token}, timeout=30)
    r.raise_for_status()
    return (r.json().get("instagram_business_account") or {}).get("id")

def post_facebook(page_id: str, page_token: str, image_url: str, message: str):
    r = requests.post(f"{GRAPH}/{page_id}/photos", data={
        "url": image_url,
        "message": message,
        "access_token": page_token,
    }, timeout=60)
    print("FB:", r.status_code, r.text[:200])

def post_instagram(ig_user_id: str, page_token: str, image_url: str, caption: str):
    c = requests.post(f"{GRAPH}/{ig_user_id}/media", data={
        "image_url": image_url,
        "caption": caption,
        "access_token": page_token,
    }, timeout=60)
    data = {}
    try:
        data = c.json()
    except Exception:
        pass
    print("IG create:", c.status_code, str(data)[:200])
    creation_id = data.get("id")
    if not creation_id:
        return
    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id,
        "access_token": page_token,
    }, timeout=60)
    print("IG publish:", p.status_code, p.text[:200])

# ---------- Scheduling helpers ----------
DOW_MAP = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}

def _parse_csv_ints(csv_str: Optional[str]) -> List[int]:
    if not csv_str:
        return []
    try:
        return [int(x.strip()) for x in csv_str.split(",") if x.strip() != ""]
    except Exception:
        return []

def schedule_user_job(user_id: int, mode: str, tz: str, hour: int, minute: int,
                      weekdays_csv: Optional[str], monthdays_csv: Optional[str]):
    job_id = f"post_user_{user_id}"
    # remove existing job
    try:
        SCHED.remove_job(job_id)
    except Exception:
        pass

    # timezone
    try:
        tzinfo = zoneinfo.ZoneInfo(tz)
    except Exception:
        tzinfo = zoneinfo.ZoneInfo("UTC")

    # choose trigger
    if mode == "daily":
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tzinfo)
    elif mode == "weekly":
        days = _parse_csv_ints(weekdays_csv)
        if not days:
            days = [0]  # Monday default
        day_of_week = ",".join(DOW_MAP.get(d, "mon") for d in days)
        trigger = CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone=tzinfo)
    elif mode == "monthly":
        mdays = _parse_csv_ints(monthdays_csv)
        if not mdays:
            mdays = [1]  # 1st default
        day = ",".join(str(d) for d in mdays)
        trigger = CronTrigger(day=day, hour=hour, minute=minute, timezone=tzinfo)
    else:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tzinfo)

    def _run():
        post_for_user(user_id)

    SCHED.add_job(_run, trigger, id=job_id)

def load_all_jobs():
    with db_conn() as c:
        for row in c.execute("SELECT user_id, mode, tz, hour, minute, weekdays, monthdays FROM schedules"):
            user_id, mode, tz, hour, minute, weekdays, monthdays = row
            schedule_user_job(user_id, mode or "daily", tz or "Asia/Kolkata",
                              hour or 9, minute or 30, weekdays, monthdays)

# ---------- Core posting ----------
def post_for_user(user_id: int, topic: Optional[str] = None):
    with db_conn() as c:
        acc = c.execute(
            "SELECT a.page_id, a.page_access_token, a.ig_user_id FROM accounts a WHERE a.user_id=?",
            (user_id,)
        ).fetchone()
        if not acc:
            print(f"No account linked for user {user_id}")
            return
        page_id, page_token, ig_user_id = acc

    topic = topic or random.choice(TOPICS)
    caption = f"Tech Byte: {topic}\n#technology #ai #cloud #devops"

    bg = generate_image(topic)
    final = compose(bg, topic)
    path = save_image(final)
    public_url = f"{BASE_URL}/images/{path.name}"
    post_facebook(page_id, page_token, public_url, caption)
    if ig_user_id:
        post_instagram(ig_user_id, page_token, public_url, caption)

# ---------- Routes ----------
@app.get("/")
async def home():
    return {
        "ok": True,
        "connect": f"{BASE_URL}/connect",
        "dashboard": f"{BASE_URL}/dashboard",
        "status": f"{BASE_URL}/status"
    }

@app.get("/connect")
async def connect():
    state = secrets.token_urlsafe(24)
    scopes = [
        "pages_show_list",
        "pages_manage_posts",
        "pages_read_engagement",
        "instagram_basic",
        "instagram_content_publish",
    ]
    auth_url = (
        "https://www.facebook.com/v20.0/dialog/oauth"
        f"?client_id={FB_APP_ID}&redirect_uri={BASE_URL}/oauth/callback"
        f"&scope={','.join(scopes)}&state={state}"
    )
    return RedirectResponse(auth_url)

@app.get("/oauth/callback")
async def oauth_callback(code: Optional[str] = None, error: Optional[str] = None, state: Optional[str] = None):
    if error:
        raise HTTPException(400, f"OAuth error: {error}")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    # short-lived user token
    token_r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "redirect_uri": f"{BASE_URL}/oauth/callback",
        "code": code,
    }, timeout=30)
    token_r.raise_for_status()
    short_user_token = token_r.json()["access_token"]

    # long-lived user token (60 days)
    long_user_token = exchange_long_lived(short_user_token)

    # who is this user?
    me = get_me(long_user_token)
    fb_user_id = me.get("id"); name = me.get("name")

    # upsert user
    with db_conn() as c:
        row = c.execute("SELECT id FROM users WHERE fb_user_id=?", (fb_user_id,)).fetchone()
        if row:
            user_id = row[0]
            c.execute("UPDATE users SET name=?, user_long_token=? WHERE id=?", (name, long_user_token, user_id))
        else:
            c.execute(
                "INSERT INTO users (fb_user_id, name, user_long_token, created_at) VALUES (?,?,?,?)",
                (fb_user_id, name, long_user_token, dt.datetime.utcnow().isoformat())
            )
            user_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()

    # pages & IG
    pages = get_pages(long_user_token)
    if not pages:
        raise HTTPException(400, "No Facebook Pages found for this user.")
    page = pages[0]
    page_id = page.get("id"); page_name = page.get("name"); page_token = page.get("access_token")
    ig_user_id = get_ig_user_id(page_id, page_token)

    # save account + default schedule if missing
    with db_conn() as c:
        exists = c.execute("SELECT id FROM accounts WHERE user_id=?", (user_id,)).fetchone()
        if exists:
            c.execute(
                "UPDATE accounts SET page_id=?, page_name=?, page_access_token=?, ig_user_id=? WHERE user_id=?",
                (page_id, page_name, page_token, ig_user_id, user_id)
            )
        else:
            c.execute(
                "INSERT INTO accounts (user_id, page_id, page_name, page_access_token, ig_user_id) VALUES (?,?,?,?,?)",
                (user_id, page_id, page_name, page_token, ig_user_id)
            )
        sched = c.execute("SELECT id FROM schedules WHERE user_id=?", (user_id,)).fetchone()
        if not sched:
            c.execute(
                "INSERT INTO schedules (user_id, mode, tz, hour, minute) VALUES (?,?,?,?,?)",
                (user_id, "daily", "Asia/Kolkata", 9, 30)
            )
        c.commit()

    load_all_jobs()
    return RedirectResponse(f"{BASE_URL}/dashboard?user_id={user_id}")

@app.get("/dashboard")
async def dashboard(user_id: Optional[int] = None):
    with db_conn() as c:
        if user_id:
            user = c.execute("SELECT id, fb_user_id, name FROM users WHERE id=?", (user_id,)).fetchone()
            acc = c.execute("SELECT page_id, page_name, ig_user_id FROM accounts WHERE user_id=?", (user_id,)).fetchone()
            sched = c.execute(
                "SELECT mode, tz, hour, minute, weekdays, monthdays FROM schedules WHERE user_id=?",
                (user_id,)
            ).fetchone()
            data = {
                "user": {"id": user[0], "fb_user_id": user[1], "name": user[2]} if user else None,
                "account": {"page_id": acc[0], "page_name": acc[1], "ig_user_id": acc[2]} if acc else None,
                "schedule": {
                    "mode": sched[0], "tz": sched[1], "hour": sched[2], "minute": sched[3],
                    "weekdays": sched[4], "monthdays": sched[5]
                } if sched else None,
                "actions": {
                    "post_now": f"{BASE_URL}/post-now?user_id={user_id}&topic=Your+Topic",
                    "set_schedule_advanced": f"{BASE_URL}/set-schedule-advanced?user_id={user_id}&mode=daily&tz=Asia/Kolkata&hour=9&minute=30"
                }
            }
        else:
            users = [
                {"id": u[0], "name": u[1], "fb_user_id": u[2]}
                for u in c.execute("SELECT id, name, fb_user_id FROM users").fetchall()
            ]
            data = {"users": users}
    return JSONResponse(data)

@app.get("/set-schedule-advanced")
async def set_schedule_advanced(
    user_id: int,
    mode: str = "daily",
    tz: str = "Asia/Kolkata",
    hour: int = 9,
    minute: int = 30,
    weekdays: Optional[str] = None,   # e.g. "1,3,5" (Mon=0)
    monthdays: Optional[str] = None   # e.g. "1,15,30"
):
    # validate
    try:
        zoneinfo.ZoneInfo(tz)
    except Exception:
        raise HTTPException(400, "Invalid timezone")
    if mode not in ("daily","weekly","monthly"):
        raise HTTPException(400, "mode must be one of: daily, weekly, monthly")

    with db_conn() as c:
        c.execute(
            "UPDATE schedules SET mode=?, tz=?, hour=?, minute=?, weekdays=?, monthdays=? WHERE user_id=?",
            (mode, tz, hour, minute, weekdays, monthdays, user_id)
        )
        if c.rowcount == 0:
            c.execute(
                "INSERT INTO schedules (user_id, mode, tz, hour, minute, weekdays, monthdays) VALUES (?,?,?,?,?,?,?)",
                (user_id, mode, tz, hour, minute, weekdays, monthdays)
            )
        c.commit()

    schedule_user_job(user_id, mode, tz, hour, minute, weekdays, monthdays)
    return JSONResponse({
        "ok": True, "user_id": user_id, "mode": mode, "tz": tz,
        "hour": hour, "minute": minute, "weekdays": weekdays, "monthdays": monthdays
    })

@app.get("/post-now")
async def post_now(user_id: int, topic: Optional[str] = None):
    post_for_user(user_id, topic)
    return JSONResponse({"ok": True, "user_id": user_id, "topic": topic or "(auto)"})

@app.get("/status")
async def status():
    return {"ok": True}

# ---------- Boot ----------
db_init()
load_all_jobs()
SCHED.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
