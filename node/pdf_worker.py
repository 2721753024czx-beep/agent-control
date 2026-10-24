"""Bounded PDF text/OCR extraction in a disposable child process."""
import json
import sys
import os
from pathlib import Path


def tessdata(options):
    languages = options.get("language", "eng+chi_sim").split("+")
    if not languages or any(not name.replace("_", "").isalnum() for name in languages):
        raise ValueError("invalid OCR language")
    configured = options.get("tessdata") or os.environ.get("TESSDATA_PREFIX")
    candidates = [Path(configured)] if configured else [
        Path(__file__).resolve().parents[1] / "runtime" / "tessdata",
        Path("/opt/homebrew/share/tessdata"), Path("/usr/local/share/tessdata"),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tessdata",
        Path("/usr/share/tesseract-ocr/5/tessdata"), Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    ]
    for folder in candidates:
        if all((folder / (language + ".traineddata")).is_file() for language in languages):
            return str(folder)
    raise ValueError("missing OCR language data")


def extract(path, options):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，请安装 node/requirements.txt") from exc
    parts, warnings, ocr_pages = [], [], 0
    remaining = 60000
    with fitz.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass:
            raise ValueError("不支持非 PDF 或加密 PDF，请先在电脑端解密")
        if not 0 < len(doc) <= 50:
            raise ValueError("PDF 页数须为 1–50 页，请拆分后发送")
        for index, page in enumerate(doc):
            text = page.get_text() or ""
            if len(text.strip()) < 20:
                if not options.get("ocr", True):
                    warnings.append("第 %d 页文字不足，OCR 未启用" % (index + 1))
                elif ocr_pages >= 10:
                    warnings.append("第 %d 页未识别：单文件最多 OCR 10 页" % (index + 1))
                elif page.rect.width * page.rect.height * (120 / 72) ** 2 > 12000000:
                    warnings.append("第 %d 页尺寸过大，未进行 OCR" % (index + 1))
                else:
                    ocr_pages += 1
                    try:
                        tp = page.get_textpage_ocr(language=options.get("language", "eng+chi_sim"),
                                                   dpi=120, full=True,
                                                   tessdata=tessdata(options))
                        text = page.get_text(textpage=tp) or text
                        if not text.strip():
                            warnings.append("第 %d 页 OCR 未识别到文字" % (index + 1))
                    except Exception:
                        warnings.append("第 %d 页扫描/少文字内容未识别；请安装对应 Tesseract 语言包并配置 pdf.tessdata" % (index + 1))
            block = "\n[第 %d 页]\n%s" % (index + 1, text)
            parts.append(block[:remaining])
            remaining -= min(len(block), remaining)
            if remaining <= 0:
                warnings.append("文本超过 60000 字符，后续内容未纳入，请拆分文件")
                break
        return {"text": "".join(parts), "pages": len(doc), "warnings": warnings,
                "ocr_pages": ocr_pages}


if __name__ == "__main__":
    try:
        print(json.dumps(extract(sys.argv[1], json.loads(sys.argv[2])), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
