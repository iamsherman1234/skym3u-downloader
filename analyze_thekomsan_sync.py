#!/usr/bin/env python3
import wave
import subprocess
import numpy as np
from pathlib import Path

def extract_wav(input_file: str, output_wav: str, duration: int = 300):
    cmd = [
        "ffmpeg", "-y",
        "-i", input_file,
        "-t", str(duration),
        "-vn",
        "-ac", "1",
        "-ar", "8000",  # 8 kHz is plenty for speech/music envelope correlation
        output_wav
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def read_wav(filename: str):
    with wave.open(filename, 'rb') as wf:
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        return framerate, data

def compute_sync(net_wav: str, kom_wav: str):
    sr1, y1 = read_wav(net_wav)
    sr2, y2 = read_wav(kom_wav)
    assert sr1 == sr2, f"Sample rates do not match: {sr1} vs {sr2}"
    
    # Normalize
    y1 /= (np.max(np.abs(y1)) + 1e-8)
    y2 /= (np.max(np.abs(y2)) + 1e-8)
    
    # Calculate energy envelope with moving window (e.g. 20ms)
    win_size = int(sr1 * 0.02)  # 20ms window
    env1 = np.convolve(np.abs(y1), np.ones(win_size)/win_size, mode='same')
    env2 = np.convolve(np.abs(y2), np.ones(win_size)/win_size, mode='same')
    
    # Decimate envelope by factor of 8 (sample rate ~ 1000 Hz => 1ms precision)
    decim = 8
    env1_d = env1[::decim]
    env2_d = env2[::decim]
    sr_env = sr1 / decim  # 1000 Hz
    
    # Remove mean
    env1_d -= np.mean(env1_d)
    env2_d -= np.mean(env2_d)
    
    # Cross correlation via FFT
    n = len(env1_d) + len(env2_d) - 1
    N = 1 << (n - 1).bit_length()
    f1 = np.fft.rfft(env1_d, N)
    f2 = np.fft.rfft(env2_d, N)
    corr = np.fft.irfft(f1 * np.conj(f2), N)[:n]
    
    lags = np.arange(-len(env2_d) + 1, len(env1_d))
    
    # Restrict lag search to reasonable window: [-30s, +30s]
    max_lag_samples = int(30 * sr_env)
    valid_mask = (lags >= -max_lag_samples) & (lags <= max_lag_samples)
    
    valid_corr = corr[valid_mask]
    valid_lags = lags[valid_mask]
    
    best_idx = np.argmax(valid_corr)
    best_lag = valid_lags[best_idx]
    offset_sec = best_lag / sr_env
    
    print("\n" + "="*60)
    print("📊 AUDIO SYNC ANALYSIS REPORT (TheKomsan vs Netflix 1080p)")
    print("="*60)
    print(f"Max correlation value: {valid_corr[best_idx]:.2f}")
    print(f"Computed Time Offset: {offset_sec:.4f} seconds ({offset_sec*1000:.1f} ms)")
    
    if offset_sec > 0:
        print(f"-> TheKomsan audio is LEADING (starts {offset_sec:.4f}s earlier than Netflix video).")
        print(f"-> To sync: trim Khmer audio with '-ss {offset_sec:.4f}'")
    elif offset_sec < 0:
        print(f"-> TheKomsan audio is LAGGING (starts {abs(offset_sec):.4f}s later than Netflix video).")
        print(f"-> To sync: delay Khmer audio with '-itsoffset {abs(offset_sec):.4f}'")
    else:
        print("-> Audio is PERFECTLY aligned (0.000s offset).")
    print("="*60 + "\n")
    return offset_sec

if __name__ == "__main__":
    net_path = "/root/samkok_1080p_work/raw_1080p_e01.mkv"
    komsan_path = "/root/samkok_1080p_work/thekomsan_e01.mp4"
    
    wav_net = "/root/samkok_1080p_work/test_net.wav"
    wav_kom = "/root/samkok_1080p_work/test_kom.wav"
    
    print("[*] Extracting 5-minute WAV from Netflix 1080p...")
    extract_wav(net_path, wav_net, 300)
    print("[*] Extracting 5-minute WAV from TheKomsan...")
    extract_wav(komsan_path, wav_kom, 300)
    
    compute_sync(wav_net, wav_kom)
