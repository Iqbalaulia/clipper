import os
import subprocess

def create_broll():
    broll_dir = "broll"
    os.makedirs(broll_dir, exist_ok=True)

    brolls = {
        "money.mp4": "UANG (B-ROLL)",
        "time.mp4": "WAKTU (B-ROLL)",
        "fire.mp4": "API (B-ROLL)",
    }

    for filename, text in brolls.items():
        filepath = os.path.join(broll_dir, filename)
        if not os.path.exists(filepath):
            print(f"Generating dummy B-Roll {filename}...")
            # Generate 3 seconds of 1080x1920 video with a colored background and text
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=3",
                "-vf", f"drawtext=text='{text}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y=(h-text_h)/2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                filepath
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Saved {filepath}")
            except Exception as e:
                print(f"Failed to generate {filename}: {e}")
        else:
            print(f"{filename} already exists.")

if __name__ == "__main__":
    create_broll()
