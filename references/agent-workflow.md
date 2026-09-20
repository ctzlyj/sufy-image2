# Agent Workflow and Key Safety

## Preferred Invocation

If the host can set a process-local environment variable without printing it, set `LTS4AI_API_KEY` only for the child process and run the CLI.

If not, open an interactive process with `--api-key-stdin`:

```bash
python scripts/sufy_image2.py generate --api-key-stdin --prompt "..."
```

Then send the key as one stdin line through the Agent's process-input tool. The script reads the line without writing it to stdout.

## Never Do This

- Never use `--api-key <value>`; that option intentionally does not exist.
- Never include a key in a shell command, URL, filename, `.env`, script, README, log, commit, issue, or final answer.
- Never echo the key back to the user after receiving it.
- Never attach the LTS4AI Authorization header when downloading a provider-returned remote image URL.

## Choosing a Command

| User intent | Command |
|---|---|
| Create from text | `generate` |
| Modify using references | `edit` |
| Several variations | `batch --prompt ... --count N` |
| Different prompt per output | `batch --prompts-file prompts.txt` |
| Check model visibility | `models` |
| 如何选择生图模型 | `guide` (offline; render the returned absolute `comparisonImage` path) |
| Check poster dimensions before paying | `canvas --width-cm W --height-cm H --quality 4K` (offline) |

Default to `SF-gpt-image-2.5-flare`; use `--model SF-gpt-image-2.5-sunburst` for the user's Sunburst selection, or `--model SF-gpt-image-2` for the original model. Exact user selection takes precedence over recommendations. Authentication or availability failures do not permit automatic fallback.

For poster sizes, extract the intended canvas dimensions from the request, not product/package dimensions. Resolve conflicting dimensions rather than guessing. Use centimeter options on `generate`, `edit`, or `batch`; do not combine them with `--ratio` or `--resolution`. `canvas` and `guide` require no API key and make no network calls.

For edits, preserve reference order. Put the main product or identity reference first because Adaptive ratio uses the first measurable image.

## Output Handling

The CLI prints JSON containing absolute paths, MIME types, byte sizes, model, and requested size. With centimeter options it also includes `canvas`; when dimensions can be read it reports actual width/height for each output. Display or render each path in capable hosts and check requested versus actual size. Do not embed the API key or source-image bytes in the response. Never reduce user copy to hide small-text problems or call mock output a visual acceptance test.

## Errors

- HTTP 401/403: ask the user to verify the LTS4AI key and model permission.
- HTTP 429: explain that rate limit or quota was reached; avoid immediate high-concurrency retry loops.
- Image POST HTTP 408/409/5xx or a network interruption: outcome unknown; do not retry automatically. Check upstream results/billing, then obtain the user's direction for a new paid submission if needed. Read-only model-list GET can retry 5xx safely.
- Missing image data: preserve the provider status and any existing output; reconcile the result before another paid request, and do not invent an image.
- Unsupported reference: convert AVIF/HEIC or other formats to PNG/JPEG before retrying.
- More than 15 MB: reduce image count or compress references without changing the product identity.
- Failed batch: keep already saved images and reconcile unfinished/unknown tasks individually, never replay the whole batch.
