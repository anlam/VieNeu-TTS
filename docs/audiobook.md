# Audiobook Generator

Converts a book into per-chapter WAV + SRT subtitle files using VieNeu TTS.

Supports two input modes:
- **Single `.txt` file** — chapters are detected automatically by heading keywords
- **Folder of `.txt` files** — each file is one chapter, sorted by filename

## Basic usage

```bash
# Single file — chapters split automatically by headings
python examples/audiobook.py book.txt --voice "Bình An" --out output/audiobook/

# Folder — one .txt file per chapter
python examples/audiobook.py chapters/ --voice "Bình An" --out output/audiobook/
```

Each chapter is saved as `01_mo_dau.wav` + `01_mo_dau.srt`, `02_chuong_mot.wav` + `02_chuong_mot.srt`, … in the output directory.  
The chapter title is spoken aloud before the chapter body.  
Rerunning automatically skips chapters whose output files already exist.

## Options

| Flag | Default | Description |
|---|---|---|
| `--voice` | Ngọc Lan | Preset voice name. See available voices below. |
| `--ref-audio` | — | Path to a 3–5s WAV clip for voice cloning. Anchors accent/style across all chunks. |
| `--out` | `outputs/audiobook` | Output directory (created if missing). |
| `--silence` | `0.3` | Seconds of silence between sentences within a chapter. |
| `--title-gap` | `0.8` | Seconds of silence between the title announcement and the chapter body. |
| `--merge` | off | Also produce a combined `full_book.wav` + `full_book.srt`. |
| `--chapter-gap` | `1.5` | Silence between chapters in `full_book.wav` (only with `--merge`). |
| `--bumper` | — | Path to a txt file with channel promo texts, one per line. |
| `--bumper-interval` | `10` | Target minutes between mid-chapter bumper injections (default: 10). |

## Available voices

```python
from vieneu import Vieneu
tts = Vieneu()
for label, voice_id in tts.list_preset_voices():
    print(label, voice_id)
```

| Voice | Gender | Style |
|---|---|---|
| Ngọc Lan | Female | Gentle |
| Gia Bảo | Male | Smooth |
| Thái Sơn | Male | Strong |
| Đức Trí | Male | Clear |
| Mỹ Duyên | Female | Smooth |
| Trúc Ly | Female | Youthful |
| Xuân Vĩnh | Male | Cheerful |
| Trọng Hữu | Male | Scholarly |
| Bình An | Male | Calm |
| Ngọc Linh | Female | Bright |

## Input formats

### Single file

The script splits on heading lines that match common patterns:

| Pattern | Examples |
|---|---|
| Vietnamese section words | `Chương`, `Phần`, `Mở đầu`, `Lời mở đầu`, `Vĩ thanh`, `Kết`, `Lời tựa`, `Lời kết` |
| English section words | `Chapter`, `Epilogue`, `Prologue` |
| Roman numerals | `I.`, `IV.`, `XII.` |
| Plain numbers | `1.`, `12.` |

Matching is case-insensitive. A heading is recognised if it is either:
- A **short standalone line** (≤ 30 chars after the keyword) — for books with single newlines between sections
- A line **surrounded by blank lines** on both sides — for books using blank-line paragraph separation, any heading length allowed

Any text before the first heading is automatically captured as a prologue. Sections with no body are silently skipped.

### Folder of files

Name each file so they sort in reading order:

```
chapters/
  01_mo_dau.txt
  02_chuong_mot.txt
  03_chuong_hai.txt
```

The **first line** of each file is used as the spoken chapter title; the rest is the body. If a file has only one line, the filename is used as the title and the full content as the body.

## Examples

```bash
# Default voice, individual files only
python examples/audiobook.py book.txt --out output/audiobook/

# Specific voice with merged output
python examples/audiobook.py book.txt --voice "Ngọc Linh" --out output/audiobook/ --merge

# Voice + reference audio for consistent accent (recommended)
python examples/audiobook.py book.txt --voice "Bình An" --ref-audio sample_BinhAn.wav --out output/audiobook/

# Slower pacing: more silence between sentences and a longer pause after title
python examples/audiobook.py book.txt --voice "Trọng Hữu" --silence 0.5 --title-gap 1.2 --out output/audiobook/
```

## Running in the background

The output directory must exist before redirecting the log file. Create it first, then run:

```bash
mkdir -p output/audiobook && nohup python -u examples/audiobook.py book.txt --voice "Bình An" --ref-audio sample_BinhAn.wav --out output/audiobook/ > output/audiobook/log.txt 2>&1 &
```

```bash
mkdir -p output/audiobook && nohup python -u examples/audiobook.py book.txt --voice "Bình An" --ref-audio sample_BinhAn.wav --bumper bumper.txt --out output/audiobook/ > output/audiobook/log.txt 2>&1 &
```

Monitor progress:

```bash
tail -f output/audiobook/log.txt
```

Check if still running:

```bash
ps aux | grep audiobook.py
```

Kill the process:

```bash
kill <PID>
```

## Subtitle files (SRT)

Each chapter produces a `.srt` file with sentence-level timestamps alongside the `.wav`. If `--merge` is used, a `full_book.srt` is also generated with timestamps offset across the entire book.


## Generating video for YouTube

Since subtitles cannot be displayed on audio-only files, convert each chapter to MP4 first using a cover image. The `scale` filter ensures dimensions are divisible by 2 as required by h264, and works for any image size.

```bash
ffmpeg -loop 1 -i output/audiobook/cover.jpg -i output/audiobook/01_mo_dau.wav \
       -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
       -c:v libx264 -c:a aac -shortest \
       output/audiobook/01_mo_dau.mp4
```

### Option 1 — Burn subtitles into the video (hardcoded)

Subtitles are permanently visible. Good for direct file distribution.

```bash
ffmpeg -loop 1 -i output/audiobook/cover.jpg -i output/audiobook/01_mo_dau.wav \
       -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles=output/audiobook/01_mo_dau.srt" \
       -c:v libx264 -c:a aac -shortest \
       output/audiobook/01_mo_dau.mp4
```

### Option 2 — Upload SRT separately to YouTube (recommended)

Upload the video without burned subtitles, then in YouTube Studio go to **Subtitles → Add** and upload the `.srt` file. Viewers can toggle subtitles on/off, and YouTube indexes the full text for search.


## Channel bumper / promo injection

To promote your channel during playback, create a `bumper.txt` file with one promo line per line:

```
Bạn đang nghe truyện trên kênh ABC, xin hãy nhấn like và đăng ký kênh nhé!
Đừng quên nhấn chuông để không bỏ lỡ các tập tiếp theo trên kênh ABC!
Cảm ơn bạn đã theo dõi kênh ABC, hẹn gặp lại ở tập tiếp theo!
```

Then pass it with `--bumper`:

```bash
python examples/audiobook.py book.txt --voice "Bình An" \
    --bumper bumper.txt --bumper-interval 10 \
    --out output/audiobook/
```

**How it works:**
- A random bumper line is spoken at the **beginning** of every chapter (after the title)
- A random bumper line is spoken at the **end** of every chapter
- Additional bumpers are injected **in the middle** roughly every `--bumper-interval` minutes, but only if the chapter is longer than that interval — short chapters get none
- Bumpers are **not shown in the SRT subtitles**, but all surrounding subtitle timestamps remain correct

## Accent consistency

If the voice switches accent between sentences, two fixes help:

1. **Lower temperature** — edit `temperature=0.5` in `examples/audiobook.py` for more deterministic output
2. **Use `--ref-audio`** — a reference clip anchors the accent for every chunk independently, which is the stronger fix. Extract a clip from an existing output that sounded correct:

```bash
ffmpeg -i output/audiobook/01_mo_dau.wav -ss 10 -t 5 ref_binh_an.wav
```

Then rerun with `--ref-audio ref_binh_an.wav`.
