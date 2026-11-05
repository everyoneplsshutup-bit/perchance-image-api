# Perchance Image API — agent guide

Give this file (or the live `/openapi.json`) to any agentic AI and it can call the API.

## Base URL
`https://perchance-image-api.onrender.com` (if the deploy URL differs, use the live Render URL instead)

## Auth
None. Rate limit: 30 requests/min per IP on generation endpoints.

## Engines
Every generation tries Perchance first; on any Perchance failure it falls back to the Pollinations image API (free, no key). Each returned image carries an `engine` field (`perchance` or `pollinations`).

## Endpoints
- `GET /health` — `{"status":"ok","key_present":bool,"engines":{...}}`
- `GET /v1/features` — full capability list (prompt rules, styles, shapes, limits)
- `GET /styles` — the 79 art styles (`default: "Painted Anime"`)
- `GET /image?prompt=...&style=&count=1&resolution=768x768&seed=-1&negative=` — simplest call
- `POST /v1/generate` — JSON `{"prompt","style":"","count":1,"resolution":"768x768","seed":-1,"negative":""}`
- `POST /v1/images/generations` — OpenAI-compatible, JSON `{"prompt","n":1,"size":"1024x1024"}`; `size` maps to 768x768 / 512x768 / 768x512; returns `{"created":...,"data":[{"url":...}]}`
- `GET /img/<file>` — download a generated image
- `GET /docs` — Swagger UI; `GET /openapi.json` — machine-readable spec

## Notes
- `style` is applied as a prompt hint (the page's style templates are client-side).
- Resolutions: Square `768x768`, Portrait `512x768`, Landscape `768x512`. `count` 1–16 (images generate sequentially, ~12s apart).
- Image URLs in responses are absolute (PUBLIC_BASE is the live Render URL).
- Free hosting sleeps after 15 min idle — the first request after sleep takes ~1 min to wake, then responses are normal.
