"""Image embeddings from a self-supervised vision model (DINOv2), on the laptop GPU."""
import os

import numpy as np
import torch
from PIL import Image

# Model and input size can be overridden for experiments. An index must be built
# and queried with the same settings; perception.index records them.
MODEL_NAME = os.environ.get("PINPOINT_EMBED_MODEL", "facebook/dinov2-base")
RES = int(os.environ.get("PINPOINT_EMBED_RES", "224"))      # multiple of 14
# Weights fine-tuned on scans of other buildings (perception.finetune). Used when
# present; set PINPOINT_EMBED_STOCK=1 to ignore them. An index must be built and
# queried with the same weights.
FINETUNED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "cache", "models", "dinov2_ft.pt")
_state = {}


def _load():
    if not _state:
        from transformers import AutoImageProcessor, AutoModel
        dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
        if RES == 224:
            _state["proc"] = AutoImageProcessor.from_pretrained(MODEL_NAME)     # resize 256, centre crop 224
        else:                                                               # whole image at RES x RES
            _state["proc"] = AutoImageProcessor.from_pretrained(
                MODEL_NAME, size={"shortest_edge": RES}, crop_size={"height": RES, "width": RES})
        model = AutoModel.from_pretrained(MODEL_NAME)
        _state["weights"] = "stock"
        if os.path.exists(FINETUNED) and not os.environ.get("PINPOINT_EMBED_STOCK"):
            model.load_state_dict(torch.load(FINETUNED, map_location="cpu"))
            _state["weights"] = "finetuned"
        _state["model"] = model.to(dev).eval()
        _state["dev"] = dev
    return _state


def weights_name():
    """"stock" or "finetuned": which weights embed() is using."""
    return _load()["weights"]


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
