# LTS4AI SF-gpt-image-2 API Contract

## Defaults

- Base URL: `https://api.lts4ai.com/v1`
- Model: `SF-gpt-image-2`
- Authentication: `Authorization: Bearer <API key>`
- Output format: PNG

## Text to Image

`POST /v1/images/generations` with `Content-Type: application/json`:

```json
{
  "model": "SF-gpt-image-2",
  "prompt": "A premium studio product photograph",
  "size": "1024x1024",
  "output_format": "png"
}
```

Do not send `n` or `response_format` for this model.

## Multi-reference Editing

`POST /v1/images/edits` with `multipart/form-data`:

| Field | Value |
|---|---|
| `model` | `SF-gpt-image-2` |
| `prompt` | User prompt |
| `size` | Resolved provider size |
| `output_format` | `png` |
| `image` | Repeated file parts in reference order |

Let the HTTP client generate the multipart boundary. Supported references are JPEG, PNG, GIF, and WebP. The Skill enforces the current site's limit of 12 files and 15 MB total bytes.

## Resolution Mapping

For current-site 1K and 2K controls:

| Ratio | Provider size |
|---|---|
| Adaptive, 1:1 | `1024x1024` |
| 16:9, 4:3, 3:2 | `1536x1024` |
| 9:16, 3:4, 2:3, 4:5 | `1024x1536` |
| 21:9, 5:4, 2:1 | `auto` |

For 4K:

| Ratio | Provider size |
|---|---|
| Adaptive | `3840x2160` |
| 1:1 | `2880x2880` |
| 16:9 | `3840x2160` |
| 21:9 | `3840x1648` |
| 4:3 | `3264x2448` |
| 3:2 | `3504x2336` |
| 5:4 | `3200x2560` |
| 2:1 | `3840x1920` |
| 3:4 | `2448x3264` |
| 2:3 | `2336x3504` |
| 4:5 | `2560x3200` |
| 9:16 | `2160x3840` |

For edits, Adaptive first infers the nearest listed ratio from the first reference image.

## Responses

Canonical response:

```json
{
  "data": [
    { "b64_json": "..." }
  ]
}
```

The client also accepts nested `b64Json`, data URLs, SSE `data:` JSON frames, direct image responses, and HTTP(S) image URLs. Remote image downloads never receive the LTS4AI Authorization header.

## Reliability

- Default timeout: 10 minutes for 1K/2K and 30 minutes for 4K.
- Default retries: two retries after the first attempt.
- Retry only HTTP 429 and 5xx responses, with 1-second and 3-second waits.
- A seed is not included in `SF-gpt-image-2` generation or edit requests.
