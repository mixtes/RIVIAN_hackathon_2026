# Phone detector fine-tuning: step by step

Goal: teach the phone detector what phones look like in this cabin camera (including dark footage),
using our own labeled frames. Everything below is run from the `CVEngine` folder.

`phone_dataset/` and the training bundle contain images of real people. Keep them local, and copy
them only to machines you are allowed to use for this data. Git ignores both.

## 0. One-time setup on this machine

The tools need OpenCV and Ultralytics. They are installed in `CVEngine\venv` (git-ignored).
Use that Python, not the system `python` (which gives `No module named 'cv2'`).

```powershell
cd Q:\src\RIVIAN_hackathon_2026\RivianHackatonEngine\CVEngine
.\venv\Scripts\Activate.ps1          # then "python" means the venv Python
```

If PowerShell blocks `Activate.ps1`, run the venv Python directly instead,
e.g. `.\venv\Scripts\python.exe label_review.py`.

If `venv` does not exist yet (exact versions tested on Windows / Python 3.13):

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-tools.txt
```

## 1. Label the frames (here, needs a screen)

```powershell
python label_review.py --filter unreviewed     # only frames not confirmed yet
```

1,400 frames are prepared (700 from the first 56 videos, 700 from the 60 added later), each with draft
boxes from a larger model. Drafts are often wrong; only frames you confirm are used for training and
testing. Expect about 1-2 hours per 700 frames.

Adding more videos later: put them in `videos\Hackathon dataset 2026`, then
`python phone_data.py contact-sheet` (new clips show NEW), give each new clip a driver id in
`phone_drivers.json` (same person = same id), `python phone_data.py prelabel`, and
`python phone_data.py build --only-new-clips --test-drivers D03 D04 D07 D13 --val-drivers D05 D08 D09 D10 D16`.

| Action | How |
|---|---|
| Draw a box | drag with the mouse |
| Select a box | click it |
| Delete a box | **right-click it**, or point at it and press `d` / `Delete` / `Backspace` |
| No phone in this frame | `c` (clear all boxes), then confirm |
| Confirm frame and go to next | `Space` or `Enter` |
| Skip without saving / go back | `s` / `b` |
| Next unconfirmed frame | `u` |
| Brighten view (display only) | `l` (dark frames are brightened automatically) |
| Window bigger / smaller | `+` / `-` (it fits your screen automatically; `--zoom 0.8` starts smaller) |
| Quit (confirmed frames are already saved) | `q` or `Esc` |

Labeling rules:

- Box **every phone the driver or passenger is holding or touching**, and phones lying anywhere in
  the image. Box only the phone itself, also when fingers cover part of it.
- **No phone visible → confirm with no boxes.** These frames are just as important: they teach the
  model that sunglasses, hands at the face, wallets, etc. are not phones.
- Not sure if it is a phone (too dark, blurred)? Skip it with `s` rather than guessing.
- You can stop any time and continue later; the tool starts at the first unconfirmed frame.
- Only one person should label a given copy of `phone_dataset/` at a time.

Optional filters: `python label_review.py --filter unreviewed|phone|dark|negatives`.

Made mistakes? Go back with `b`, fix the boxes, and confirm again. To undo confirmations entirely
(back to draft boxes, not reviewed):

```powershell
python phone_data.py reset --images 05_23_62_005853_0005 05_23_62_005853_0015
python phone_data.py reset --all-reviewed       # start over completely
```

Check progress:

```powershell
python phone_data.py status
```

Train, val and test should all have reviewed frames, including some with phones.

## 2. Pack and copy to the GPU machine

```powershell
python phone_data.py pack
scp phone_train_bundle.tar.gz user@gpu-host:~/
```

The bundle (about 200 MB) contains only confirmed frames, their labels, `phone_data.py`,
`live_phone.py`, the base model `yolo11m.pt`, and `requirements-gpu.txt`.
Replace `user@gpu-host` with your SSH login.

## 3. Train on the GPU machine (Linux)

```bash
ssh user@gpu-host
tar -xzf phone_train_bundle.tar.gz && cd phone_ft
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-gpu.txt
nvidia-smi
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"   # must print True

tmux new -s phone                     # keeps training running if SSH disconnects
python phone_data.py darken           # synthetic dark copies of training frames
python phone_data.py train --batch 32 --workers 8
python phone_data.py eval --model phone_dataset/runs/phone_ft/weights/best.pt | tee phone_dataset/runs/phone_ft/eval.txt
```

- Leave tmux with `Ctrl+B` then `D`; return with `tmux attach -t phone`.
- Expected time: about 10-45 minutes of training depending on the GPU, plus setup.
- Training stops early on its own if validation stops improving.

## 4. Bring the results back

On this machine, in `CVEngine`:

```powershell
New-Item -ItemType Directory -Force phone_dataset\runs | Out-Null
scp -r user@gpu-host:~/phone_ft/phone_dataset/runs/phone_ft phone_dataset/runs/
Get-Content phone_dataset\runs\phone_ft\eval.txt
```

You get `weights/best.pt` (the model), `eval.txt`, and training curves (`results.csv`, plots).

`eval.txt` compares the current model (`yolo11m.pt`) with the fine-tuned one on the test drivers
(never seen in training): phones found, frames with false alarms, dark phones found, and false alarms
on hands at the face. Use the new model only if it is better, especially on false alarms.

## 5. Try it live

```powershell
python live_phone.py "videos\Hackathon dataset 2026\05_23_62_012138_video_inCabin.mp4" --phone-model phone_dataset\runs\phone_ft\weights\best.pt --show
```

That clip is the dark one where the current model missed the phone.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'cv2'` here | Use the venv (step 0). |
| `Activate.ps1 cannot be loaded` | Use `.\venv\Scripts\python.exe ...` instead. |
| Remote: `torch.cuda.is_available()` is False | NVIDIA driver too old for the default PyTorch build: install the PyTorch build matching `nvidia-smi` (pytorch.org). |
| Remote: `ImportError: libGL.so.1` | `pip uninstall -y opencv-python && pip install opencv-python-headless` |
| Remote: CUDA out of memory | `--batch 16` (or 8). |
| Remote has no internet | `pip install` needs internet once; training itself works offline. |
| `Not enough reviewed frames` | Review more (needs at least 100 train and 10 val frames). |
