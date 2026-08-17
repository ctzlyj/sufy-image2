---
name: sufy-image2
description: Use when a user wants to generate, redraw, edit, restyle, or batch-create images with an LTS4AI API key and the SF-gpt-image-2 model, including text-to-image, multi-reference image-to-image, aspect-ratio selection, or 1K/2K/4K output.
---

# SUFY Image2

Use the bundled standard-library Python client to run the same LTS4AI `SF-gpt-image-2` request contracts used by `image.ctikki.com`.

## Workflow

1. Determine the operation:
   - no reference image → `generate`
   - one to twelve reference images → `edit`
   - several outputs or one prompt per line → `batch`
2. Ask once for the LTS4AI API key only when the user has not supplied it.
3. Keep the key ephemeral. Prefer a process-local `LTS4AI_API_KEY`; otherwise start the CLI with `--api-key-stdin` and send the key through stdin without shell echo.
4. Infer missing creative details from the request. Default to ratio `1:1`, quality `2K`, and output directory `output/`.
5. Run the command from this Skill's directory.
6. Read the JSON summary from stdout. Render or display each generated local image when the host supports images; otherwise provide its absolute path.
7. Report provider errors concisely without repeating the API key.

## Commands

```bash
python scripts/sufy_image2.py generate --prompt "A premium product photograph" --ratio 1:1 --quality 2K
```

```bash
python scripts/sufy_image2.py edit --prompt "Keep the product unchanged and replace the background" --image product.png --ratio Adaptive --quality 2K
```

```bash
python scripts/sufy_image2.py batch --prompt "A refined studio product photo" --count 4 --concurrency 2 --ratio 1:1 --quality 2K
```

```bash
python scripts/sufy_image2.py batch --prompts-file prompts.txt --image reference.png --concurrency 2 --ratio Adaptive --quality 4K
```

Add `--api-key-stdin` after the subcommand when using stdin. Never put the key in a command argument.

## Operating Rules

- Use only JPEG, PNG, GIF, or WebP references.
- Accept at most 12 references and 15 MB total source bytes.
- Use `Adaptive` for edits when the first reference should determine the closest supported ratio.
- Treat 4K as a potentially 30-minute request; 1K/2K default to 10 minutes.
- Batch at most 10 tasks and use conservative concurrency, normally 1–3.
- Do not add legacy `n` or `response_format` fields.
- Do not claim deterministic seed support. The site's seed control is not sent to `SF-gpt-image-2`.
- Do not save keys in `.env`, shell scripts, generated metadata, logs, commits, or final responses.

## References

- Read `references/api-contract.md` when diagnosing request shape, ratios, sizes, or response formats.
- Read `references/agent-workflow.md` for secure invocation patterns and actionable error handling.

