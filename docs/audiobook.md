# Audiobook Generator

Converts a `.txt` file into per-chapter WAV files using VieNeu TTS.

## Basic usage

```bash
python examples/audiobook.py book.txt --voice "Bình An" --out output/audiobook/
```

Each chapter is saved as `01_mo_dau.wav`, `02_chuong_mot.wav`, … in the output directory.  
The chapter title (e.g. "Chương Một") is spoken aloud before the chapter body.  
Rerunning automatically skips chapters whose output file already exists.

## Options

| Flag | Default | Description |
|---|---|---|
| `--voice` | Ngọc Lan | Preset voice name. See available voices below. |
| `--ref-audio` | — | Path to a 3–5s WAV clip for instant voice cloning. |
| `--out` | `outputs/audiobook` | Output directory (created if missing). |
| `--silence` | `0.3` | Seconds of silence between sentences within a chapter. |
| `--title-gap` | `0.8` | Seconds of silence between the title announcement and the chapter body. |
| `--merge` | off | Also produce a single `full_book.wav` combining all chapters. |
| `--chapter-gap` | `1.5` | Seconds of silence between chapters in `full_book.wav` (only with `--merge`). |

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

# Voice cloning from a reference clip
python examples/audiobook.py book.txt --ref-audio my_voice.wav --out output/audiobook/

# Slower pacing: more silence between sentences and a longer pause after title
python examples/audiobook.py book.txt --voice "Trọng Hữu" --silence 0.5 --title-gap 1.2 --out output/audiobook/
```

## Running in the background

To keep the process running after closing your SSH session:

```bash
nohup python -u examples/audiobook.py book.txt --voice "Bình An" --out output/audiobook/ \
    > output/audiobook/log.txt 2>&1 &
```

Monitor progress:

```bash
tail -f output/audiobook/log.txt
```

Check if still running:

```bash
ps aux | grep audiobook.py
```
