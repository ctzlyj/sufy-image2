---
name: sufy-image2
description: Use when a user wants to generate, redraw, edit, restyle, or batch-create images with an LTS4AI API key using SF-gpt-image-2, including Image Studio 商品套图, 服装工作台, multi-reference editing, custom centimeter poster sizes, and 1K/2K/4K output.
---

# SUFY Image2

Use the bundled standard-library Python client with the image-model contracts used by `image.ctikki.com`. Until the owner explicitly re-enables the 2.5 models, send every image request with `SF-gpt-image-2`. Legacy `--model SF-gpt-image-2.5-flare` and `--model SF-gpt-image-2.5-sunburst` inputs are accepted only for compatibility and are resolved to Image2 before any request.

## 当前模型策略

- **Image2（当前统一模型）**：所有文生图、参考图编辑、批量任务和自定义尺寸任务均使用 `SF-gpt-image-2`。
- **Flare / Sunburst**：暂时停用，不推荐、不主动选择，也不向上游发送这两个模型 ID；等待所有者明确通知后再恢复。

## Workflow

1. Determine the operation:
   - no reference image → `generate`
   - one to twelve reference images → `edit`
   - several outputs or one prompt per line → `batch`
   - 商品套图或服装工作台任务 → 先阅读 `references/image-studio-workflows.md`，再按其中的角色分工、参考图顺序和保真约束组织提示词
2. Ask once for the LTS4AI API key only when the user has not supplied it.
3. Keep the key ephemeral. Prefer a process-local `LTS4AI_API_KEY`; otherwise start the CLI with `--api-key-stdin` and send the key through stdin without shell echo.
4. Infer missing creative details from the request. Default to ratio `1:1`, quality `2K`, and output directory `output/`. For a specified poster/canvas size, use `--width-cm` and `--height-cm`; read `references/api-contract.md` for exact limits. Distinguish canvas dimensions from product/package dimensions, and resolve conflicting sizes before a paid call.
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

```bash
python scripts/sufy_image2.py canvas --width-cm 120 --height-cm 40 --quality 4K
python scripts/sufy_image2.py generate --prompt "海报设计，保留全部指定标题与卖点" --width-cm 120 --height-cm 40 --quality 4K
```

`canvas` is offline and free; the second command generates a paid image. Width/height set the exact aspect ratio, not native print resolution or DPI. Report requested `size` separately from each output's measured `width`/`height`; inspect actual output rather than claiming a 120 cm print is natively 300 DPI. Image2 keeps the existing custom-size limits; 120×40 is only an example.

## Operating Rules

- Use only JPEG, PNG, GIF, or WebP references.
- Accept at most 12 references and 15 MB total source bytes.
- Use `Adaptive` for edits when the first reference should determine the closest supported ratio.
- Treat 4K as a potentially 30-minute request; 1K/2K default to 10 minutes.
- Batch at most 10 tasks and use conservative concurrency, normally 1–3.
- Preserve all user titles, selling points, edited plans, product identity, and background/style choices. Do not add a “精简清晰” mode, remove text to conceal poor small-text rendering, or unify cross-border platform presets to white backgrounds. This CLI does not manage the website's accounts, COS archives, history, or templates.
- Generation HTTP 408/409/5xx, interrupted responses, and network timeouts have an unknown outcome: stop and reconcile results/billing before another paid submission. Never automatically replay a whole failed batch; retain already saved images. `--retries` only retries explicit 429 responses and read-only GET 5xx.
- Do not add legacy `n` or `response_format` fields.
- Do not claim deterministic seed support; no image request sends a seed.
- Do not save keys in `.env`, shell scripts, generated metadata, logs, commits, or final responses.

## References

- Read `references/api-contract.md` when diagnosing request shape, ratios, sizes, or response formats.
- Read `references/agent-workflow.md` for secure invocation patterns and actionable error handling.
- Read `references/image-studio-workflows.md` for Image Studio 商品套图、标准换装、直接动作、主图审核和动作轮次规则。
