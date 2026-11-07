"""Export the YOLO models to OpenVINO for faster CPU inference (Intel CPUs; ~1.6-1.8x on the live pipeline).
Usage: python export_openvino.py            (needs: pip install openvino)
Then:  python live.py SOURCE --pose-model yolo11s-pose_openvino_model/ --phone-model yolo11m_dyn_openvino_model/
The phone model is exported with dynamic input shapes: a fixed 640x640 input pads the (non-square) driver crop
differently from PyTorch and found fewer phones (14 vs 19 of 36 frames in 084339); dynamic matches PyTorch.
"""
import shutil
from ultralytics import YOLO

EXPORTS = [('yolo11s-pose.pt', False, 'yolo11s-pose_openvino_model'),
           ('yolo11m-pose.pt', False, 'yolo11m-pose_openvino_model'),
           ('yolo11m.pt', True, 'yolo11m_dyn_openvino_model')]

if __name__ == '__main__':
    for weights, dynamic, folder in EXPORTS:
        path = YOLO(weights).export(format='openvino', imgsz=640, dynamic=dynamic, half=False)
        if str(path).rstrip('/\\') != folder:
            shutil.rmtree(folder, ignore_errors=True)
            shutil.move(path, folder)
        print('exported', weights, '->', folder)
