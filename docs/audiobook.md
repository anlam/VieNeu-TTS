# Audiobook Generator

Converts a `.txt` file into per-chapter WAV + SRT subtitle files using VieNeu TTS.

## Basic usage

```bash
python examples/audiobook.py book.txt --voice "Bình An" --out output/audiobook/
```

Each chapter is saved as `01_mo_dau.wav` + `01_mo_dau.srt`, `02_chuong_mot.wav` + `02_chuong_mot.srt`, … in the output directory.  
The chapter title (e.g. "Chương Một") is spoken aloud before the chapter body.  
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

## Chapter detection

The script automatically splits the text on lines that match common heading patterns:

| Pattern | Examples |
|---|---|
| Vietnamese section words | `Chương`, `Phần`, `Mở đầu`, `Lời mở đầu`, `Vĩ thanh`, `Kết`, `Lời tựa`, `Lời kết` |
| English section words | `Chapter`, `Epilogue`, `Prologue` |
| Roman numerals | `I.`, `IV.`, `XII.` |
| Plain numbers | `1.`, `12.` |

Matching is case-insensitive. Any text before the first heading is automatically captured as a prologue. Sections with no body are silently skipped.

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

### Verifying the SRT

Inspect the file directly:

```bash
cat output/audiobook/01_mo_dau.srt | head -20
```

Check that the SRT duration matches the audio duration:

```bash
python3 -c "
total = 0
with open('output/audiobook/01_mo_dau.srt') as f:
    for line in f:
        if '-->' in line:
            end = line.split('-->')[1].strip()
            h,m,s = end.replace(',','.').split(':')
            total = float(h)*3600 + float(m)*60 + float(s)
print(f'SRT duration: {total:.1f}s')
"
```

Play with subtitles using ffplay:

```bash
ffplay -i output/audiobook/01_mo_dau.wav -vf "subtitles=output/audiobook/01_mo_dau.srt"
```

## Copying to Mac and playing

Copy a chapter from the server to your Mac (run on Mac terminal):

```bash
scp user@your-server:~/workspace/tts/VieNeu-TTS/output/audiobook/01_mo_dau.* ~/Downloads/
```

Copy the entire output folder:

```bash
scp -r user@your-server:~/workspace/tts/VieNeu-TTS/output/audiobook/ ~/Downloads/audiobook/
```

**Playing on Mac:**
- **IINA** (recommended, free) — drag the `.wav` onto IINA; it auto-detects the `.srt` if both files share the same name in the same folder. Download at [iina.io](https://iina.io)
- **VLC** — drag the `.wav` in, then `Subtitles → Add Subtitle File` to load the `.srt`

## Accent consistency

If the voice switches accent between sentences, two fixes help:

1. **Lower temperature** — edit `temperature=0.5` in `examples/audiobook.py` for more deterministic output
2. **Use `--ref-audio`** — a reference clip anchors the accent for every chunk independently, which is the stronger fix. Extract a clip from an existing output that sounded correct:

```bash
ffmpeg -i output/audiobook/01_mo_dau.wav -ss 10 -t 5 ref_binh_an.wav
```

Then rerun with `--ref-audio ref_binh_an.wav`.
