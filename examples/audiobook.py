"""
Audiobook generator — reads a .txt file and produces one .wav per chapter.

Usage:
    python examples/audiobook.py book.txt --voice "Ngọc Linh" --out outputs/audiobook/

The script splits the input on chapter headings (lines starting with "Chương",
"Chapter", "Phần", "Mở đầu", etc.).  If no headings are found it treats the
whole file as a single chapter.

Each chapter is saved as  01_mo_dau.wav, 02_chuong_mot.wav, …
Use --merge to also produce a combined full_book.wav.
Rerunning skips chapters whose output file already exists.
"""

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
from vieneu import Vieneu


# ── chapter splitter ─────────────────────────────────────────────────────────

_CHAPTER_RE = re.compile(
    r"^\s*(?:Chương|Chapter|Phần|Mở\s+đầu|Lời\s+mở\s+đầu|Vĩ\s+thanh|Kết|Epilogue|Prologue|Lời\s+tựa|Lời\s+kết)\b.*$"
    r"|^\s*[IVXLCDM]+\.\s*$"          # Roman numeral headings
    r"|^\s*\d+\.\s*$",                 # Plain number headings  e.g. "3."
    re.MULTILINE | re.IGNORECASE,
)


def slugify(title: str, max_len: int = 40) -> str:
    """Convert a title to a safe ASCII filename slug, preserving Vietnamese letters."""
    # Decompose Unicode so diacritics become separate combining chars, then drop them
    nfkd = unicodedata.normalize("NFKD", title)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_str).strip().lower()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len].strip("_") or "chapter"


def split_into_chapters(text: str) -> list[tuple[str, str]]:
    """Return [(title, body), …].  Falls back to one chapter if no headings."""
    matches = list(_CHAPTER_RE.finditer(text))

    if not matches:
        return [("Chapter 1", text.strip())]

    chapters: list[tuple[str, str]] = []

    # Capture any text that appears before the first heading
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


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VieNeu audiobook generator")
    parser.add_argument("input", help="Path to the .txt book file")
    parser.add_argument("--voice", default=None, help="Preset voice name (default: Ngọc Lan)")
    parser.add_argument("--ref-audio", default=None, help="Reference WAV for voice cloning")
    parser.add_argument("--out", default="outputs/audiobook", help="Output directory")
    parser.add_argument("--silence", type=float, default=0.3,
                        help="Silence between sentences in seconds (default: 0.3)")
    parser.add_argument("--title-gap", type=float, default=0.8,
                        help="Silence after the title announcement before body (default: 0.8s)")
    parser.add_argument("--merge", action="store_true",
                        help="Also produce a combined full_book.wav in the output directory")
    parser.add_argument("--chapter-gap", type=float, default=1.5,
                        help="Silence gap between chapters in full_book.wav (default: 1.5s, only used with --merge)")
    args = parser.parse_args()

    txt_path = Path(args.input)
    if not txt_path.exists():
        sys.exit(f"File not found: {txt_path}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    text = txt_path.read_text(encoding="utf-8")
    chapters = split_into_chapters(text)
    print(f"Found {len(chapters)} chapter(s) in {txt_path.name}")

    tts = Vieneu()
    sample_rate = tts.sample_rate
    total_start = time.time()

    infer_kwargs = dict(
        voice=args.voice,
        ref_audio=args.ref_audio,
        silence_p=args.silence,
        temperature=0.7,
    )

    chapter_wavs: list[np.ndarray] = []
    render_idx = 0

    for idx, (title, body) in enumerate(chapters, start=1):
        if not body:
            print(f"  Chapter {idx} ({title}): empty — skipping")
            continue

        render_idx += 1
        out_path = out_dir / f"{render_idx:02d}_{slugify(title)}.wav"

        if out_path.exists():
            print(f"  Chapter {idx} ({title[:60]}): already exists — skipping")
            if args.merge:
                import soundfile as sf
                audio, _ = sf.read(str(out_path), dtype="float32")
                chapter_wavs.append(audio)
            continue

        print(f"  Rendering chapter {idx}/{len(chapters)}: {title[:60]}")

        t0 = time.time()
        title_wav = tts.infer(title, **infer_kwargs)
        body_wav = tts.infer(body, **infer_kwargs)
        elapsed = time.time() - t0

        gap_samples = int(args.title_gap * sample_rate)
        audio = np.concatenate([title_wav, np.zeros(gap_samples, dtype=np.float32), body_wav])

        tts.save(audio, str(out_path))
        chapter_wavs.append(audio)
        audio_dur = len(audio) / sample_rate
        print(f"    → {out_path}  (audio: {audio_dur:.1f}s | render: {elapsed:.1f}s | RTF: {elapsed/audio_dur:.2f}x)")

    if not chapter_wavs:
        sys.exit("No audio was generated.")

    total_elapsed = time.time() - total_start
    total_audio = sum(len(w) for w in chapter_wavs) / sample_rate
    print(f"\nTotal: {len(chapter_wavs)} chapter(s) | audio: {total_audio/60:.1f} min | time: {total_elapsed:.1f}s | RTF: {total_elapsed/total_audio:.2f}x")

    if args.merge:
        t0 = time.time()
        gap_samples = int(args.chapter_gap * sample_rate)
        gap = np.zeros(gap_samples, dtype=np.float32)
        combined = np.concatenate(
            [part for wav in chapter_wavs for part in (wav, gap)][:-1]
        )
        full_path = out_dir / "full_book.wav"
        tts.save(combined, str(full_path))
        elapsed = time.time() - t0
        print(f"\nFull audiobook saved → {full_path}  ({len(combined)/sample_rate/60:.1f} min | merge: {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
