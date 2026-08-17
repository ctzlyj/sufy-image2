# SUFY Image2 Agent Skill

把 `image.ctikki.com` 当前使用的 LTS4AI `SF-gpt-image-2` 生图能力封装成可安装的 Agent Skill。安装后，用户只需向 Agent 提供自己的 LTS4AI API Key，再用自然语言描述想生成或修改的图片。

> 公开名称是 SUFY Image2；实际调用模型 ID 为 `SF-gpt-image-2`，默认接口为 `https://api.lts4ai.com/v1`。

## 能力

- 文生图：`generate`
- 1–12 张参考图的图生图/重绘：`edit`
- 最多 10 个任务的批量生成和提示词队列：`batch`
- Adaptive、1:1、16:9、21:9、4:3、3:2、5:4、2:1、3:4、2:3、4:5、9:16
- 1K、2K、4K 尺寸映射
- JSON、Base64、Data URL、SSE 和远程图片 URL 响应解析
- 429/5xx 有界重试、超时、输入限制和 API Key 脱敏
- 仅依赖 Python 3.10+ 标准库

## 安装

最简单的方式：把仓库地址发给支持 Agent Skills 的 Agent，并说：

```text
请安装这个 Skill：https://github.com/CTctikki/sufy-image2
```

手动安装时，把仓库克隆到 Agent 的 Skills 目录。例如 Codex：

```bash
git clone https://github.com/CTctikki/sufy-image2 ~/.codex/skills/sufy-image2
```

其他 Agent 请复制到其兼容的 Skills 目录，确保 `SKILL.md` 位于 Skill 根目录。

## 用户使用方式

安装后可直接对 Agent 说：

```text
使用 sufy-image2，API Key 是我接下来发送的密钥。帮我生成一张 1:1 的高端瓜子商品主图，2K，深色背景，香槟金文字。
```

或：

```text
使用 sufy-image2，把这两张商品参考图做成 4:5 的高级电商场景图，保持包装文字和商品外观完全不变。
```

Agent 应通过临时环境变量或 stdin 使用 Key，不应把 Key 写进命令、文件、日志或回复。

## CLI 示例

CLI 不接受 `--api-key` 参数。优先使用临时 `LTS4AI_API_KEY`，或让 Agent 使用 `--api-key-stdin`。

### 文生图 generate

```bash
python scripts/sufy_image2.py generate --prompt "高端影棚商品摄影" --ratio 1:1 --quality 2K
```

### 图生图 edit

```bash
python scripts/sufy_image2.py edit --prompt "保持商品不变，替换为高级场景" --image product.png --ratio Adaptive --quality 2K
```

### 批量 batch

```bash
python scripts/sufy_image2.py batch --prompt "高级商品摄影" --count 4 --concurrency 2 --ratio 1:1 --quality 2K
```

```bash
python scripts/sufy_image2.py batch --prompts-file prompts.txt --image product.png --ratio Adaptive --quality 4K
```

## 重要限制

- 参考图仅支持 JPEG、PNG、GIF、WebP，最多 12 张，原图合计不超过 15 MB。
- 单批最多 10 个任务，建议并发 1–3。
- 当前站点界面的 seed 不会传给 `SF-gpt-image-2`，因此本 Skill 不承诺固定 seed 复现。
- 4K 请求可能需要最长约 30 分钟。

## 给用户转发

可直接转发的完整中文说明见 [`USER_GUIDE.zh-CN.md`](USER_GUIDE.zh-CN.md)。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用本地 Mock Server，不需要真实 API Key，也不会产生调用费用。

## License

MIT
