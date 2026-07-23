"""
Audiobook generator — produces one .wav + .srt per chapter.

Supports two input modes:
  - Single .txt file: chapters are split on headings (Chương, Chapter, Phần, …)
  - Folder of .txt files: each file is one chapter, sorted by filename.
    First line of each file = chapter title; rest = body.

Usage:
    python examples/audiobook.py book.txt --voice "Ngọc Linh" --out outputs/audiobook/
    python examples/audiobook.py chapters/ --voice "Ngọc Linh" --out outputs/audiobook/

Each chapter is saved as  01_mo_dau.wav + 01_mo_dau.srt, 02_chuong_mot.wav + …
Use --merge to also produce a combined full_book.wav + full_book.srt.
Rerunning skips chapters whose output file already exists.
"""

import argparse
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
from vieneu import Vieneu
from vieneu_utils.core_utils import split_text_into_chunks


# ── chapter splitter ─────────────────────────────────────────────────────────

_CHAPTER_RE = re.compile(
    # Rule 1: blank line on both sides (or file boundary) — any heading length
    r"(?:(?<=\n\n)|\A)\s*"
    r"(?:Chương|Chapter|Phần|Mở\s+đầu|Lời\s+mở\s+đầu|Vĩ\s+thanh|Kết|Epilogue|Prologue|Lời\s+tựa|Lời\s+kết)\b"
    r"[^\n]*(?=\n\n|\Z)"
    r"|"
    # Rule 2: short standalone line ≤ 30 chars after keyword — single-newline format
    r"^\s*(?:Chương|Chapter|Phần|Mở\s+đầu|Lời\s+mở\s+đầu|Vĩ\s+thanh|Kết|Epilogue|Prologue|Lời\s+tựa|Lời\s+kết)\b[^\n]{0,30}$"
    r"|^\s*[IVXLCDM]+\.\s*$"          # Roman numeral headings
    r"|^\s*\d+\.\s*$",                 # Plain number headings  e.g. "3."
    re.MULTILINE | re.IGNORECASE,
)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries: list[tuple[float, float, str]], path: Path) -> None:
    """Write (start, end, text) entries to an SRT file."""
    lines = []
    for i, (start, end, text) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}")
        lines.append(text.strip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def slugify(title: str, max_len: int = 40) -> str:
    """Convert a title to a safe ASCII filename slug."""
    # đ/Đ have no NFKD decomposition so must be mapped manually before normalization
    title = title.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", title)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", ascii_str).strip().lower()
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:max_len].strip("_") or "chapter"


def chapters_from_folder(folder: Path) -> list[tuple[str, str]]:
    """Each .txt file in folder (sorted by name) is one chapter.
    First line = title, remaining lines = body.
    """
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


def render_chapter(
    tts: Vieneu,
    title: str,
    body: str,
    infer_kwargs: dict,
    silence_p: float,
    title_gap: float,
    bumper_wav: np.ndarray | None = None,
    bumper_interval: float = 600.0,
) -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """Render a chapter and return (audio, srt_entries) with timestamps.

    bumper_wavs are injected at random positions between sentences.
    They are excluded from SRT entries but cursor advances correctly.
    """
    sample_rate = tts.sample_rate
    all_wavs: list[np.ndarray] = []
    srt_entries: list[tuple[float, float, str]] = []
    cursor = 0.0

    # Title — rendered as a single chunk
    title_wav = tts.infer(title, **infer_kwargs)
    title_dur = len(title_wav) / sample_rate
    srt_entries.append((cursor, cursor + title_dur, title))
    cursor += title_dur + title_gap
    all_wavs.append(title_wav)
    all_wavs.append(np.zeros(int(title_gap * sample_rate), dtype=np.float32))

    def _inject_bumper() -> None:
        nonlocal cursor
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))
        all_wavs.append(bumper_wav)
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))
        cursor += silence_p + len(bumper_wav) / sample_rate + silence_p

    # Collect all body chunks so we can decide injection points upfront
    text_chunks = split_text_into_chunks(body)
    sentence_pairs = list(zip(text_chunks, tts.infer_stream(body, **infer_kwargs)))

    # Beginning bumper — always inject if bumpers provided
    if bumper_wav is not None:
        _inject_bumper()

    # Middle bumpers — only if chapter is long enough to warrant them
    injection_indices: set[int] = set()
    if bumper_wav is not None and len(sentence_pairs) > 1:
        est_body_dur = sum(len(w) / sample_rate + silence_p for _, w in sentence_pairs)
        if est_body_dur > bumper_interval:
            n_bumpers = max(0, int(est_body_dur / bumper_interval))
            n_bumpers = min(n_bumpers, 5, len(sentence_pairs) - 1)
            if n_bumpers > 0:
                injection_indices = set(random.sample(range(1, len(sentence_pairs)), n_bumpers))

    for i, (sentence, wav) in enumerate(sentence_pairs):
        if i in injection_indices:
            _inject_bumper()

        dur = len(wav) / sample_rate
        srt_entries.append((cursor, cursor + dur, sentence))
        cursor += dur + silence_p
        all_wavs.append(wav)
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))

    # End bumper — reuse trailing silence as pre-gap, then append bumper
    if bumper_wav is not None:
        all_wavs.append(bumper_wav)  # trailing silence from loop acts as the gap
        cursor += len(bumper_wav) / sample_rate
    else:
        all_wavs.pop()  # drop trailing silence

    audio = np.concatenate(all_wavs)
    return audio, srt_entries


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VieNeu audiobook generator")
    parser.add_argument("input", help="Path to a .txt book file or a folder of per-chapter .txt files")
    parser.add_argument("--voice", default=None, help="Preset voice name (default: Ngọc Lan)")
    parser.add_argument("--ref-audio", default=None, help="Reference WAV for voice cloning")
    parser.add_argument("--out", default="outputs/audiobook", help="Output directory")
    parser.add_argument("--silence", type=float, default=0.3,
                        help="Silence between sentences in seconds (default: 0.3)")
    parser.add_argument("--title-gap", type=float, default=0.8,
                        help="Silence after the title announcement before body (default: 0.8s)")
    parser.add_argument("--merge", action="store_true",
                        help="Also produce a combined full_book.wav + full_book.srt")
    parser.add_argument("--chapter-gap", type=float, default=1.5,
                        help="Silence gap between chapters in full_book.wav (default: 1.5s, only with --merge)")
    parser.add_argument("--bumper", default=None,
                        help="Path to txt file with channel promo texts, one per line")
    parser.add_argument("--bumper-interval", type=float, default=10.0,
                        help="Target minutes between bumper injections (default: 10)")
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
    total_start = time.time()

    infer_kwargs = dict(
        voice=args.voice,
        ref_audio=args.ref_audio,
        temperature=0.6,
    )

    # Pre-render bumper (done once, reused across all chapters)
    bumper_wav: np.ndarray | None = None
    if args.bumper:
        bumper_path = Path(args.bumper)
        if not bumper_path.exists():
            sys.exit(f"Bumper file not found: {bumper_path}")
        bumper_text = " ".join(l.strip() for l in bumper_path.read_text(encoding="utf-8").splitlines() if l.strip())
        if bumper_text:
            print("Pre-rendering bumper…")
            bumper_wav = tts.infer(bumper_text, **infer_kwargs)
            print(f"  Bumper duration: {fmt_duration(len(bumper_wav)/sample_rate)} | interval: every ~{args.bumper_interval:.0f} min")

    chapter_wavs: list[np.ndarray] = []
    chapter_srts: list[list[tuple[float, float, str]]] = []
    render_idx = 0

    for idx, (title, body) in enumerate(chapters, start=1):
        if not body:
            print(f"  Chapter {idx} ({title}): empty — skipping")
            continue

        render_idx += 1
        stem = f"{render_idx:02d}_{slugify(title)}"
        out_wav = out_dir / f"{stem}.wav"
        out_srt = out_dir / f"{stem}.srt"

        if out_wav.exists() and out_srt.exists():
            print(f"  Chapter {idx} ({title[:60]}): already exists — skipping")
            if args.merge:
                import soundfile as sf
                audio, _ = sf.read(str(out_wav), dtype="float32")
                chapter_wavs.append(audio)
                # SRT timestamps will be rebuilt with offset during merge
                chapter_srts.append(None)
            continue

        print(f"  Rendering chapter {idx}/{len(chapters)}: {title[:60]}")

        t0 = time.time()
        audio, srt_entries = render_chapter(
            tts, title, body, infer_kwargs, args.silence, args.title_gap,
            bumper_wav=bumper_wav,
            bumper_interval=args.bumper_interval * 60,
        )
        elapsed = time.time() - t0

        tts.save(audio, str(out_wav))
        write_srt(srt_entries, out_srt)
        chapter_wavs.append(audio)
        chapter_srts.append(srt_entries)

        audio_dur = len(audio) / sample_rate
        print(f"    → {out_wav.name}  (audio: {fmt_duration(audio_dur)} | render: {fmt_duration(elapsed)} | RTF: {elapsed/audio_dur:.2f}x)")

    if not chapter_wavs:
        sys.exit("No audio was generated.")

    total_elapsed = time.time() - total_start
    total_audio = sum(len(w) for w in chapter_wavs) / sample_rate
    print(f"\nTotal: {len(chapter_wavs)} chapter(s) | audio: {fmt_duration(total_audio)} | time: {fmt_duration(total_elapsed)} | RTF: {total_elapsed/total_audio:.2f}x")

    if args.merge:
        t0 = time.time()
        gap_samples = int(args.chapter_gap * sample_rate)
        gap = np.zeros(gap_samples, dtype=np.float32)

        combined = np.concatenate(
            [part for wav in chapter_wavs for part in (wav, gap)][:-1]
        )

        # Rebuild SRT entries with correct offsets across chapters
        merged_srt: list[tuple[float, float, str]] = []
        cursor = 0.0
        for wav, entries in zip(chapter_wavs, chapter_srts):
            if entries is not None:
                for start, end, txt in entries:
                    merged_srt.append((cursor + start, cursor + end, txt))
            cursor += len(wav) / sample_rate + args.chapter_gap

        full_wav = out_dir / "full_book.wav"
        full_srt = out_dir / "full_book.srt"
        tts.save(combined, str(full_wav))
        write_srt(merged_srt, full_srt)

        elapsed = time.time() - t0
        print(f"Full audiobook → {full_wav.name}  (audio: {fmt_duration(len(combined)/sample_rate)} | merge: {fmt_duration(elapsed)})")
        print(f"Full subtitles → {full_srt.name}  ({len(merged_srt)} entries)")


if __name__ == "__main__":
    main()
