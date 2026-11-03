KINDS = {"pair": ["image", "audio"], "clip": ["video"], "face": ["image"]}

CALLS = []


def members(item_key, caller):
    CALLS.append((item_key, caller))
    name = item_key.rsplit(":", 1)[-1]
    if name == "gone":
        raise ValueError(f"the Bundle {item_key} is gone")
    if name == "boom":
        raise RuntimeError("fixture source failure")
    return [{"kind": kind, "name": f"{name} {kind}"} for kind in KINDS.get(name, [])]
