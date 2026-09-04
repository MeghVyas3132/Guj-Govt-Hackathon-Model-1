import cv2
import requests
import time
import os
from Crypto.Cipher import AES

from ml.auth import get_cookie
from ml.config import settings

def test_hls():
    cam_id = "cam06"
    cookie = get_cookie()
    session = requests.Session()
    session.cookies.set("sentinel", cookie)
    
    base_url = settings.sentinel_base_url
    m3u8_url = f"{base_url}/{cam_id}/index.m3u8"
    
    print(f"Fetching playlist: {m3u8_url}")
    r = session.get(m3u8_url)
    if r.status_code != 200:
        print("Failed to get m3u8:", r.status_code)
        return
        
    lines = r.text.strip().split('\n')
    ts_file = None
    key_url = None
    
    for line in lines:
        if line.startswith("#EXT-X-KEY"):
            # e.g. #EXT-X-KEY:METHOD=AES-128,URI="/enc.key",IV=0x00...
            parts = line.split(',')
            for p in parts:
                if p.startswith('URI='):
                    key_url = p.split('"')[1]
        elif not line.startswith("#") and line.endswith(".ts"):
            ts_file = line
            
    if not ts_file or not key_url:
        print("Could not find key or TS file in playlist.")
        return
        
    if key_url.startswith('/'):
        key_url = base_url + key_url
    ts_url = f"{base_url}/{cam_id}/{ts_file}"
    
    print("Downloading AES key...")
    key_bytes = session.get(key_url).content
    
    print(f"Downloading encrypted chunk: {ts_url}")
    enc_data = session.get(ts_url).content
    
    print("Decrypting chunk...")
    # HLS AES-128 uses CBC mode. The IV is usually specified, or defaults to the sequence number.
    # In the playlist we saw IV=0x00000000000000000000000000000000
    iv = bytes(16) 
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    dec_data = cipher.decrypt(enc_data)
    
    temp_file = "temp_chunk.ts"
    with open(temp_file, "wb") as f:
        f.write(dec_data)
        
    print("Decoding decrypted chunk with OpenCV...")
    cap = cv2.VideoCapture(temp_file)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imshow("Decrypted HLS Stream Test", frame)
        if cv2.waitKey(30) & 0xFF == ord('q'):
            break
            
    cap.release()
    cv2.destroyAllWindows()
    os.remove(temp_file)
    print("Success!")

if __name__ == "__main__":
    test_hls()
