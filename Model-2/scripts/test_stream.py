"""
Direct RTSP stream test — no AI processing, no HLS.
Use this to demonstrate the UDP vs TCP grey-smear difference,
or to test RTSP auth is working.
"""
import cv2
import os
from ml.config import settings

cam_id = "cam06"
rtsp_url = settings.rtsp_url(cam_id)  # includes email:password auth

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

print(f"Opening stream: rtsp://<email>:<password>@.../{cam_id}  (auth embedded)")
cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

if not cap.isOpened():
    print("Failed to open stream!")
    exit(1)

print("Stream opened successfully. Press 'q' to quit.")
while True:
    ret, frame = cap.read()
    if not ret or frame is None:
        print("Failed to read frame.")
        break

    cv2.imshow("Direct Stream Test", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
