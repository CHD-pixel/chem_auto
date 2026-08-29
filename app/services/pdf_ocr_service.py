"""PP-StructureV3 document parsing service.

Uses Paddle's native save_to_markdown() for structured output.
No custom HTML→markdown conversion, no manual region mapping.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from paddleocr import PPStructureV3


def create_pipeline(device: str = "gpu") -> PPStructureV3:
    """Create a PP-StructureV3 pipeline instance.

    Instantiate once and reuse across multiple parse_document calls.
    """
    return PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_table_recognition=True,
        use_formula_recognition=False,
        use_seal_recognition=False,
        use_chart_recognition=False,
        # ── Lower thresholds to catch faint / small text ─────────
        text_det_thresh=0.2,             # default 0.3
        text_det_box_thresh=0.5,         # default 0.6
        layout_threshold=0.3,            # default 0.5
        layout_unclip_ratio=1.2,         # default 1.0
        # ── Better layout + table models ────────────────────────
        layout_detection_model_name="PP-DocLayoutV2",
        # Use the best wired-table model (SLANeXt > SLANet_plus > SLANet)
        wired_table_structure_recognition_model_name="SLANeXt_wired",
        wired_table_cells_detection_model_name="RT-DETR-L_wired_table_cell_det",
        device=device,
    )


def parse_document(pdf_bytes: bytes, pipeline: PPStructureV3) -> list:
    """Run PP-StructureV3 on a PDF and return result objects.

    pdf_bytes are written to a temporary file because PP-StructureV3.predict()
    requires a file path, not raw bytes.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as _tmp:
            _tmp.write(pdf_bytes)
            tmp_path = _tmp.name

        return pipeline.predict(input=tmp_path)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def save_markdown(results: list, output_dir: str | Path) -> list[str]:
    """Save each page as a .md file via Paddle's native save_to_markdown().

    Returns a list of file paths in page order.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, res in enumerate(results):
        res.save_to_markdown(save_path=str(output_dir))
        # Paddle names files as {input_stem}_page_{N}.md
        # But since input is a temp file, we need to find what was saved
    # Collect saved .md files sorted by name
    md_files = sorted(output_dir.glob("*.md"))
    return [str(f) for f in md_files]


def read_markdown_texts(results: list) -> str:
    """Read markdown text from all pages and concatenate with page markers.

    Uses Paddle's res.markdown['markdown_texts'] directly.
    """
    parts: list[str] = []
    for i, res in enumerate(results):
        md = res.markdown
        text = md.get("markdown_texts", "") if isinstance(md, dict) else ""
        if text.strip():
            parts.append(f"\n[Page {i}]\n{text}")
    return "\n".join(parts)
