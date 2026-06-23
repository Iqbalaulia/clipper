import os
import urllib.request

def download_bgm():
    bgm_dir = "bgm"
    os.makedirs(bgm_dir, exist_ok=True)
    
    tracks = {
        "lofi.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "phonk.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "cinematic.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
    }
    
    for filename, url in tracks.items():
        filepath = os.path.join(bgm_dir, filename)
        if not os.path.exists(filepath):
            print(f"Downloading {filename}...")
            try:
                urllib.request.urlretrieve(url, filepath)
                print(f"Saved to {filepath}")
            except Exception as e:
                print(f"Failed to download {filename}: {e}")
        else:
            print(f"{filename} already exists.")

if __name__ == "__main__":
    download_bgm()
