# ChemAutoAgent

面向化学仪器，光谱仪器、泵阀设备、温控设备等实验室硬件的**多 Agent 自动化系统**。

上传仪器 PDF 手册后，系统自动完成：

```
PDF 手册 → 手册理解(OCR) → 协议/安全/函数抽取 → Python 驱动生成
        → 逐函数真机测试 → 错误修复 → 发布注册 → 安全调用
```

内置 4 个子 Agent：`build_agent`（建驱动）、`test_agent`（测试修复）、`publish_agent`（发布）、`invoke_agent`（调用）。

---

## 环境要求

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)**（包管理 + 运行）
- **DeepSeek API Key**（模型推理用，`DEEPSEEK_API_KEY`）

### OCR 后端（二选一）

| 模式 | 硬件要求 | OCR 速度 | 安装命令 |
|------|----------|---------|---------|
| **GPU** | NVIDIA 显卡 + CUDA 12.9 驱动 + cuDNN 9 | 快 | `uv sync --extra gpu` |
| **CPU** | 无特殊要求 | 慢 | `uv sync --extra cpu` |

> GPU 版对驱动版本要求较高（需支持 CUDA 12.9）。不确定能否用，先执行 `nvidia-smi` 看 CUDA 版本；不支持就装 CPU 版。
>
> CPU 版无需显卡、任何机器可装，但 OCR 解析 PDF 会比较慢（大手册可能需要数分钟）。

---

## 安装

```bash
git clone <仓库地址>
cd <目录>

# 先安装 uv（若未安装）：https://docs.astral.sh/uv/#installation

# 二选一：
uv sync --extra gpu    # GPU 版（NVIDIA 显卡）
uv sync --extra cpu    # CPU 版
```

首次 `uv sync` 会安装 PaddleOCR/PaddleX 依赖（数百 MB），并联网下载 OCR 模型权重，请保持网络畅通。

---

## 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 API Key：

```dotenv
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

> `.env` 含真实密钥，**不要提交到 git**（已在 `.gitignore` 中排除）。

---

## 运行

```bash
# Web 界面（推荐，可查看 session state / 事件历史）
uv run adk web .

# 命令行对话
uv run adk run app
```

---

## 基本流程

1. **添加新仪器**：上传 PDF 手册 → `build_agent` 自动 OCR、抽取协议、生成 Python 驱动
2. **测试**：`test_agent` 连接真机、逐函数测试、诊断失败、修复代码
3. **发布**：`publish_agent` 把测试通过的驱动存入 registry（跨会话复用）
4. **调用**：`invoke_agent` 列出/调用已发布驱动的函数

控制类函数（如设置温度/功率/阀门）测试后默认需要**人工确认**真实物理效果（`CHEMAUTO_REQUIRE_USER_CONFIRMATION=true`）。

---

## Windows 注意事项

- 命令行运行前建议设置 `export PYTHONIOENCODING=utf-8`，避免 emoji 输出触发 GBK 编码崩溃。
- 直接运行 `scripts/` 下的脚本时需先 `export PYTHONPATH="."`。

---

## 目录结构

```
app/
├── agent.py              # 根 agent（4 个子 agent 编排）
├── agents/               # build / test / invoke 等子 agent
├── tools/                # build/test/publish/invoke/manual 工具
├── services/             # artifact / registry / ocr / session 等服务
├── schemas/              # Pydantic 数据契约
├── skills/               # 7 个协议模板（SCPI/Modbus/binary-frame/...）
├── prompts/              # 各 agent 的 prompt
├── llm/                  # 模型工厂（DeepSeek 路由）
└── runtime/              # 运行配置
```

---

## 数据与历史记录

运行 `adk run app` / `adk web .` 时，对话和产物默认**保存在本地**（SQLite + 文件），关掉重开仍在：

| 内容 | 位置 |
|---|---|
| 对话历史 / session 状态 | `app/.adk/session.db` |
| PDF、生成的代码、发布产物、设备注册表 | `app/.adk/artifacts/` |

- **纯本地存储**，不上传任何服务器。
- 已在 `.gitignore` 排除（`.adk/`），不会提交进 git、也不会随公开仓库泄露。
- **重启不丢**：同一 session 下次打开还能继续对话；已发布设备也跨会话可见。
- **想清空**：直接删除 `app/.adk/` 目录即可全部重置。

以下情况会退回内存存储、重启丢失：

- 设置了环境变量 `ADK_DISABLE_LOCAL_STORAGE=1`
- `app/` 目录不可写
- 在 Cloud Run / Kubernetes 等容器环境运行（除非设 `ADK_FORCE_LOCAL_STORAGE=1`）

> 对话很长时（超过 200 个事件）会自动把早期轮次压缩成摘要，防止上下文过长；会话本身仍保留。

---

## 常用命令

### 安装

```bash
uv sync --extra gpu    # GPU 版（NVIDIA 显卡）
uv sync --extra cpu    # CPU 版
```

### 运行

```bash
uv run adk web .        # Web 界面（推荐）
uv run adk run app      # 命令行对话
```

### 重开对话 / 清空历史

| 场景 | 做法 |
|---|---|
| CLI 开新会话 | 退出当前对话（Ctrl+C），重新 `uv run adk run app`（每次运行都是新会话） |
| Web 开新会话 | 在界面上点「新建会话 / New session」按钮 |
| 只清对话历史 | 删除 `app/.adk/session.db` |
| 清空全部数据 | 删除整个 `app/.adk/`（连已发布设备一起清空） |

### 会话保存 / 恢复（可选）

```bash
uv run adk run app --save_session             # 退出时把会话存成 JSON
uv run adk run app --resume session.json      # 从保存的会话继续
```

### 强制不保存（内存模式）

```bash
uv run adk run app --no_use_local_storage     # 本次运行不落盘，退出即丢
```

---

## 说明

- 协议模板在 `app/skills/`，最终发布的驱动属于用户产物，不存回模板。
