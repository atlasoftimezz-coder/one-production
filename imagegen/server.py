"""One Production image assets server (owner, 2026-10-12: "use image generation for asset if hard to
create in davinci"). Runs in its own Python (installed by One Production under the image module's
folder on a drive with room - the studio PC's C: is nearly full) and is started by the app.

Model: FLUX.1-schnell (Apache-2.0), slimmed for a 16 GB card and 15 GB of RAM (owner's choice):
    transformer   GGUF Q5_K_S (city96/FLUX.1-schnell-gguf), dequantised on the fly (diffusers GGUF)
    text encoder  T5-XXL loaded 4-bit (bitsandbytes nf4) straight onto the GPU
    CLIP-L + VAE  bf16
4 steps at 1024x1024 (schnell is guidance-distilled: no negative prompt). Then BiRefNet (MIT) cuts the
object out; the PNG is trimmed to its alpha box plus a margin.

    GET  /api/meta       {"loaded", "model", "device"}
    POST /api/generate   {"prompt", "seed", "width", "height", "steps", "out", "cutout"} -> {"file", "w", "h", "seconds"}
    POST /api/unload     frees the GPU (the voice model or Resolve may need it)
"""
import argparse
import gc
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO = "black-forest-labs/FLUX.1-schnell"
GGUF_REPO, GGUF_FILE = "city96/FLUX.1-schnell-gguf", "flux1-schnell-Q5_K_S.gguf"
CUTOUT_REPO = "ZhengPeng7/BiRefNet"
app = FastAPI(title="One Production image assets")
STATE: dict = {"pipe": None, "cut": None, "model": f"FLUX.1-schnell ({GGUF_FILE}, T5 nf4)"}


class GenIn(BaseModel):
    prompt: str
    out: str
    seed: int = 0
    # 768, not 1024: with DaVinci Resolve also on the 16 GB card, 1024 filled it and Windows spilled into system
    # RAM - 214 s an object instead of 26 (first real draft, 2026-10-12). Heroes are at most ~650 px tall at 1080p.
    width: int = 768
    height: int = 768
    steps: int = 4
    cutout: bool = True


def _pipe():
    if STATE["pipe"] is not None:
        return STATE["pipe"]
    import torch
    from diffusers import FluxPipeline, FluxTransformer2DModel, GGUFQuantizationConfig
    from huggingface_hub import hf_hub_download
    from transformers import BitsAndBytesConfig, T5EncoderModel
    gguf = hf_hub_download(GGUF_REPO, GGUF_FILE)
    transformer = FluxTransformer2DModel.from_single_file(
        gguf, quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16), torch_dtype=torch.bfloat16,
        config=REPO, subfolder="transformer")
    t5 = T5EncoderModel.from_pretrained(
        REPO, subfolder="text_encoder_2", torch_dtype=torch.bfloat16, device_map={"": 0},
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.bfloat16))
    pipe = FluxPipeline.from_pretrained(REPO, transformer=transformer, text_encoder_2=t5, torch_dtype=torch.bfloat16)
    for name in ("transformer", "text_encoder", "vae"):          # T5 is already on the card (4-bit)
        getattr(pipe, name).to("cuda")
    STATE["pipe"] = pipe
    return pipe


def _cutter():
    if STATE["cut"] is not None:
        return STATE["cut"]
    import torch
    from transformers import AutoModelForImageSegmentation
    m = AutoModelForImageSegmentation.from_pretrained(CUTOUT_REPO, trust_remote_code=True)
    m.to("cuda").eval().half()
    STATE["cut"] = (m, torch)
    return STATE["cut"]


def cut_out(img):
    """RGB -> RGBA with BiRefNet's mask, trimmed to the object plus a 3% margin."""
    from PIL import Image
    from torchvision import transforms
    model, torch = _cutter()
    tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    with torch.no_grad():
        pred = model(tf(img).unsqueeze(0).to("cuda").half())[-1].sigmoid().float().cpu()[0].squeeze()
    mask = transforms.ToPILImage()(pred).resize(img.size, Image.BILINEAR)
    out = img.convert("RGBA")
    out.putalpha(mask)
    box = mask.point(lambda v: 255 if v > 24 else 0).getbbox()
    if box:
        mx, my = int(0.03 * img.width), int(0.03 * img.height)
        box = (max(0, box[0] - mx), max(0, box[1] - my), min(img.width, box[2] + mx), min(img.height, box[3] + my))
        out = out.crop(box)
    return out


@app.get("/api/meta")
def meta():
    import torch
    return {"loaded": STATE["pipe"] is not None, "model": STATE["model"],
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"}


@app.post("/api/generate")
def generate(body: GenIn):
    import torch
    if not body.prompt.strip():
        raise HTTPException(400, "empty prompt")
    t = time.time()
    try:
        pipe = _pipe()
        g = torch.Generator("cuda").manual_seed(int(body.seed))
        img = pipe(body.prompt, width=body.width, height=body.height, num_inference_steps=body.steps,
                   guidance_scale=0.0, max_sequence_length=256, generator=g).images[0]
        out = cut_out(img) if body.cutout else img
    except torch.cuda.OutOfMemoryError:
        unload()
        raise HTTPException(507, "the graphics card ran out of memory - close other GPU programs and try again")
    finally:
        torch.cuda.empty_cache()                       # hand cached blocks back - Resolve shares the card
    target = Path(body.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target)
    return {"file": str(target), "w": out.width, "h": out.height, "seconds": round(time.time() - t, 1)}


@app.post("/api/unload")
def unload():
    import torch
    STATE["pipe"] = None
    STATE["cut"] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return {"loaded": False}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8722)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")   # less fragmentation on a shared card
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
