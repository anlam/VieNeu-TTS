"""
Simple audiobook generator — one .wav per chapter, no SRT.

Lets the internal infer() handle all chunking (max_chars=256) so we can test
whether the sentence-dropping problem exists without our custom chunking layer.

Usage:
    python examples/audiobook_simple.py book.txt --voice "Ngọc Linh" --out outputs/simple/
    python examples/audiobook_simple.py chapters/ --voice "Ngọc Linh" --out outputs/simple/
"""

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

from vieneu import Vieneu


_CHAPTER_RE = re.compile(
    r"(?:(?<=\n\n)|\A)\s*"
    r"(?:Chương|Chapter|Phần|Mở\s+đầu|Lời\s+mở\s+đầu|Vĩ\s+thanh|Kết|Epilogue|Prologue|Lời\s+tựa|Lời\s+kết)\b"
    r"[^\n]*(?=\n\n|\Z)"
    r"|"
    r"^\s*(?:Chương|Chapter|Phần|Mở\s+đầu|Lời\s+mở\s+đầu|Vĩ\s+thanh|Kết|Epilogue|Prologue|Lời\s+tựa|Lời\s+kết)\b[^\n]{0,30}$"
    r"|^\s*[IVXLCDM]+\.\s*$"
    r"|^\s*\d+\.\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def slugify(title: str, max_len: int = 40) -> str:
    title = title.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", title)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_str).strip().lower()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len].strip("_") or "chapter"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s" if m else f"{s}s"


def chapters_from_folder(folder: Path) -> list[tuple[str, str]]:
    txt_files = sorted(folder.glob("*.txt"))
    if not txt_files:
        sys.exit(f"No .txt files found in {folder}")
    chapters = []
    for f in txt_files:
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue
        lines = content.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if not body:
            title = f.stem
            body = content
        chapters.append((title, body))
    return chapters


def split_into_chapters(text: str) -> list[tuple[str, str]]:
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        return [("", text.strip())]
    chapters: list[tuple[str, str]] = []
    pre = text[:matches[0].start()].strip()
    if pre:
        chapters.append(("Mở đầu", pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start():end].strip()
        lines = block.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        chapters.append((title, body))
    return chapters


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple VieNeu audiobook generator (no SRT)")
    parser.add_argument("input", help="Path to a .txt book file or a folder of per-chapter .txt files")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--ref-audio", default=None)
    parser.add_argument("--out", default="outputs/audiobook_simple")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Not found: {input_path}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_dir():
        chapters = chapters_from_folder(input_path)
        print(f"Found {len(chapters)} chapter(s) in {input_path.name}/")
    else:
        text = input_path.read_text(encoding="utf-8")
        chapters = split_into_chapters(text)
        print(f"Found {len(chapters)} chapter(s) in {input_path.name}")

    tts = Vieneu()
    sample_rate = tts.sample_rate

    infer_kwargs = dict(
        voice=args.voice,
        ref_audio=args.ref_audio,
        temperature=0.5,
        style="doc_truyen",
        apply_watermark=False,
    )

    for idx, (title, body) in enumerate(chapters, start=1):
        slug = slugify(title)
        out_wav = out_dir / f"{idx:02d}_{slug}.wav"

        if out_wav.exists():
            print(f"[{idx}/{len(chapters)}] Skipping (exists): {out_wav.name}")
            continue

        print(f"[{idx}/{len(chapters)}] {title}")
        t0 = time.time()

        # Title prepended to body so the model treats them as one continuous passage
        full_text = f"{title}. {body}" if (title and body) else (body or title)
        audio = tts.infer(full_text, **infer_kwargs)
        tts.save(audio, str(out_wav))

        dur = len(audio) / sample_rate
        elapsed = time.time() - t0
        print(f"  -> {out_wav.name} | {fmt_duration(dur)} audio in {fmt_duration(elapsed)}")

    print("Done.")


if __name__ == "__main__":
    main()
