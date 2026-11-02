from __future__ import annotations

from typing import Sequence

from PIL import Image

from src.platform.media_tools.fastpath import (
    advance_media,
    prepare_image,
    run_fragments,
    save_image,
)
from src.platform.media_tools.params import validate_params
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.timeline import Clock, StepContext
from src.platform.media_tools.types import (
    FilterFragment,
    InvalidSelection,
    ToolCancelled,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
    UnsupportedMedia,
)

Step = tuple[ToolSpec, dict]


def check_accepts(spec: ToolSpec, media: ToolMedia) -> None:
    if media.kind not in spec.accepts:
        raise UnsupportedMedia(f"{spec.label} cannot be applied to {media.kind}")


def check_requires(spec: ToolSpec, media: ToolMedia) -> None:
    required = spec.requires or {}
    if required.get("has_audio") and media.has_audio is False:
        raise UnsupportedMedia(f"{spec.label} needs a {media.kind} with sound")
    if required.get("has_alpha") and media.has_alpha is False:
        raise UnsupportedMedia(f"{spec.label} needs an image with transparency")
    minimum = required.get("min_duration")
    if minimum and media.duration is not None and media.duration < float(minimum):
        raise UnsupportedMedia(f"{spec.label} needs at least {float(minimum):g}s of media")


def check_selection(spec: ToolSpec, count: int) -> None:
    low, high = spec.selection_bounds()
    if count < low or (high is not None and count > high):
        span = f"{low}" if high == low else (f"{low} to {high}" if high else f"at least {low}")
        raise InvalidSelection(f"{spec.label} needs {span} item(s), got {count}")


def _tool(spec: ToolSpec):
    if spec.impl is None:
        raise ToolFailed(f"{spec.label} has no implementation to run")
    return spec.impl


def _guard(ctx: ToolContext) -> None:
    if ctx.is_cancelled():
        raise ToolCancelled()


def _fuse_timed(
    steps: Sequence[Step], start: int, media: ToolMedia, ctx: ToolContext
) -> tuple[list[FilterFragment], int]:
    fragments: list[FilterFragment] = []
    virtual = media
    clock = Clock(length=media.duration)
    index = start
    while index < len(steps):
        spec, params = steps[index]
        if spec.cardinality != "one_to_one" or virtual.kind not in spec.accepts:
            break
        if spec.output_kind(virtual.kind) != virtual.kind or spec.impl is None:
            break
        if index > start:
            try:
                check_requires(spec, virtual)
            except UnsupportedMedia:
                break
        clock = spec.impl.retime(clock, params)
        params = spec.impl.prepare(params, StepContext(source=media, clock=clock, ctx=ctx.quiet()))
        fragment = spec.impl.video_filter(virtual, params)
        if fragment is None:
            break
        fragments.append(fragment)
        virtual = advance_media(virtual, fragment)
        index += 1
    return fragments, index - start


def _run_images(
    steps: Sequence[Step], start: int, media: ToolMedia, ctx: ToolContext
) -> tuple[ToolMedia | None, int]:
    index = start
    image: Image.Image | None = None
    virtual = media
    while index < len(steps):
        spec, params = steps[index]
        if spec.cardinality != "one_to_one" or virtual.kind not in spec.accepts:
            break
        if spec.output_kind(virtual.kind) != "image" or spec.impl is None or not spec.impl.has_image_path():
            break
        if index > start:
            try:
                check_requires(spec, virtual)
            except UnsupportedMedia:
                break
        if image is None:
            image = prepare_image(media.path)
        image = spec.impl.image(image, params)
        virtual = virtual.with_changes(width=image.width, height=image.height, has_alpha=has_alpha_mode(image))
        index += 1
    if image is None:
        return None, 0
    dest = ctx.new_path(media.path.suffix or ".png", stem=media.path.stem[:40] or "out")
    written = save_image(image, dest)
    return ctx.probe(written, "image"), index - start


def _ends_with_summary(ctx: ToolContext, index: int, total: int) -> bool:
    return ctx.summary is not None and index == total - 1


def _run_item(steps: Sequence[Step], media: ToolMedia, ctx: ToolContext) -> list[ToolMedia]:
    total = len(steps)
    position = 0
    current = media
    while position < total:
        _guard(ctx)
        spec, params = steps[position]
        check_accepts(spec, current)
        check_requires(spec, current)
        tool = _tool(spec)

        if spec.cardinality == "one_to_one":
            if current.kind in ("video", "audio"):
                fragments, consumed = _fuse_timed(steps, position, current, ctx)
                if consumed:
                    stage = ctx.scoped(position / total, (position + consumed) / total)
                    stage.progress(0.0, spec.label)
                    current = run_fragments(current, fragments, stage)
                    stage.progress(1.0, spec.label)
                    position += consumed
                    continue
            else:
                stage = ctx.scoped(position / total, (position + 1) / total)
                produced, consumed = _run_images(steps, position, current, stage)
                if produced is not None:
                    stage.progress(1.0, spec.label)
                    current = produced
                    position += consumed
                    continue

        stage = ctx.scoped(position / total, (position + 1) / total)
        stage.progress(0.0, spec.label)
        outputs = tool.run([current], params, stage)
        stage.progress(1.0, spec.label)
        if not outputs and _ends_with_summary(ctx, position, total):
            return []
        if spec.cardinality == "one_to_many":
            results: list[ToolMedia] = []
            for output in outputs:
                results.extend(_run_item(steps[position + 1:], output, ctx.scoped(position / total, 1.0)))
            return results
        if len(outputs) != 1:
            raise ToolFailed(f"{spec.label} did not produce exactly one result")
        current = outputs[0]
        position += 1
    return [current]


def run_chain(
    steps: Sequence[Step],
    inputs: Sequence[ToolMedia],
    ctx: ToolContext,
    *,
    validate: bool = True,
) -> list[ToolMedia]:
    if not steps:
        raise ToolFailed("There is nothing to run")
    if not inputs:
        raise InvalidSelection("There is nothing to run it on")

    prepared: list[Step] = []
    for index, (spec, params) in enumerate(steps):
        if validate:
            try:
                params = validate_params(spec, params)
            except Exception as exc:
                if hasattr(exc, "step_index"):
                    exc.step_index = index
                raise
        prepared.append((spec, params))

    total = len(prepared)
    current = list(inputs)
    index = 0
    while index < total:
        _guard(ctx)
        spec, params = prepared[index]
        if spec.cardinality == "many_to_one":
            check_selection(spec, len(current))
            for media in current:
                check_accepts(spec, media)
                check_requires(spec, media)
            stage = ctx.scoped(index / total, (index + 1) / total)
            stage.progress(0.0, spec.label)
            outputs = _tool(spec).run(list(current), params, stage)
            stage.progress(1.0, spec.label)
            if not outputs and _ends_with_summary(ctx, index, total):
                return []
            if len(outputs) != 1:
                raise ToolFailed(f"{spec.label} did not produce exactly one result")
            current = outputs
            index += 1
            continue

        end = index
        while end < total and prepared[end][0].cardinality != "many_to_one":
            end += 1
        segment = prepared[index:end]
        results: list[ToolMedia] = []
        for item_number, media in enumerate(current):
            item_ctx = ctx.scoped(
                (index + (end - index) * item_number / len(current)) / total,
                (index + (end - index) * (item_number + 1) / len(current)) / total,
            )
            results.extend(_run_item(segment, media, item_ctx))
        current = results
        index = end
    ctx.progress(1.0, "")
    return current
