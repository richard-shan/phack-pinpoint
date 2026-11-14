"""Image embeddings from a self-supervised vision model (DINOv2), on the laptop GPU."""
import numpy as np
import torch
from PIL import Image

MODEL_NAME = "facebook/dinov2-base"
_state = {}


def _load():
    if not _state:
        from transformers import AutoImageProcessor, AutoModel
        dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        _state["proc"] = AutoImageProcessor.from_pretrained(MODEL_NAME)
        _state["model"] = AutoModel.from_pretrained(MODEL_NAME).to(dev).eval()
        _state["dev"] = dev
    return _state


def embed(images, batch=32):
    """BGR arrays (as from cv2) or PIL images -> unit-length vectors, one row each.
    The vector is the class token joined with the mean of the patch tokens."""
    s = _load()
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
