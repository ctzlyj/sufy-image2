---
name: sufy-image2
description: Use when a user wants to generate, redraw, edit, restyle, or batch-create images with an LTS4AI API key using GPT-image-2, gpt-image-2.5, or the official Imagen models, including Image Studio 商品套图, 服装工作台, multi-reference editing, custom centimeter poster sizes, and 1K/2K/4K output.
---

# SUFY Image2

Use the bundled standard-library Python client with the image-model contracts used by `image.ctikki.com`. Default to `GPT-image-2`; retain the user's explicitly selected model, including `gpt-image-2.5` and the official Imagen models (`gemini-3.1-pro-imagen-official`, `gemini-3.5-flash-lite-imagen-official`, `gemini-3.6-flash-imagen-official`). The retired `SF-gpt-image-2` maps to `GPT-image-2` on the site. Do not silently fall back when a model is unavailable.

## 更换生图模型 / 如何选择生图模型

- **GPT-image-2（默认）**：日常商品图、批量出图和多参考图编辑；沿用原 Image2 的接口与 token 计费。
- **gpt-image-2.5**：同代直连模型 ID，需要时显式指定。
- **官方 Imagen 通道**：`gemini-3.1-pro-imagen-official`、`gemini-3.5-flash-lite-imagen-official`、`gemini-3.6-flash-imagen-official`，按张计费。
- 用户问如何选择时，运行 `python scripts/sufy_image2.py guide`，用中文解释当前目录；不要让用户访问英文官方说明或外部链接。建议不是速度、价格或质量保证，实际可用模型以用户 Key 的 `models` 结果为准。

## Workflow

1. Determine the operation:
   - no reference image → `generate`
   - one to twelve reference images → `edit`
   - several outputs or one prompt per line → `batch`
   - 商品套图或服装工作台任务 → 先阅读 `references/image-studio-workflows.md`，再按其中的角色分工、参考图顺序和保真约束组织提示词
2. On the user's own Windows machine, reuse the current-user DPAPI credential at `%LOCALAPPDATA%\JoyCode\credentials\sufy-image2.dpapi` when present. It is encrypted for that Windows identity and must never be copied to another account or machine.
3. Otherwise ask once for the LTS4AI API key. Keep transient keys process-local with `LTS4AI_API_KEY` or `--api-key-stdin`; never put a key in command arguments, source, logs, replies, or plaintext files.
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
python scripts/sufy_image2.py generate --prompt "海报设计，保留全部指定标题与卖点" --width-cm 120 --height-cm 40 --quality 4K --model gpt-image-2.5
```

`canvas` is offline and free; the second command generates a paid image. Width/height set the exact aspect ratio, not native print resolution or DPI. Report requested `size` separately from each output's measured `width`/`height`; inspect actual output rather than claiming a 120 cm print is natively 300 DPI. All catalog models use the same custom-size limits, not just the 120×40 example.

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
