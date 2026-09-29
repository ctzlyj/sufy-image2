# SUFY Image2 Agent Skill

把 `image.ctikki.com` 使用的 LTS4AI 生图能力封装成可安装的 Agent Skill。安装后，用户只需向 Agent 提供自己的 LTS4AI API Key，再用自然语言描述想生成或修改的图片。

> Skill 名称继续保留 SUFY Image2；默认模型更新为 `GPT-image-2`，可选 `gpt-image-2.5` 和官方 Imagen 通道模型。默认接口为 `https://api.lts4ai.com/v1`。

## 如何选择生图模型

| 模型 | 建议用途 | 完整模型 ID |
|---|---|---|
| GPT-image-2（默认） | 日常商品图、批量出图、多参考图编辑 | `GPT-image-2` |
| gpt-image-2.5 | 同代直连 ID，需要时显式指定 | `gpt-image-2.5` |
| Imagen Pro（官方通道） | 可选官方 Imagen 模型，按张计费 | `gemini-3.1-pro-imagen-official` |
| Imagen Flash Lite（官方通道） | 可选官方 Imagen 模型，按张计费 | `gemini-3.5-flash-lite-imagen-official` |
| Imagen Flash（官方通道） | 可选官方 Imagen 模型，按张计费 | `gemini-3.6-flash-imagen-official` |

直接告诉 Agent“更换生图模型为 gpt-image-2.5”即可；CLI 用 `--model` 指定完整 ID。不会擅自替换用户指定的模型。实际可用模型以用户 Key 的 `models` 命令结果为准。

## 能力

- 文生图：`generate`
- 1–12 张参考图的图生图/重绘：`edit`
- 最多 10 个任务的批量生成和提示词队列：`batch`
- Image Studio 商品套图：主图、场景图、卖点图、白底图、认证图和四宫格图的职责与证据约束
- Image Studio 服装工作台：标准换装、直接动作、主图审核、动作轮次和参考图顺序
- Adaptive、1:1、16:9、21:9、4:3、3:2、5:4、2:1、3:4、2:3、4:5、9:16
- 1K、2K、4K 尺寸映射
- 自定义厘米海报尺寸：精确保持比例，支持三种模型；也可直接指定合法像素尺寸
- 离线 `guide` 中文选型说明、`canvas` 尺寸预检（不需要 Key，不收费）
- JSON、Base64、Data URL、SSE 和远程图片 URL 响应解析
- 429 有界重试；生图超时/网关错误等未知结果停止重发，避免重复扣费；输入限制和 API Key 脱敏
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

已安装用户可直接说“请更新 sufy-image2 到最新版，保留我的本地修改”。Git 安装且工作区干净时可 `git pull --ff-only`；有本地修改或分叉时先保存并合并，不用 `reset --hard`、覆盖整个目录或删除原输出。非 Git 安装先将新版解压到旁边目录再对比合并；更新不需要复制任何个人 Key 或历史图片。

## 用户使用方式

安装后可直接对 Agent 说：

```text
使用 sufy-image2，API Key 是我接下来发送的密钥。帮我生成一张 1:1 的高端瓜子商品主图，2K，深色背景，香槟金文字。
```

或：

```text
使用 sufy-image2，把这两张商品参考图做成 4:5 的高级电商场景图，保持包装文字和商品外观完全不变。
```

商品套图或服装工作台任务可直接描述目标，例如：

```text
使用 sufy-image2，按 Image Studio 商品套图规则生成主图：围绕一个核心购买理由，使用主标题、副标题和最多两个真实证据标签。
```

```text
使用 sufy-image2，按服装工作台的标准换装流程处理这些商品正反面图和人物模板图；先生成主图供我审核，不要自动继续动作图。
```

详细规则见 [`references/image-studio-workflows.md`](references/image-studio-workflows.md)。

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

### 自定义海报尺寸

```bash
python scripts/sufy_image2.py canvas --width-cm 120 --height-cm 40 --quality 4K
python scripts/sufy_image2.py generate --prompt "活动海报，完整保留标题与卖点" --width-cm 120 --height-cm 40 --quality 4K --model gpt-image-2.5
python scripts/sufy_image2.py generate --prompt "活动海报" --resolution 3072x1024
```

第一条只计算尺寸，不生图。120×40 厘米在 4K 档会请求 3840×1280 像素；29.7×21 厘米会请求 3168×2240 像素。并非只支持这些尺寸。

- 目录内模型使用相同范围：宽高比 1:3～3:1，两边为 16 的倍数，最长边不超过 3840，总像素 655,360～8,294,400。
- 厘米宽高必须能精确换算；无法精确表示会明确报错，不近似比例、不裁切或补边。支持最多 6 位小数；不要把商品自身尺寸当作海报尺寸。
- `--width-cm` 与 `--height-cm` 必须成对使用，且不能和 `--ratio` / `--resolution` 混用。`edit`、`batch` 同样支持。
- 厘米用于确定画布比例，不代表原生印刷 DPI。结果 JSON 同时包含请求尺寸及能读取到的实际图片宽高；交付前实际预览小字与商品，不用 Mock 测试代替视觉验收。

- 参考图仅支持 JPEG、PNG、GIF、WebP，最多 12 张，原图合计不超过 15 MB。
- 单批最多 10 个任务，建议并发 1–3。
- 当前站点界面的 seed 不会传给 `GPT-image-2`，因此本 Skill 不承诺固定 seed 复现。
- 4K 请求可能需要最长约 30 分钟。
- 生图超时、连接中断或 HTTP 408/409/5xx 时结果可能已生成并计费，脚本不会自动重发。先核对服务端记录；批量部分失败时保留已保存图片，不整批重跑。

## 给用户转发

可直接转发的完整中文说明见 [`USER_GUIDE.zh-CN.md`](USER_GUIDE.zh-CN.md)。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用本地 Mock Server，不需要真实 API Key，也不会产生调用费用。

2026-09-20 更新验证：40 项单测通过，3894 组厘米画布输入与网站算法结果逐项一致，Skill 元数据校验通过；这不替代真实生成的小字视觉验收。

本次对齐网站源码 `c00dc693f8dbf2021bfa36d62050447df5bda587` 的模型、厘米画布与未知结果保护；不是网站 COS、登录后台或 Windows 客户端的复制品。

## License

MIT
