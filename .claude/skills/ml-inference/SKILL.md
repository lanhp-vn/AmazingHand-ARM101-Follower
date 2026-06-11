---
name: ml-inference
description: Use when loading a PyTorch checkpoint for local inference in this workspace — covers safe checkpoint loading (weights_only + argparse.Namespace allowlist), extracting checkpoint['model'], slim safetensors export, and the timm ViT load recipe used by fundus_analysis.
---

# Local ML inference (PyTorch / timm)

Patterns proven in `src/arm101_hand/fundus_analysis/` (RETFound DR grading).

## Safe checkpoint loading

RETFound/MAE checkpoints embed an `argparse.Namespace` under `args`, so plain
`weights_only=True` fails with `UnpicklingError`. Allowlist exactly that class —
never fall back to `weights_only=False` on a file you did not produce:

```python
import argparse
import torch

with torch.serialization.safe_globals([argparse.Namespace]):
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
state_dict = ckpt["model"]   # fine-tune checkpoints nest weights under 'model'
```

## Slim export (drop optimizer state)

Fine-tune checkpoints carry optimizer + scaler state (~2/3 of the file). Export
just the model weights to safetensors (pickle-free, ~3× faster load), into a
gitignored `models/` dir — never write into read-only `references/` (IL-2):

```python
from safetensors.torch import save_file

save_file({k: v.contiguous().clone() for k, v in state_dict.items()}, out)
```

`safetensors` requires contiguous tensors and rejects shared storage — the
`.contiguous().clone()` is load-bearing.

## timm ViT load recipe

`timm.create_model("vit_large_patch16_224", num_classes=5, global_pool="avg")`
matches RETFound's global-pool ViT exactly (296/296 keys). Load with
`strict=True` so any key drift fails loudly — it doubles as a correctness gate.
Note: `global_pool="token"` does NOT match (keeps `norm.*`, drops `fc_norm.*`).

## Eval transform

`Resize(256, BICUBIC)` → `CenterCrop(224)` → `ToTensor` → `Normalize(ImageNet)`
(mean `[0.485, 0.456, 0.406]`, std `[0.229, 0.224, 0.225]`). cv2 reads BGR;
convert to RGB PIL before the torchvision transform.

## Privacy

Patient retinal images: keep inference fully local. No network calls, no cloud
APIs, no telemetry in the grading path.
```
