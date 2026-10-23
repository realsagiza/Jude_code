"""Local-only film assembly after all generated assets are downloaded."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from judecode.film.errors import FilmError


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise FilmError("Install FFmpeg or imageio-ffmpeg locally to assemble the downloaded clips") from None


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            return stream.getnframes() / rate if rate else 0
    except (OSError, wave.Error):
        raise FilmError("A downloaded narration/sound WAV is invalid; the Pod was already scheduled for cleanup") from None


def _run(binary: str, args: list[str], cwd: Path, timeout: int = 600) -> None:
    try:
        result = subprocess.run(
            [binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise FilmError("Local FFmpeg assembly timed out after 10 minutes") from None
    if result.returncode:
        # Never return raw FFmpeg output; paths/prompts may be user-specific.
        raise FilmError("Local FFmpeg could not assemble the downloaded media; the original assets are preserved")


def _has_audio(binary: str, path: Path) -> bool:
    try:
        result = subprocess.run([binary, "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise FilmError("Could not inspect a downloaded clip's audio stream") from None
    return b"Audio:" in result.stderr


def assemble(scenes: list[dict], aspect_ratio: str, output: str | Path) -> dict:
    """Mix per-scene voice/sound locally and concatenate into one H.264 MP4."""
    dimensions = {"16:9": (854, 480), "9:16": (480, 854), "1:1": (480, 480)}
    if aspect_ratio not in dimensions or not 1 <= len(scenes) <= 30:
        raise FilmError("Film assembly needs 1–30 scenes and a supported aspect ratio")
    width, height = dimensions[aspect_ratio]
    prepared = []
    warnings = []
    total_duration = 0.0
    for index, scene in enumerate(scenes, start=1):
        video = Path(scene.get("video", "")).expanduser().resolve()
        if not video.is_file() or video.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
            raise FilmError(f"Scene {index} is missing a downloaded video clip")
        planned = scene.get("duration")
        if type(planned) is not int or not 1 <= planned <= 120:
            raise FilmError(f"Scene {index} has an invalid planned duration")
        narration = Path(scene["narration"]).expanduser().resolve() if scene.get("narration") else None
        sound = Path(scene["sound"]).expanduser().resolve() if scene.get("sound") else None
        for audio in (narration, sound):
            if audio and (not audio.is_file() or audio.suffix.lower() != ".wav"):
                raise FilmError(f"Scene {index} has a missing or invalid downloaded WAV file")
        duration = max(float(planned), _wav_duration(narration) if narration else 0.0,
                       _wav_duration(sound) if sound else 0.0)
        if duration - planned > 0.5:
            warnings.append({"scene": index, "code": "audio_overrun", "extended_seconds": round(duration - planned, 3)})
        total_duration += duration
        prepared.append((video, narration, sound, duration))

    binary = _ffmpeg()
    final = Path(output).expanduser().resolve()
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary_final = final.with_name(final.stem + ".partial.mp4")
    if temporary_final.exists():
        temporary_final.unlink()

    try:
        with tempfile.TemporaryDirectory(prefix="jude-film-assembly-") as temp_name:
            temp = Path(temp_name)
            segments = []
            for index, (video, narration, sound, duration) in enumerate(prepared):
                segment = f"segment-{index:03d}.mp4"
                segments.append(segment)
                has_clip_audio = _has_audio(binary, video)
                inputs = ["-i", str(video)]
                audio_labels = []
                input_index = 1
                if has_clip_audio:
                    audio_labels.append(f"[0:a:0]volume=0.45[clip{index}]")
                    audio_inputs = [f"[clip{index}]"]
                else:
                    audio_inputs = []
                if narration:
                    inputs += ["-i", str(narration)]
                    audio_labels.append(f"[{input_index}:a:0]volume=1.0[voice{index}]")
                    audio_inputs.append(f"[voice{index}]")
                    input_index += 1
                if sound:
                    inputs += ["-i", str(sound)]
                    audio_labels.append(f"[{input_index}:a:0]volume=0.35[fx{index}]")
                    audio_inputs.append(f"[fx{index}]")
                    input_index += 1
                if not audio_inputs:
                    inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                    audio_inputs.append(f"[{input_index}:a:0]")
                video_filter = (
                    f"[0:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                    f"tpad=stop_mode=clone:stop_duration={duration:.3f},format=yuv420p[v]"
                )
                prefix = ";".join(audio_labels)
                if len(audio_inputs) == 1:
                    mix_expr = f"{audio_inputs[0]}apad,atrim=duration={duration:.3f},aresample=48000[a]"
                else:
                    joined = "".join(audio_inputs)
                    mix_expr = f"{joined}amix=inputs={len(audio_inputs)}:duration=longest:normalize=0,apad,atrim=duration={duration:.3f},aresample=48000[a]"
                mix_filter = ";".join(part for part in (prefix, mix_expr) if part)
                filter_complex = video_filter + ";" + mix_filter
                args = [*inputs, "-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
                        "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", segment]
                _run(binary, args, temp)

            playlist = temp / "scenes.txt"
            playlist.write_text("".join(f"file '{name}'\n" for name in segments), encoding="utf-8")
            _run(binary, ["-f", "concat", "-safe", "1", "-i", "scenes.txt", "-c:v", "libx264",
                          "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
                          "-b:a", "192k", "-movflags", "+faststart", str(temporary_final)], temp)
    except BaseException:
        temporary_final.unlink(missing_ok=True)
        raise

    if not temporary_final.is_file() or temporary_final.stat().st_size <= 0:
        temporary_final.unlink(missing_ok=True)
        raise FilmError("FFmpeg returned without a usable final MP4; downloaded assets are preserved")
    temporary_final.replace(final)
    return {
        "files": [str(final)],
        "scenes": len(prepared),
        "duration_seconds": round(total_duration, 3),
        "resolution": f"{width}x{height}",
        "quality_warnings": warnings,
        "quality_status": "unreviewed",
    }
