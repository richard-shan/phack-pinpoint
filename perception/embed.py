"""Image descriptors for retrieval: MegaLoc by default, frozen DINOv2 as the alternative."""
import os

import cv2
import numpy as np
import torch
from PIL import Image

# Model and input size can be overridden for experiments. An index must be built
# and queried with the same settings; perception.index records them.
# Which descriptor turns an image into a vector:
#   megaloc  DINOv2-base with a learned aggregation head trained for place recognition,
#            including indoor scans (8,448-d, 322 px input).
#   dinov2   frozen DINOv2: class token joined with mean patch token (1,536-d).
# An index must be built and queried with the same descriptor; perception.index
# records it. Override with PINPOINT_DESCRIPTOR.
DESCRIPTOR = os.environ.get("PINPOINT_DESCRIPTOR", "megaloc")
MODEL_NAME = os.environ.get("PINPOINT_EMBED_MODEL", "facebook/dinov2-base")   # dinov2 only
RES = int(os.environ.get("PINPOINT_EMBED_RES", "322" if DESCRIPTOR == "megaloc" else "224"))  # multiple of 14
_state = {}


def _device():
    return "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")


def _load():
    if _state:
        return _state
    dev = _device()
    if DESCRIPTOR == "megaloc":
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file
        from .vendor.megaloc_model import MegaLoc
        model = MegaLoc()
        model.load_state_dict(load_file(hf_hub_download(repo_id="gberton/MegaLoc", filename="model.safetensors")))
        _state.update(model=model.to(dev).eval(), dev=dev, weights="megaloc")
    else:
        from transformers import AutoImageProcessor, AutoModel
        if RES == 224:
            proc = AutoImageProcessor.from_pretrained(MODEL_NAME)        # resize 256, centre crop 224
        else:                                                           # whole image at RES x RES
            proc = AutoImageProcessor.from_pretrained(
                MODEL_NAME, size={"shortest_edge": RES}, crop_size={"height": RES, "width": RES})
        _state.update(proc=proc, model=AutoModel.from_pretrained(MODEL_NAME).to(dev).eval(),
                      dev=dev, weights="stock")
    return _state


def weights_name():
    """Which descriptor weights embed() is using."""
    return _load()["weights"]


_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _embed_megaloc(s, images, batch):
    out = []
    for i in range(0, len(images), batch):
        chunk = []
        for im in images[i:i + batch]:
            rgb = np.asarray(im.convert("RGB")) if isinstance(im, Image.Image) else im[:, :, ::-1]
            rgb = cv2.resize(np.ascontiguousarray(rgb), (RES, RES), interpolation=cv2.INTER_AREA)
            chunk.append(((rgb.astype(np.float32) / 255 - _MEAN) / _STD).transpose(2, 0, 1))
        with torch.no_grad():
            d = s["model"](torch.from_numpy(np.stack(chunk)).to(s["dev"]))
        out.append(d.float().cpu().numpy())
    return np.concatenate(out).astype(np.float32)


def embed(images, batch=32):
    """BGR arrays (as from cv2) or PIL images -> unit-length vectors, one row each.
    The vector is the class token joined with the mean of the patch tokens."""
    s = _load()
    if DESCRIPTOR == "megaloc":
        return _embed_megaloc(s, images, batch)
    pil = [im if isinstance(im, Image.Image) else Image.fromarray(im[:, :, ::-1]) for im in images]
    out = []
    for i in range(0, len(pil), batch):
        x = s["proc"](images=pil[i:i + batch], return_tensors="pt")
        with torch.no_grad():
            h = s["model"](**{k: v.to(s["dev"]) for k, v in x.items()}).last_hidden_state
        v = torch.cat([torch.nn.functional.normalize(h[:, 0], dim=-1),
                       torch.nn.functional.normalize(h[:, 1:].mean(1), dim=-1)], -1)
        out.append(torch.nn.functional.normalize(v, dim=-1).cpu().numpy())
    return np.concatenate(out).astype(np.float32)
