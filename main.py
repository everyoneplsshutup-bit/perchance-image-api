"""Perchance AI image generator — self-hosted REST wrapper.

Replays the generator page's real flow with plain HTTP once a browser-verified
userKey is known (see README.md for how to get one).

Engine fallback (Nov 5, 2026): Perchance userKeys are session-bound to the
browser/IP that minted them — two fresh keys (from two different browsers
where generation worked) both returned invalid_key from this VM. So every
generation tries Perchance first and, on invalid_key or any Perchance
failure, automatically falls back to the Pollinations image API (free, no
key), which works from this VM. Each returned image carries an "engine"
field ("perchance" or "pollinations"). Perchance is still tried first, so
it is used automatically if a key ever works from here.

  POST https://image-generation.perchance.org/api/generate?userKey=...  (JSON body)
  GET  https://image-generation.perchance.org/api/downloadTemporaryImage?imageId=...

The generate body shape (generatorName/channel/subChannel/prompt/
negativePrompt/seed/resolution/guidanceScale) matches the page's own request.
The page's Art Style dropdown has no field in that body — it is a client-side
template — so `style` here is applied as a prompt hint, honestly documented.
"""

import asyncio
import json
import os
import random
import time
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE = "https://image-generation.perchance.org/api"
APP_DIR = Path(__file__).resolve().parent.parent
CACHE = APP_DIR / "cache"
CACHE.mkdir(exist_ok=True)
CONTROLS_FILE = APP_DIR.parent / "perchance-test" / "controls.json"
# Render/HF Docker image copy: controls.json sits next to app/ in the image,
# because ../perchance-test/ does not exist there (see Dockerfile).
BUNDLED_CONTROLS_FILE = APP_DIR / "controls.json"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
RESOLUTIONS = {"768x768", "512x768", "768x512"}

app = FastAPI(title="Perchance Image API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- key handling: re-read every request so a refresh needs no restart ---


def load_key():
    env = os.environ.get("USER_KEY", "").strip()
    if env:
        return env, "env"
    f = APP_DIR / "userkey.txt"
    if f.exists():
        k = f.read_text().strip()
        if k:
            return k, "userkey.txt"
    return None, "none"


def load_styles():
    # Paths tried in order: bundled copy at image root, copy inside the
    # image's perchance-test/ dir (Dockerfile COPYs it there), then the
    # local workspace path used when running from source.
    for path in (
        BUNDLED_CONTROLS_FILE,
        APP_DIR / "perchance-test" / "controls.json",
        CONTROLS_FILE,
    ):
        try:
            d = json.loads(path.read_text())
            for c in d.get("controls", []):
                if c.get("api_name") == "art_style":
                    return c.get("options", []) or ["Painted Anime"]
        except Exception:
            continue
    return ["Painted Anime"]


STYLES = load_styles()

# --- tiny per-IP rate limit (30/min on generation endpoints) ---

_hits = defaultdict(list)


def check_rate(ip: str, limit: int = 30, window: int = 60):
    now = time.time()
    lst = [t for t in _hits[ip] if now - t < window]
    if len(lst) >= limit:
        raise HTTPException(429, "rate limit: try again in a bit")
    lst.append(now)
    _hits[ip] = lst


# --- Perchance calls ---


async def generate_one(client, key, prompt, negative, seed, resolution):
    # Real page shape (from the browser's own generate requests):
    # decimal requestId, empty adAccessCode, and bdf (a per-session value;
    # override with PERCHANCE_BDF when a fresh key brings a fresh bdf).
    bdf = os.environ.get("PERCHANCE_BDF", "0.4804946457410627")
    url = (
        f"{BASE}/generate?userKey={key}"
        f"&requestId={random.random()}"
        f"&adAccessCode="
        f"&__cacheBust={random.random()}"
        f"&bdf={bdf}"
    )
    body = {
        "generatorName": "ai-image-generator",
        "channel": "ai-text-to-image-generator",
        "subChannel": "public",
        "prompt": prompt,
        "negativePrompt": negative or "",
        "seed": seed,
        "resolution": resolution,
        "guidanceScale": 7.0,
    }
    r = await client.post(
        url, json=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"},
    )
    try:
        data = r.json()
    except Exception:
        raise HTTPException(502, f"Perchance generate returned non-JSON (HTTP {r.status_code})")
    if not isinstance(data, dict) or "imageId" not in data:
        text = json.dumps(data)[:300] if isinstance(data, dict) else str(data)[:300]
        if "invalid_key" in text:
            raise HTTPException(503, "invalid_key: the userKey expired — refresh it (see README) and retry")
        raise HTTPException(502, f"Perchance generate failed: {text}")
    return data


async def download_one(client, image_id, ext):
    r = await client.get(f"{BASE}/downloadTemporaryImage?imageId={image_id}")
    if r.status_code != 200 or not r.content:
        raise HTTPException(502, f"download failed for {image_id} (HTTP {r.status_code})")
    path = CACHE / f"{image_id}.{ext}"
    path.write_bytes(r.content)
    return path


async def pollinations_one(client, prompt, seed, resolution):
    """Fallback engine: Pollinations image API (free, keyless, model flux).
    Verified working from this VM. Negative prompt is not supported by its
    URL API, so it is dropped here (the style prompt hint still applies).
    Pollinations' free tier intermittently answers 402 (out of free
    capacity at that moment), so retry, then try model=turbo."""
    w, h = (int(x) for x in resolution.split("x"))
    use_seed = random.randint(0, 2**31 - 1) if seed is None or seed < 0 else seed
    last_status = None
    for model in ("flux", "turbo"):
        for _attempt in range(2):
            url = (
                "https://image.pollinations.ai/prompt/" + quote(prompt, safe="") +
                f"?width={w}&height={h}&seed={use_seed}&model={model}&nologo=true&private=true"
            )
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            last_status = r.status_code
            if r.status_code == 200 and r.content:
                name = f"poll-{uuid.uuid4().hex[:16]}.jpg"
                path = CACHE / name
                path.write_bytes(r.content)
                return {
                    "id": name[:-4],
                    "file": name,
                    "url": img_url(name),
                    "width": w,
                    "height": h,
                    "seed": use_seed,
                    "engine": "pollinations",
                }
            await asyncio.sleep(2)
    raise HTTPException(502, f"Pollinations request failed (HTTP {last_status})")


def img_url(filename: str) -> str:
    return f"{PUBLIC_BASE}/img/{filename}" if PUBLIC_BASE else f"/img/{filename}"


def build_proxy():
    """Explicit proxy with auth passed separately. httpx mis-parses this VM's
    credential-bearing proxy URL from the environment, so the client must
    never read the env vars itself (trust_env=False below)."""
    raw = os.environ.get("https_proxy") or os.environ.get("http_proxy") or ""
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname:
        return None
    url = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 3128}"
    if u.username:
        return httpx.Proxy(url, auth=(u.username, u.password or ""))
    return url


async def run_generation(prompt, style, count, resolution, seed, negative):
    # Perchance first (if a key is present); on invalid_key or any Perchance
    # failure, fall back to Pollinations for that image. No key at all also
    # goes straight to Pollinations, so the API works without a key.
    # Pollinations' free tier 402s a second image requested within a few
    # seconds of the first (shared egress IP), so sequential images are
    # spaced 12s apart. If a later image still fails after retries, the
    # images that did succeed are returned with partial=True instead of
    # being discarded by a 502; if none succeeded, the error is raised.
    key, _src = load_key()
    effective = f"{style} style, {prompt}" if style else prompt
    out = []
    failed = None
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False, proxy=build_proxy()) as client:
            for idx in range(count):
                if idx > 0:
                    await asyncio.sleep(12)
                try:
                    if key:
                        try:
                            data = await generate_one(client, key, effective, negative, seed, resolution)
                            ext = data.get("fileExtension", "jpeg")
                            path = await download_one(client, data["imageId"], ext)
                            out.append({
                                "id": data["imageId"],
                                "file": path.name,
                                "url": img_url(path.name),
                                "width": data.get("width"),
                                "height": data.get("height"),
                                "seed": data.get("seed"),
                                "engine": "perchance",
                            })
                            continue
                        except (HTTPException, httpx.HTTPError):
                            pass  # fall through to Pollinations
                    out.append(await pollinations_one(client, effective, seed, resolution))
                except (HTTPException, httpx.HTTPError) as e:
                    failed = e
                    break
    except httpx.HTTPError as e:
        failed = failed or e
    if failed is not None and not out:
        if isinstance(failed, HTTPException):
            raise failed
        raise HTTPException(502, "image request failed (network/proxy error)")
    return out, failed is not None


# --- endpoints ---


@app.get("/")
def root():
    return {
        "name": "Perchance Image API",
        "endpoints": [
            "GET /health",
            "GET /v1/features",
            "GET /styles",
            "GET /image?prompt=...&style=&count=&resolution=&seed=&negative=",
            "POST /v1/generate {prompt, style, count, resolution, seed, negative}",
            "POST /v1/images/generations {prompt, n, size}  (OpenAI-compatible)",
            "GET /img/<file>",
        ],
    }


@app.get("/health")
def health():
    key, src = load_key()
    return {"status": "ok", "key_present": bool(key), "key_source": src,
            "engines": {"perchance": "ready" if key else "no_key",
                        "pollinations": "ready"}}


@app.get("/v1/features")
def features():
    return {
        "prompt": {
            "type": "text",
            "max_chars": 1000,
            "notes": "Supports {a|b|c} randomisation and [brackets] for emphasis.",
        },
        "art_style": {
            "type": "dropdown",
            "default": "Painted Anime",
            "count": len(STYLES),
            "options": STYLES,
            "note": "Applied as a prompt hint (the page's style templates are client-side).",
        },
        "shape": {"type": "dropdown", "default": "Square",
                  "maps_to_resolution": {"Square": "768x768", "Portrait": "512x768", "Landscape": "768x512"}},
        "how_many": {"type": "number", "default": 4, "api_max": 16,
                     "note": "count > 1 runs that many sequential generate calls."},
        "not_on_page": ["negative_prompt", "seed", "guidance_scale", "resolution picker"],
        "source": "perchance.org/ai-text-to-image-generator (no login, no sign-up)",
    }


@app.get("/styles")
def styles():
    return {"default": "Painted Anime", "count": len(STYLES), "styles": STYLES}


@app.get("/image")
async def image_get(
    request: Request,
    prompt: str = Query(..., max_length=1000),
    style: str = "",
    count: int = Query(1, ge=1, le=16),
    resolution: str = "768x768",
    seed: int = -1,
    negative: str = "",
):
    check_rate(request.client.host if request.client else "unknown")
    if resolution not in RESOLUTIONS:
        raise HTTPException(400, f"resolution must be one of {sorted(RESOLUTIONS)}")
    images, partial = await run_generation(prompt, style.strip(), count, resolution, seed, negative)
    resp = {"prompt": prompt, "style": style or None, "resolution": resolution,
            "count": len(images), "images": images}
    if partial:
        resp["partial"] = True
    return resp


class GenerateBody(BaseModel):
    prompt: str
    style: str = ""
    count: int = 1
    resolution: str = "768x768"
    seed: int = -1
    negative: str = ""


@app.post("/v1/generate")
async def generate_post(request: Request, body: GenerateBody):
    check_rate(request.client.host if request.client else "unknown")
    if len(body.prompt) > 1000:
        raise HTTPException(400, "prompt max 1000 chars")
    if not (1 <= body.count <= 16):
        raise HTTPException(400, "count must be 1-16")
    if body.resolution not in RESOLUTIONS:
        raise HTTPException(400, f"resolution must be one of {sorted(RESOLUTIONS)}")
    images, partial = await run_generation(body.prompt, body.style.strip(), body.count,
                                  body.resolution, body.seed, body.negative)
    resp = {"prompt": body.prompt, "style": body.style or None,
            "resolution": body.resolution, "count": len(images), "images": images}
    if partial:
        resp["partial"] = True
    return resp


class OpenAIBody(BaseModel):
    prompt: str
    n: int = 1
    size: str = "768x768"


@app.post("/v1/images/generations")
async def openai_generations(request: Request, body: OpenAIBody):
    check_rate(request.client.host if request.client else "unknown")
    size_map = {"1024x1024": "768x768", "768x768": "768x768",
                "512x768": "512x768", "768x512": "768x512"}
    resolution = size_map.get(body.size, "768x768")
    n = max(1, min(body.n, 16))
    images, _partial = await run_generation(body.prompt, "", n, resolution, -1, "")
    return {"created": int(time.time()),
            "data": [{"url": im["url"]} for im in images]}


@app.get("/img/{filename}")
def serve_img(filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "bad filename")
    path = CACHE / filename
    if not path.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(path)
