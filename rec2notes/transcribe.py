"""Audio to a timestamped transcript, with ffmpeg and whisper-cli, cached by audio content."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

from . import Abort, paths, stopping

PROGRESS = re.compile(r"progress =\s*(\d+)%")  # whisper-cli -pp, every 5%
DLL_NOT_FOUND = 0xC0000135  # Windows' exit status for a program that can't start: whisper-cli without the VC++ runtime
DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")  # ffmpeg's line about its input
WORDS_PER_MINUTE = 110  # a 90-minute lecture's transcript, timestamps included; to settle from more lectures
LONG_TRANSCRIPT_WORDS = 30_000  # about four and a half hours of lecture; past this a merge is slow and eats a lot of usage


def sha256_file(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def whisper_cli() -> str | None:
    """Our own build in the rec2notes folder, else a whisper-cli on PATH."""
    built = paths.whisper_cli_built()
    if os.access(built, os.X_OK):
        return str(built)
    return shutil.which("whisper-cli")


def audio_seconds(audio: Path) -> float | None:
    """The recording's length, read by ffmpeg from the file's header without converting it; None if it can't tell."""
    try:
        result = subprocess.run([paths.program("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(audio)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not (match := DURATION.search(result.stderr)):
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def format_segments(segments: list[tuple[int, str]]) -> str:
    """[(start_ms, text)] to one `[hh:mm:ss] text` line per non-empty segment."""
    lines = []
    for start, text in segments:
        text = " ".join(text.split())
        if text:
            lines.append(f"[{format_timestamp(start)}] {text}\n")
    return "".join(lines)


def parse_whisper_json(raw: bytes) -> list[tuple[int, str]]:
    # whisper-cli escapes only quotes and backslashes, and a segment can end in the
    # middle of a UTF-8 sequence: decode leniently and allow control characters.
    data = json.loads(raw.decode("utf-8", errors="replace"), strict=False)
    return [(seg["offsets"]["from"], seg["text"]) for seg in data.get("transcription", [])]


def label_parts(transcripts: list[str]) -> str:
    """One part keeps `[hh:mm:ss]`; several become `[pN hh:mm:ss]`, in order."""
    if len(transcripts) == 1:
        return transcripts[0]
    return "".join(re.sub(r"^\[", f"[p{n} ", text, flags=re.M) for n, text in enumerate(transcripts, 1))


def transcribe(audio: Path, cache: Path, model: str, vocab: str, run_dir: Path, part: int,
               on_progress: Callable[[int], None] | None = None) -> float | None:
    """Transcribe one audio file, store the formatted transcript in `cache`, and return
    the audio's length in seconds (None if unknown).

    `on_progress` gets 0 when whisper starts, then whisper's percentage as it goes; all of
    ffmpeg's and whisper's output goes to whisper.log.
    """
    wav, out = run_dir / f"p{part}.wav", run_dir / f"p{part}"
    log_path = run_dir / "whisper.log"
    with open(log_path, "a", encoding="utf-8", buffering=1) as log:
        log.write(f"==> part {part}: {audio}\n")
        try:
            _run([paths.program("ffmpeg"), "-y", "-loglevel", "error", "-i", str(audio),
                  "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                 log, f"ffmpeg could not convert {audio}")
            seconds = _wav_seconds(wav)
            if on_progress:
                on_progress(0)
            # On Windows whisper-cli reads its command line in the ANSI code page, but a response file as UTF-8
            # bytes, one argument per line. It opens the models by UTF-8 path, the audio and output only by
            # ANSI name: those two are plain ASCII names in the run directory, its working directory.
            args = ["-m", str(paths.whisper_model(model)), "-f", wav.name, "-l", "it", "--max-context", "0",
                    "--vad", "--vad-model", str(paths.vad_model()),
                    "--prompt", " ".join(vocab.split()), "-oj", "-of", out.name, "-pp"]
            (run_dir / "whisper-args.txt").write_text("\n".join(args) + "\n", encoding="utf-8", newline="\n")
            _run([whisper_cli(), "@whisper-args.txt"], log, f"whisper-cli failed on {audio}", on_progress, cwd=run_dir)
        finally:
            wav.unlink(missing_ok=True)  # a lecture is hundreds of MB as WAV, even when stopped halfway
    try:
        text = format_segments(parse_whisper_json(out.with_name(out.name + ".json").read_bytes()))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Abort(f"could not read whisper-cli's output for {audio}: {e}") from None
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, cache)
    return seconds


def _wav_seconds(wav: Path) -> float | None:
    try:
        with wave.open(str(wav)) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def _run(cmd: list[str], log, failure: str, on_progress: Callable[[int], None] | None = None,
         cwd: Path | None = None) -> None:
    """Run `cmd` with its output copied line by line to `log`, reporting any progress lines.
    A stop signal ends the child, which ends the output, and raises Stopped afterwards."""
    stopping.check()
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                          text=True, encoding="utf-8", errors="replace") as proc, stopping.child(proc):
        try:
            for line in proc.stdout:
                log.write(line)
                if on_progress and (match := PROGRESS.search(line)):
                    on_progress(int(match.group(1)))
        except BaseException:
            proc.kill()  # otherwise leaving the block would wait for the child to finish
            raise
    stopping.check()
    code = proc.returncode
    if paths.WINDOWS and code == DLL_NOT_FOUND:
        raise Abort(f"{failure}: a DLL it needs is missing. Install the Visual C++ runtime with "
                    "`winget install Microsoft.VCRedist.2015+.x64`, then run rec2notes again")
    if code:
        tail = Path(log.name).read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
        raise Abort(f"{failure} (exit status {code}). Last lines of {log.name}:\n  " + "\n  ".join(tail))
