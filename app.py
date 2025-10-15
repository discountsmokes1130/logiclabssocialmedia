import os, io, base64, datetime as dt, random, textwrap, secrets, sqlite3, zoneinfo
load_all_jobs()


return RedirectResponse(f"{BASE_URL}/dashboard?user_id={user_id}")


@app.get("/dashboard")
async def dashboard(user_id: Optional[int] = None):
# minimal JSON dashboard
with db_conn() as c:
data = {}
if user_id:
user = c.execute("SELECT id, fb_user_id, name FROM users WHERE id=?", (user_id,)).fetchone()
acc = c.execute("SELECT page_id, page_name, ig_user_id FROM accounts WHERE user_id=?", (user_id,)).fetchone()
sched = c.execute("SELECT tz, hour, minute FROM schedules WHERE user_id=?", (user_id,)).fetchone()
data = {
"user": {"id": user[0], "fb_user_id": user[1], "name": user[2]} if user else None,
"account": {"page_id": acc[0], "page_name": acc[1], "ig_user_id": acc[2]} if acc else None,
"schedule": {"tz": sched[0], "hour": sched[1], "minute": sched[2]} if sched else None,
"actions": {
"post_now": f"{BASE_URL}/post-now?user_id={user_id}&topic=Your+Topic",
"set_schedule": f"{BASE_URL}/set-schedule?user_id={user_id}&tz=Asia/Kolkata&hour=9&minute=30"
}
}
else:
users = [dict(id=u[0], name=u[1], fb_user_id=u[2]) for u in c.execute("SELECT id, name, fb_user_id FROM users").fetchall()]
data = {"users": users}
return JSONResponse(data)


@app.get("/set-schedule")
async def set_schedule(user_id: int, tz: str = "Asia/Kolkata", hour: int = 9, minute: int = 30):
# validate tz
try:
zoneinfo.ZoneInfo(tz)
except Exception:
raise HTTPException(400, "Invalid timezone")
with db_conn() as c:
c.execute("UPDATE schedules SET tz=?, hour=?, minute=? WHERE user_id=?", (tz, hour, minute, user_id))
c.commit()
schedule_user_job(user_id, tz, hour, minute)
return JSONResponse({"ok": True, "user_id": user_id, "tz": tz, "hour": hour, "minute": minute})


@app.get("/post-now")
async def post_now(user_id: int, topic: Optional[str] = None):
post_for_user(user_id, topic)
return JSONResponse({"ok": True, "user_id": user_id, "topic": topic or "(auto)"})


@app.get("/status")
async def status():
return {"ok": True}


# ====== Scheduler boot ======
db_init()
load_all_jobs()
SCHED.start()


if __name__ == "__main__":
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
