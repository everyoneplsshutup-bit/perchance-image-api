# Perchance Image API

Self-hosted REST wrapper around Perchance's free AI image generator
(https://perchance.org/ai-text-to-image-generator — no login, no sign-up).
It replays the generator page's real request with plain HTTP:

- `POST image-generation.perchance.org/api/generate?userKey=...` (JSON body)
- `GET image-generation.perchance.org/api/downloadTemporaryImage?imageId=...`

## Engine fallback (Nov 5, 2026)

Perchance generation from this VM is BLOCKED by session binding, proven
twice: a fresh key from the user's own browser, and a fresh key from a
GitHub PC's browser (both verified working in their own browsers, HTTP 200
on generate) both returned `invalid_key` from this VM within minutes of
extraction. A userKey is bound to the browser/IP that minted it and does
not transfer to another server — so a borrowed key alone cannot make
server-side Perchance generation work from here.

So every generation tries Perchance first (with the key in `userkey.txt`)
and, on `invalid_key` or any Perchance failure, automatically falls back
to the Pollinations image API (free, keyless, model `flux`) — verified
working from this VM. Each returned image carries an `"engine"` field
(`"perchance"` or `"pollinations"`). If a key ever works from here,
Perchance is used automatically. The API works with no key at all now.

## Run

```bash
./run.sh            # http://localhost:8000
```

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | Server + key status |
| `GET /v1/features` | Real page features: prompt, 79 art styles, shape, how many |
| `GET /styles` | The 79 art style names (default: Painted Anime) |
| `GET /image?prompt=...&style=&count=&resolution=&seed=&negative=` | Generate + serve |
| `POST /v1/generate` | Same, JSON body `{prompt, style, count, resolution, seed, negative}` |
| `POST /v1/images/generations` | OpenAI-compatible `{prompt, n, size}` → `{created, data:[{url}]}` |
| `GET /img/<file>` | Download a generated image |

`resolution`: `768x768` (Square) | `512x768` (Portrait) | `768x512` (Landscape).
`count`: 1–16 (each extra image is a sequential generate call).
`seed`: -1 = random (default). Unknown param or bad resolution → 400.

## The userKey (optional now — Perchance only)

Perchance generation needs a browser-verified `userKey` (64 hex chars). It cannot be
minted from a datacenter/VPN server — Perchance's verification only passes in
a normal browser on a residential connection. Get one like this:

1. On your own computer/phone browser, open
   https://perchance.org/ai-text-to-image-generator
2. Type any prompt and Generate once — wait for one image to appear.
3. Open DevTools → Network → filter `image-generation` → click any
   `image-generation.perchance.org/api/...` request → copy the `userKey=...`
   value from its URL.
4. Paste it into `userkey.txt` (or set the `USER_KEY` env var) and restart —
   no, restart is NOT needed: the key is re-read on every request.

A key is valid for hours–days. When generation starts returning
`invalid_key`, repeat steps 1–4 for a fresh key.

Phone without a PC: Kiwi Browser (Android) has built-in DevTools and the same
steps work there. Stock Chrome on Android has no DevTools.

## Honest limits

- `style` is applied as a prompt hint — the page's Art Style templates are
  client-side and are not a field in the API's generate body.
- `count` runs sequential calls, so 4 images take roughly 4x one image.
- The Perchance key expires (hours–days) and must be refreshed by hand —
  this is not set-and-forget. Free community service; Perchance can change
  or limit it. (And per the fallback note above, a key minted in another
  browser does not work from this VM at all.)
- Pollinations (the fallback engine) is also a free community service: it
  can be slow/busy, a small watermark can still appear despite `nologo`,
  and the `negative` prompt is dropped on the Pollinations path (its URL
  API has no negative-prompt field). It also rate-limits the shared egress
  IP: a second image requested within a few seconds of the first gets
  HTTP 402, so the API spaces sequential images 12s apart and retries
  (flux, then turbo). If an image still fails after retries, the images
  that did succeed are returned with `"partial": true` instead of a 502.
- Rate limit: 30 generation requests/minute per IP (in-memory).
