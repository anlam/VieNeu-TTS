"""
Audiobook generator — produces one .wav + .srt per chapter.

Supports two input modes:
  - Single .txt file: chapters are split on headings (Chương, Chapter, Phần, …)
  - Folder of .txt files: each file is one chapter, sorted by filename.
    First line of each file = chapter title; rest = body.

Usage:
    python examples/audiobook.py book.txt --voice "Bình An" --ref-audio sample.wav --out output/audiobook/
    python examples/audiobook.py chapters/ --voice "Bình An" --out output/audiobook/

Each chapter is saved as 01_mo_dau.wav + 01_mo_dau.srt, 02_chuong_mot.wav + …
Use --merge to also produce a combined full_book.wav + full_book.srt.
Rerunning skips chapters whose output files already exist.

Audio pipeline mirrors tts.infer() exactly (normalize_to_chunks_v3_with_gaps →
_infer_chunks GPU batch → _compress_silence → join with variable gap silences),
so quality and speed match audiobook_simple.py. SRT timestamps are derived from
the exact duration of each 256-char chunk as it is generated.

The chapter title is prepended to the body so the model reads it as one
continuous passage — avoids the short-utterance artifacts of speaking the title
in isolation.

Options:
  --voice           Preset voice name (default: Ngọc Lan)
  --ref-audio       Reference WAV for voice cloning
  --out             Output directory (default: outputs/audiobook)
  --merge           Also produce full_book.wav + full_book.srt
  --chapter-gap     Silence between chapters in full_book (default: 1.5s)
  --bumper          Path to .txt file with promo lines (one per line)
  --bumper-interval Target minutes between bumper injections (default: 10)
  --silence         Silence padding around bumpers in seconds (default: 0.3)
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
from vieneu.v3turbo import _compress_silence
from vieneu_utils.core_utils import join_audio_chunks, gaps_to_silence
from vieneu_utils.phonemize_text import normalize_to_chunks_v3_with_gaps


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
        return [("", text.strip())]

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
    bumper_wav: np.ndarray | None = None,
    bumper_interval: float = 600.0,
) -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """Render a chapter and return (audio, srt_entries) with timestamps.

    All sentence chunks are batched in a single GPU pass via _infer_chunks so
    throughput matches audiobook_simple.py. SRT timestamps are exact at the
    chunk boundary level (one entry per sentence chunk).
    """
    sample_rate = tts.sample_rate
    all_wavs: list[np.ndarray] = []
    srt_entries: list[tuple[float, float, str]] = []
    cursor = 0.0

    def _inject_bumper() -> None:
        nonlocal cursor
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))
        all_wavs.append(bumper_wav)
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))
        cursor += silence_p + len(bumper_wav) / sample_rate + silence_p

    if bumper_wav is not None:
        _inject_bumper()

    full_body = f"{title}. {body}" if (title and body) else (body or title)

    # Mirror tts.infer() exactly: same chunker, same batching, same silence strategy
    chunks, gaps = normalize_to_chunks_v3_with_gaps(full_body, max_chars=256)
    gap_durs = gaps_to_silence(gaps)  # variable silence per boundary type (para > sentence > minor)

    speaker_emb, ref_codes = tts._resolve_ref(
        infer_kwargs.get("voice"), infer_kwargs.get("ref_audio"), True, True
    )
    sampling = dict(
        temperature=infer_kwargs.get("temperature", 0.8),
        top_k=25, top_p=0.95, max_new_frames=300, repetition_penalty=1.2,
    )
    chunk_wavs = tts._infer_chunks(
        chunks, speaker_emb, ref_codes,
        infer_kwargs.get("style", "doc_truyen"), True,
        tts.max_batch_size, sampling,
    )
    chunk_wavs = [_compress_silence(w, sample_rate) for w in chunk_wavs]

    # Middle bumpers — estimate total duration first
    injection_indices: set[int] = set()
    if bumper_wav is not None and len(chunk_wavs) > 1:
        est_dur = sum(len(w) / sample_rate for w in chunk_wavs) + sum(gap_durs)
        if est_dur > bumper_interval:
            n_bumpers = min(max(0, int(est_dur / bumper_interval)), 5, len(chunk_wavs) - 1)
            if n_bumpers > 0:
                injection_indices = set(random.sample(range(1, len(chunk_wavs)), n_bumpers))

    for i, (chunk, wav) in enumerate(zip(chunks, chunk_wavs)):
        if i in injection_indices:
            _inject_bumper()
        dur = len(wav) / sample_rate
        srt_entries.append((cursor, cursor + dur, chunk.capitalize()))
        cursor += dur
        gap = gap_durs[i] if i < len(gap_durs) else 0.0
        cursor += gap
        all_wavs.append(wav)
        if i < len(gap_durs):
            all_wavs.append(np.zeros(int(gap_durs[i] * sample_rate), dtype=np.float32))

    if bumper_wav is not None:
        all_wavs.append(np.zeros(int(silence_p * sample_rate), dtype=np.float32))
        all_wavs.append(bumper_wav)
        cursor += silence_p + len(bumper_wav) / sample_rate

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
        temperature=0.8,
        style="doc_truyen",
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
                import re as _re
                import soundfile as sf
                audio, _ = sf.read(str(out_wav), dtype="float32")
                chapter_wavs.append(audio)
                raw = out_srt.read_text(encoding="utf-8")
                def _ts(t):
                    h, mi, s_ms = t.split(":")
                    s, ms = s_ms.split(",")
                    return int(h)*3600 + int(mi)*60 + int(s) + int(ms)/1000
                entries = [
                    (_ts(m.group(1)), _ts(m.group(2)), m.group(3).strip())
                    for m in _re.finditer(
                        r"\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\Z)",
                        raw, _re.DOTALL
                    )
                ]
                chapter_srts.append(entries if entries else None)
            continue

        print(f"  Rendering chapter {idx}/{len(chapters)}: {title[:60]}")

        t0 = time.time()
        audio, srt_entries = render_chapter(
            tts, title, body, infer_kwargs, args.silence,
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
