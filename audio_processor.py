import os
import subprocess
import json
from PIL import Image
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
from telethon.tl.types import DocumentAttributeAudio

# ==========================================
# 1. STRICT FFMPEG RE-ENCODER (CBR 128K)
# ==========================================
def convert_to_pure_mp3(input_path: str, output_path: str) -> bool:
    """
    Re-encodes any source (m4a, aac, corrupted mp3) into standard
    128k CBR 44.1kHz stereo MP3 with mandatory Xing seek headers.
    """
    # -vn: strips dirty cover art/video tracks from container
    # -write_xing 1: generates seek headers required by Telegram player
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-write_xing", "1",
        "-id3v2_version", "3",
        output_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 500 * 1024
    except subprocess.CalledProcessError as e:
        print(f"[FFmpeg Error]: {e.stderr.decode('utf-8', errors='ignore')}")
        return False

# ==========================================
# 2. FFPROBE INTEGRITY & DURATION AUDIT
# ==========================================
def verify_mp3_integrity(file_path: str) -> tuple[bool, int]:
    """
    Verifies that the file contains playable MPEG audio frames
    and extracts the exact duration in seconds.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_name",
        "-of", "json",
        file_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(proc.stdout)
        
        duration = float(data.get("format", {}).get("duration", 0))
        streams = data.get("streams", [])
        codec = streams[0].get("codec_name", "") if streams else ""
        
        # Must be mp3 codec, over 60 seconds, and over 500 KB
        if codec == "mp3" and duration > 60:
            return True, int(duration)
        return False, 0
    except Exception as e:
        print(f"[FFprobe Integrity Check Failed]: {e}")
        return False, 0

# ==========================================
# 3. ID3v2.3 METADATA & COVER NORMALIZER
# ==========================================
def apply_id3_tags(mp3_path: str, title: str, story_name: str, cover_path: str):
    """
    Applies pure ID3v2.3 tags (TIT2, TPE1, TALB) and embeds a normalized baseline JPEG.
    ID3v2.3 is universally compatible with Telegram desktop, mobile, and web.
    """
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delete()  # Strip legacy or foreign metadata completely

    # Set Track Title: "Ep X - [Official Title]"
    tags.add(TIT2(encoding=3, text=title))
    # Set Performer / Artist: "{Story Name}" (Clean, no extra strings)
    tags.add(TPE1(encoding=3, text=story_name))
    # Set Album: "{Story Name}"
    tags.add(TALB(encoding=3, text=story_name))

    # Normalize Cover Art to standard 800x800 Baseline JPEG
    if cover_path and os.path.exists(cover_path):
        normalized_cover = f"{mp3_path}_norm_cover.jpg"
        try:
            with Image.open(cover_path) as img:
                img = img.convert("RGB")
                img.thumbnail((800, 800))
                img.save(normalized_cover, "JPEG", quality=90)

            with open(normalized_cover, "rb") as f:
                tags.add(APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,  # Front Cover
                    desc="Cover",
                    data=f.read()
                ))
        except Exception as cov_err:
            print(f"[Cover Normalization Notice]: {cov_err}")
        finally:
            if os.path.exists(normalized_cover):
                try:
                    os.remove(normalized_cover)
                except Exception:
                    pass

    tags.save(mp3_path, v2_version=3)

# ==========================================
# 4. TELETHON SAFE STREAMING UPLOADER
# ==========================================
async def upload_audio_to_vault(client, target_channel_id: int, file_path: str, title: str, story_name: str, duration: int, thumb_path: str = None):
    """
    Streams the verified MP3 to the Vault channel with explicit
    DocumentAttributeAudio attributes enabling native Telegram in-app streaming.
    """
    attributes = [
        DocumentAttributeAudio(
            duration=duration,
            voice=False,
            title=title,
            performer=story_name
        )
    ]
    
    await client.send_file(
        entity=target_channel_id,
        file=file_path,
        thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
        caption="",
        attributes=attributes,
        mime_type="audio/mpeg",
        supports_streaming=True
    )
