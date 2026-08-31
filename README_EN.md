# ChemAutoAgent

[English](README_EN.md) | [简体中文](README.md)

A **multi-agent automation system** for laboratory hardware — chemistry instruments, spectrometers, pumps/valves, temperature controllers, and more.

After uploading an instrument PDF manual, the system automatically:

```
PDF manual → manual understanding (OCR) → protocol/safety/function extraction → Python driver generation
          → per-function real-device testing → error repair → publish & register → safe invocation
```

It includes 4 sub-agents: `build_agent` (build drivers), `test_agent` (test & repair), `publish_agent` (publish), `invoke_agent` (invoke).

---

## Requirements

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (package manager + runner)
- **DeepSeek API Key** (for model inference, `DEEPSEEK_API_KEY`)

### OCR backend (choose one)

| Mode | Hardware | OCR speed | Install |
|------|----------|-----------|---------|
| **GPU** | NVIDIA GPU + CUDA 12.9 driver + cuDNN 9 | Fast | `uv sync --extra gpu` |
| **CPU** | None | Slow | `uv sync --extra cpu` |

> The GPU version requires a fairly new driver (must support CUDA 12.9). If unsure, run `nvidia-smi` to check the CUDA version; use the CPU version if unsupported.
>
> The CPU version needs no GPU and works on any machine, but OCR parsing of PDFs is slower (large manuals may take minutes).

---

## Installation

```bash
git clone <repo-url>
cd <dir>

# Install uv first (if not installed): https://docs.astral.sh/uv/#installation

# Choose one:
uv sync --extra gpu    # GPU version (NVIDIA GPU)
uv sync --extra cpu    # CPU version
```

The first `uv sync` installs PaddleOCR/PaddleX dependencies (hundreds of MB) and downloads OCR model weights; keep the network connected.

---

## Configuration

```bash
cp .env.example .env
```

Edit `.env` and fill in your API key:

```dotenv
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

---

## Run

```bash
# CLI (recommended)
uv run adk run app

# Web UI (has bugs, not recommended for now)
uv run adk web .
```

---

## Basic workflow

1. **Add a new instrument**: upload the PDF manual → `build_agent` runs OCR, extracts the protocol, and generates a Python driver
2. **Test**: `test_agent` connects to the real device, tests each function, diagnoses failures, and fixes the code
3. **Publish**: `publish_agent` saves the tested driver into the registry (reusable across sessions)
4. **Invoke**: `invoke_agent` lists / invokes functions of published drivers

Control functions (e.g., set temperature / power / valve) require **human confirmation** of the physical effect after testing by default (`CHEMAUTO_REQUIRE_USER_CONFIRMATION=true`).

---

## Directory structure

```
app/
├── agent.py              # root agent (orchestrates 4 sub-agents)
├── agents/               # build / test / invoke sub-agents
├── tools/                # build/test/publish/invoke/manual tools
├── services/             # artifact / registry / ocr / session services
├── schemas/              # Pydantic data contracts
├── skills/               # 7 protocol templates (SCPI/Modbus/binary-frame/...)
├── prompts/              # prompts for each agent
├── llm/                  # model factory (DeepSeek routing)
└── runtime/              # runtime config
```

---

## Data & history

When running `adk run app` / `adk web .`, conversations and artifacts are saved **locally** by default (SQLite + files) and persist across restarts:

| Content | Location |
|---|---|
| Conversation history / session state | `app/.adk/session.db` |
| PDFs, generated code, published artifacts, device registry | `app/.adk/artifacts/` |

- **Local storage only** — nothing is uploaded to any server.
- Excluded in `.gitignore` (`.adk/`), so it is neither committed to git nor leaked with the public repo.
- **Persists across restarts**: the same session resumes next time; published devices are visible across sessions.
- **To reset**: just delete the `app/.adk/` directory.

The following cases fall back to in-memory storage (lost on restart):

- Environment variable `ADK_DISABLE_LOCAL_STORAGE=1` is set
- The `app/` directory is not writable
- Running in a container environment such as Cloud Run / Kubernetes (unless `ADK_FORCE_LOCAL_STORAGE=1`)

> Long conversations (more than 200 events) auto-compress earlier turns into summaries to avoid an oversized context; the session itself is kept.

---

## Common commands

### Install

```bash
uv sync --extra gpu    # GPU version (NVIDIA GPU)
uv sync --extra cpu    # CPU version
```

### Run

```bash
uv run adk run app      # CLI (recommended)
uv run adk web .        # Web UI (has bugs, not recommended for now)
```

### New conversation / clear history

| Scenario | How |
|---|---|
| New CLI session | Exit the current conversation (Ctrl+C), then rerun `uv run adk run app` (each run is a new session) |
| New Web session | Click the "New session" button in the UI |
| Clear conversation only | Delete `app/.adk/session.db` |
| Clear everything | Delete the entire `app/.adk/` (also clears published devices) |

### Resume a specific session

> Sessions are saved by default even without extra flags (see "Data & history"). The following is the advanced export / resume usage.

- **CLI resume**: `adk run` creates a new session each run and cannot pick a session from `session.db` by id directly; you need to export first, then resume:

```bash
uv run adk run app --save_session                         # export as app/<session_id>.session.json on exit
uv run adk run app --resume app/<session_id>.session.json # resume that session
```

> The Web UI can also list and resume sessions, but the web UI has bugs and is not recommended for now.

### Force no persistence (in-memory)

```bash
uv run adk run app --no_use_local_storage     # not persisted this run, lost on exit
```

---

## Notes

- Protocol templates live in `app/skills/`; final published drivers are user artifacts and are not written back to templates.

---

## Citation

If this work helps your research, please cite:

> Wu, C.; Jiang, S.; Zuo, Z.; Li, J. ChemAutoAgent: A Multi-Agent System for Evidence-Controlled Laboratory Instrument Driver Generation from Text-Based Manuals. *Appl. Sci.* **2026**, *16*, 8291. https://doi.org/10.3390/app16168291

Paper: <https://www.mdpi.com/2076-3417/16/16/8291>

BibTeX:

```bibtex
@Article{app16168291,
AUTHOR = {Wu, Cheda and Jiang, Shunnan and Zuo, Zhaohong and Li, Jun},
TITLE = {ChemAutoAgent: A Multi-Agent System for Evidence-Controlled Laboratory Instrument Driver Generation from Text-Based Manuals},
JOURNAL = {Applied Sciences},
VOLUME = {16},
YEAR = {2026},
NUMBER = {16},
ARTICLE-NUMBER = {8291},
URL = {https://www.mdpi.com/2076-3417/16/16/8291},
ISSN = {2076-3417},
DOI = {10.3390/app16168291}
}
```
