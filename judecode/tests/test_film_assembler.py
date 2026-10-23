import subprocess
from pathlib import Path

import pytest

from judecode.film import assembler
from judecode.film.errors import FilmError


def test_local_assembler_mixes_wav_and_builds_real_mp4(tmp_path):
    try:
        binary = assembler._ffmpeg()
    except FilmError:
        pytest.skip("FFmpeg is not installed")
    clip = tmp_path / "clip.mp4"
    voice = tmp_path / "voice.wav"
    sound = tmp_path / "sound.wav"
    assembler._run(binary, ["-f", "lavfi", "-i", "color=red:s=160x120:d=1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(clip)], tmp_path)
    assembler._run(binary, ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:a", "pcm_s16le",
                            str(voice)], tmp_path)
    assembler._run(binary, ["-f", "lavfi", "-i", "sine=frequency=220:duration=0.4", "-c:a", "pcm_s16le",
                            str(sound)], tmp_path)

    output = tmp_path / "final.mp4"
    result = assembler.assemble([{"video": str(clip), "duration": 1,
                                  "narration": str(voice), "sound": str(sound)}], "16:9", output)
    assert output.is_file() and output.stat().st_size > 1000
    assert result["resolution"] == "854x480"
    assert result["quality_status"] == "unreviewed"
    decoded = subprocess.run([binary, "-i", str(output), "-f", "null", "-"], capture_output=True)
    assert decoded.returncode == 0
    assert b"854x480" in decoded.stderr and b"Audio: aac" in decoded.stderr
    assert Path(result["files"][0]) == output


def test_local_assembler_rejects_missing_inputs(tmp_path):
    with pytest.raises(FilmError, match="missing a downloaded video"):
        assembler.assemble([{"video": str(tmp_path / "missing.mp4"), "duration": 1}], "16:9", tmp_path / "x.mp4")
