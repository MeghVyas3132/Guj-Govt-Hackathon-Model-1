import argparse
import sys
import os
import cv2
import logging

from ml.stream import CameraStream
from ml.auth import get_cookie
from ml.pipeline import DetectionPipeline
from ml.models import warm_all_models

logging.basicConfig(level=logging.INFO, format="%(message)s")

def main():
    parser = argparse.ArgumentParser(description="Live view of camera feed with YOLO & Plate OCR")
    parser.add_argument("camera_id", nargs="?", default="cam06", help="Camera ID to view (default: cam06)")
    parser.add_argument("--start", metavar="HH:MM", default=None,
                        help="Start position in the 12-hour archive, e.g. '09:30'")
    parser.add_argument("--end", metavar="HH:MM", default=None,
                        help="End position in the archive, e.g. '11:00'. Stops when reached.")
    args = parser.parse_args()

    warm_all_models()
    pipeline = DetectionPipeline()
    cookie = get_cookie()

    with CameraStream(args.camera_id, cookie) as stream:
        for f in stream.frames(start_time=args.start, end_time=args.end):
            frame = f.image
            # read_plates=True is the point of this tool: it shows the raw
            # per-frame OCR output the worker deliberately no longer trusts on
            # its own, which is what you want when eyeballing model behaviour.
            detections = pipeline.process_frame(
                frame, args.camera_id, f.pts_ms, f.archive_ms, read_plates=True
            )
            
            for det in detections:
                x1, y1, x2, y2 = det.bbox["x1"], det.bbox["y1"], det.bbox["x2"], det.bbox["y2"]
                
                # Draw bounding box
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                
                # Prepare label
                label = f"{det.class_name} {int(det.confidence * 100)}%"
                plate_str = ""
                if det.plate_text:
                    label += f" | {det.plate_text} ({int(det.plate_confidence * 100)}%)"
                    plate_str = f" | plate: {det.plate_text} ({int(det.plate_confidence * 100)}%)"
                
                # Print clean log line
                print(f"[{args.camera_id}] {det.class_name} {int(det.confidence * 100)}%{plate_str}")
                
                # Draw label background and text
                (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(frame, (x1, y1 - 20), (x1 + w, y1), (0, 255, 0), -1)
                cv2.putText(frame, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

            cv2.imshow("Live Viewer", frame)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
