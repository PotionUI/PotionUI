"""Generate the golden fixture for PotionUI's MiniMaxH3LinearBranch port.

Runs OpenVDN's own BidirectionalLinearBranch (Apache-2.0, tree b8cb28f) on CPU at a
tiny shape with CORRELATED inputs (random keys do not reproduce the bf16/Cholesky
trap the reference documents), and writes the weights, the inputs and the outputs to
a .pt the committed test loads. The committed test never imports this tree.

Run from the OpenVDN checkout root:
    PYTHONPATH=<repo>/venv/lib/python3.12/site-packages:. python <this file> <out_dir>
"""
import json
import sys

import torch

from src.models.attention_gates import OutputGate
from src.models.linear_attention.branch import BidirectionalLinearBranch
from src.models.softmax_attention.window import window_bounds

SEED = 20260906

HIDDEN, HEADS, HEAD_DIM = 16, 2, 8
NUM_FRAMES, FRAME_H, FRAME_W = 9, 2, 3
TOKENS_PER_FRAME = FRAME_H * FRAME_W
TEXT_LEN, GLOBAL_LEN = 3, 2
CHUNK, RADIUS = 2, 1

VIDEO_START = TEXT_LEN + GLOBAL_LEN
VIDEO_ROWS = NUM_FRAMES * TOKENS_PER_FRAME
SEQ_LEN = VIDEO_START + VIDEO_ROWS


def correlated(rows, width, rank, gen, noise=0.05):
    """Low-rank basis + small noise: patches within a frame are strongly correlated,
    which is what makes A's off-diagonals large (scan.py:117-129)."""
    basis = torch.randn(rank, width, generator=gen)
    mix = torch.randn(rows, rank, generator=gen)
    return mix @ basis + noise * torch.randn(rows, width, generator=gen)


def main(out_dir):
    torch.manual_seed(SEED)
    gen = torch.Generator().manual_seed(SEED)

    branch = BidirectionalLinearBranch(
        HIDDEN, HEADS, HEAD_DIM, delta_rule="vdn_solve", bridge="alpha", a_fp32=True,
        short_conv=("k", "v"),
    )
    to_out_linear = torch.nn.Linear(HEADS * HEAD_DIM, HIDDEN, bias=False)
    softmax_gate = OutputGate(HIDDEN, HEADS, init_value=0.99)
    branch.eval()

    inner = HEADS * HEAD_DIM
    x = correlated(SEQ_LEN, HIDDEN, 3, gen)
    q_raw = correlated(SEQ_LEN, inner, 3, gen).view(SEQ_LEN, HEADS, HEAD_DIM)
    k_raw = correlated(SEQ_LEN, inner, 2, gen).view(SEQ_LEN, HEADS, HEAD_DIM)
    v_raw = correlated(SEQ_LEN, inner, 3, gen).view(SEQ_LEN, HEADS, HEAD_DIM)

    bounds = window_bounds(NUM_FRAMES, RADIUS, CHUNK)
    video = slice(VIDEO_START, SEQ_LEN)
    text = slice(0, TEXT_LEN)
    qkv_video = (q_raw[video], k_raw[video], v_raw[video])
    qkv_text = (q_raw[text], k_raw[text], v_raw[text])

    outputs = {}
    with torch.no_grad():
        for name, skip_ends, with_text in (
            ("anchors", True, True),
            ("no_anchors", False, True),
            ("no_text", True, False),
        ):
            readout = branch(
                x[video], NUM_FRAMES, TOKENS_PER_FRAME, bounds, qkv_raw=qkv_video,
                frame_size=(FRAME_H, FRAME_W), skip_ends=skip_ends,
                text_x=x[text] if with_text else None,
                text_qkv_raw=qkv_text if with_text else None,
            )
            outputs[f"readout_{name}"] = readout.clone()
            outputs[f"projected_{name}"] = to_out_linear(readout).clone()
        outputs["softmax_gate"] = softmax_gate(x).clone()

    state = {f"linear_attention.{k}": v.clone() for k, v in branch.state_dict().items()}
    state["to_out_linear.weight"] = to_out_linear.weight.detach().clone()
    state["softmax_gate.up.weight"] = softmax_gate.up.weight.detach().clone()
    state["softmax_gate.up.bias"] = softmax_gate.up.bias.detach().clone()

    fixture = {
        "state_dict": state,
        "x": x,
        "q_raw": q_raw,
        "k_raw": k_raw,
        "v_raw": v_raw,
        "bounds": torch.tensor(bounds, dtype=torch.int64),
        **outputs,
    }
    torch.save(fixture, f"{out_dir}/linear_branch_golden.pt")

    provenance = {
        "source": "https://github.com/OpenVDN/vdn-minimax-h3",
        "tree": "b8cb28fbfca0266d1c7742a9f25ab8b58191de97",
        "licence": "Apache-2.0",
        "generator": "scratchpad/vdn_golden_gen.py",
        "seed": SEED,
        "torch": torch.__version__,
        "config": {
            "hidden": HIDDEN, "heads": HEADS, "head_dim": HEAD_DIM,
            "num_frames": NUM_FRAMES, "frame_height": FRAME_H, "frame_width": FRAME_W,
            "tokens_per_frame": TOKENS_PER_FRAME, "text_len": TEXT_LEN,
            "global_len": GLOBAL_LEN, "video_start": VIDEO_START, "seq_len": SEQ_LEN,
            "chunk": CHUNK, "radius": RADIUS,
            "delta_rule": "vdn_solve", "bridge": "alpha", "short_conv": ["k", "v"],
        },
        "cases": {
            "anchors": "skip_ends=True, text state on (the released anchor_frames='both')",
            "no_anchors": "skip_ends=False, text state on",
            "no_text": "skip_ends=True, both scans start from zero",
        },
    }
    with open(f"{out_dir}/linear_branch_golden.json", "w") as handle:
        json.dump(provenance, handle, indent=2)
        handle.write("\n")
    print(json.dumps({k: list(v.shape) for k, v in state.items()}, indent=2))
    print("outputs:", {k: list(v.shape) for k, v in outputs.items()})


if __name__ == "__main__":
    main(sys.argv[1])
