# Deploying MultiGame to Render

## What you will end up with

A public URL like `https://multigame.onrender.com` that anyone can open in
a browser and play — desktop or mobile, no installs required.

---

## 1 · Required folder structure

Before doing anything else, make sure your project looks exactly like this:

```
multigame/                  ← root of your GitHub repo
├── server.py
├── requirements.txt
├── render.yaml
├── .gitignore
├── static/
│   └── index.html
└── games/
    ├── base_game.py
    ├── game1_crashbash.py
    ├── game2_tntbattle.py
    ├── bot_ai.py
    └── game_factory.py
```

Nothing else is required. Do **not** commit `__pycache__/` or `.venv/`.

---

## 2 · Push to GitHub

If you do not have a GitHub account, create one at github.com — it is free.

```bash
# one-time setup (only if you have never used git on this machine)
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"

# inside the multigame/ folder
git init
git add .
git commit -m "initial deploy"

# create a new repo on github.com (call it "multigame"), then:
git remote add origin https://github.com/YOUR_USERNAME/multigame.git
git branch -M main
git push -u origin main
```

---

## 3 · Create a Render account

Go to **render.com** and sign up with your GitHub account.
This lets Render read your repos without any extra tokens.

---

## 4 · Deploy with the Blueprint (recommended — one click)

1. In the Render dashboard click **"New +"** → **"Blueprint"**
2. Select your `multigame` repository
3. Render reads `render.yaml` automatically and pre-fills everything
4. Click **"Apply"**
5. Wait ~2 minutes for the build to finish
6. Your service appears at `https://multigame.onrender.com`
   (the exact subdomain is shown in the dashboard)

---

## 4b · Deploy manually (if Blueprint does not appear)

1. Click **"New +"** → **"Web Service"**
2. Connect your `multigame` GitHub repo
3. Fill in these fields:

| Field | Value |
|---|---|
| **Name** | multigame |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn server:app --host 0.0.0.0 --port $PORT` |
| **Plan** | Free (or Starter for always-on) |

4. Click **"Create Web Service"**

---

## 5 · Verify the deploy

Once the build log shows `Application startup complete`, open your URL.
You should see the MultiGame lobby.

To confirm the API is working, open:
```
https://multigame.onrender.com/api/rooms
```
It should return `{"rooms": []}`.

FastAPI's auto-generated docs are also available at:
```
https://multigame.onrender.com/docs
```

---

## 6 · Important: Free plan spin-down

On Render's **free plan** the service goes to sleep after 15 minutes of
inactivity. The first request after that takes ~30 seconds to wake up.

**If you want always-on:** upgrade the service to the **Starter plan**
($7 / month) in the Render dashboard → your service → Settings → Plan.

Alternatively, keep the free plan and use a free uptime monitor
(e.g. UptimeRobot) to ping your `/api/rooms` endpoint every 10 minutes.

---

## 7 · Share the link with friends

Give people your Render URL — that is all they need.

- Desktop players use keyboard (arrow keys + space by default,
  configurable via ⌨️ in the lobby)
- Mobile players use the on-screen D-pad automatically
- One player creates a room, shares the 8-character code,
  friends paste it in the Join tab

---

## 8 · Redeploy after code changes

```bash
# make your edits, then:
git add .
git commit -m "describe what changed"
git push
```

Render detects the push and redeploys automatically within ~1 minute.

---

## 9 · Troubleshooting

| Symptom | Fix |
|---|---|
| Build fails with `ModuleNotFoundError: fastapi` | Check `requirements.txt` is in the repo root, not inside `games/` |
| `Cannot find games/` on startup | Make sure the `games/` folder is committed — check with `git ls-files games/` |
| SSE stream disconnects after ~30 s | Add `X-Accel-Buffering: no` header (already in the code) and check your reverse-proxy config |
| Free plan takes 30 s to load | Expected — upgrade to Starter or use UptimeRobot to keep it warm |
| CORS error in browser console | The server already has `allow_origins=["*"]`; hard-reload the page |

---

## 10 · Custom domain (optional)

In the Render dashboard → your service → Settings → Custom Domain,
add your domain (e.g. `games.yourdomain.com`) and follow the CNAME
instructions. Render handles TLS automatically.