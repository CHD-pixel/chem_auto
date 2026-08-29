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

## 说明

- 设备注册表和已发布驱动保存在 `app/.adk/` 下（运行期数据，已在 `.gitignore` 排除）。
- 协议模板在 `app/skills/`，最终发布的驱动属于用户产物，不存回模板。
