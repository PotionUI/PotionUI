<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';
	import {
		ZOOM_STEP,
		actualSize,
		fitViewport,
		isActualSize,
		panView,
		pinchStep,
		resolveView,
		wheelFactor,
		zoomAt,
		zoomCentered,
		zoomPercent,
		type Point,
		type Viewport
	} from '$lib/tool-tabs/frame/viewport';
	import CanvasChip from './CanvasChip.svelte';

	let {
		mediaWidth,
		mediaHeight,
		viewport = $bindable(fitViewport()),
		compare = false,
		split = $bindable(0.5),
		beforeLabel = 'Before',
		afterLabel = 'After',
		phone = false,
		padding,
		spaceHeld,
		hint,
		layer,
		before,
		overlay,
		badge,
		children
	}: {
		mediaWidth: number;
		mediaHeight: number;
		viewport?: Viewport;
		compare?: boolean;
		split?: number;
		beforeLabel?: string;
		afterLabel?: string;
		phone?: boolean;
		padding?: number;
		spaceHeld?: boolean;
		hint?: Snippet | null;
		layer: Snippet;
		before?: Snippet;
		overlay?: Snippet;
		badge?: Snippet;
		children?: Snippet;
	} = $props();

	const frame = getFrameContext();

	let stageEl = $state<HTMLElement>();
	let stage = $state({ width: 0, height: 0 });
	let panning = $state(false);
	let held = $derived(spaceHeld ?? frame?.spaceHeld ?? false);
	let media = $derived({ width: mediaWidth, height: mediaHeight });
	let view = $derived(resolveView(viewport, stage, media, padding ?? (phone ? 14 : 60)));
	let art = $derived({
		left: view.panX,
		top: view.panY,
		width: Math.max(0, mediaWidth * view.zoom),
		height: Math.max(0, mediaHeight * view.zoom)
	});
	let splitPct = $derived(Math.max(0, Math.min(1, split)) * 100);

	const touches = new Map<number, Point>();
	let last: Point | null = null;
	let splitting = false;

	$effect(() => {
		const el = stageEl;
		if (!el) return;
		const measure = () => (stage = { width: el.clientWidth, height: el.clientHeight });
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		const onWheel = (event: WheelEvent) => {
			event.preventDefault();
			const rect = el.getBoundingClientRect();
			const factor = wheelFactor(event.deltaY, event.deltaMode, event.ctrlKey);
			viewport = zoomAt(view, factor, { x: event.clientX - rect.left, y: event.clientY - rect.top });
		};
		el.addEventListener('wheel', onWheel, { passive: false });
		return () => {
			observer.disconnect();
			el.removeEventListener('wheel', onWheel);
		};
	});

	function local(event: PointerEvent): Point {
		const rect = stageEl!.getBoundingClientRect();
		return { x: event.clientX - rect.left, y: event.clientY - rect.top };
	}

	function pair(): [Point, Point] {
		const [a, b] = [...touches.values()];
		return [{ ...a }, { ...b }];
	}

	function down(event: PointerEvent) {
		if (event.pointerType === 'touch') {
			touches.set(event.pointerId, local(event));
			if (touches.size === 2) {
				event.stopPropagation();
				event.preventDefault();
			}
			return;
		}
		if (!held && event.button !== 1) return;
		event.stopPropagation();
		event.preventDefault();
		stageEl?.setPointerCapture?.(event.pointerId);
		panning = true;
		last = local(event);
	}

	function moved(event: PointerEvent) {
		if (event.pointerType === 'touch' && touches.has(event.pointerId)) {
			if (touches.size === 2) {
				const before = pair();
				touches.set(event.pointerId, local(event));
				viewport = pinchStep(view, before, pair());
				event.stopPropagation();
			} else {
				touches.set(event.pointerId, local(event));
			}
			return;
		}
		if (!panning || !last) return;
		const point = local(event);
		viewport = panView(view, point.x - last.x, point.y - last.y);
		last = point;
	}

	function up(event: PointerEvent) {
		touches.delete(event.pointerId);
		if (panning) {
			panning = false;
			last = null;
		}
	}

	function setSplitFrom(event: PointerEvent) {
		const rect = stageEl!.getBoundingClientRect();
		if (art.width <= 0) return;
		split = Math.max(0, Math.min(1, (event.clientX - rect.left - art.left) / art.width));
	}

	function splitDown(event: PointerEvent) {
		if (held || event.button !== 0) return;
		event.stopPropagation();
		event.preventDefault();
		(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
		splitting = true;
		setSplitFrom(event);
	}

	function splitMove(event: PointerEvent) {
		if (splitting) setSplitFrom(event);
	}

	function splitKey(event: KeyboardEvent) {
		const step = event.shiftKey ? 0.1 : 0.02;
		if (event.key === 'ArrowLeft') split = Math.max(0, split - step);
		else if (event.key === 'ArrowRight') split = Math.min(1, split + step);
		else if (event.key === 'Home') split = 0;
		else if (event.key === 'End') split = 1;
		else return;
		event.preventDefault();
	}

	let canZoomIn = $derived(view.zoom < 16);
	let canZoomOut = $derived(view.zoom > 0.05);
</script>

<div
	bind:this={stageEl}
	class="relative min-h-0 min-w-0 flex-1 touch-none select-none overflow-hidden bg-scrim {held ? (panning ? 'cursor-grabbing' : 'cursor-grab') : ''}"
	role="presentation"
	onpointerdowncapture={down}
	onpointermovecapture={moved}
	onpointerupcapture={up}
	onpointercancelcapture={up}
	data-editor-canvas
	data-zoom={zoomPercent(view)}
>
	<div
		class="absolute"
		style="left: {art.left}px; top: {art.top}px; width: {art.width}px; height: {art.height}px;"
		data-canvas-art
	>
		<div class="absolute inset-0">{@render layer()}</div>
		{#if compare && before}
			<div class="absolute inset-0" style="clip-path: inset(0 {100 - splitPct}% 0 0);" data-canvas-before>
				{@render before()}
			</div>
		{/if}
		{#if overlay}
			<div class="absolute inset-0">{@render overlay()}</div>
		{/if}
		{#if compare}
			<div
				class="absolute inset-y-0 w-0.5 -translate-x-px cursor-ew-resize bg-scrim-on shadow-[0_0_0_1px_rgb(var(--scrim)/0.25)]"
				style="left: {splitPct}%;"
				onpointerdown={splitDown}
				onpointermove={splitMove}
				onpointerup={() => (splitting = false)}
				onpointercancel={() => (splitting = false)}
				role="presentation"
				data-canvas-divider
			>
				<div
					class="absolute left-1/2 top-1/2 grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded bg-scrim-on text-scrim shadow-floating focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-signal"
					role="slider"
					tabindex="0"
					aria-label="Before and after split"
					aria-valuemin={0}
					aria-valuemax={100}
					aria-valuenow={Math.round(splitPct)}
					onkeydown={splitKey}
					data-canvas-knob
				>
					<Icon name="editor-compare" className="h-3.5 w-3.5" strokeWidth={1.7} />
				</div>
			</div>
			<CanvasChip class="pointer-events-none absolute left-3 top-3">{beforeLabel}</CanvasChip>
			<CanvasChip class="pointer-events-none absolute right-3 top-3">{afterLabel}</CanvasChip>
		{:else if badge}
			<div class="pointer-events-none absolute left-3 top-3">{@render badge()}</div>
		{/if}
	</div>

	{@render children?.()}

	{#if !phone}
		<div
			class="absolute bottom-3.5 left-1/2 flex -translate-x-1/2 items-center gap-0.5 rounded-lg border border-scrim-on/10 bg-scrim/70 p-[3px] text-scrim-on backdrop-blur"
			role="group"
			aria-label="Zoom"
			data-canvas-zoom
		>
			<IconButton icon="minus" label="Zoom out" size="xs" variant="overlay" class="h-7 w-7" disabled={!canZoomOut} onclick={() => (viewport = zoomCentered(view, 1 / ZOOM_STEP, stage))} />
			<span class="min-w-[52px] text-center font-mono text-sm tabular-nums" aria-live="polite" data-canvas-zoom-value>{zoomPercent(view)}%</span>
			<IconButton icon="plus" label="Zoom in" size="xs" variant="overlay" class="h-7 w-7" disabled={!canZoomIn} onclick={() => (viewport = zoomCentered(view, ZOOM_STEP, stage))} />
			<span class="mx-[3px] h-4 w-px bg-scrim-on/15" aria-hidden="true"></span>
			<Button variant="overlay" size="xs" class="h-7 font-mono" pressed={viewport.fit} onclick={() => (viewport = fitViewport())} attrs={{ 'data-canvas-fit': '' }}>Fit</Button>
			<Button variant="overlay" size="xs" class="h-7 font-mono" pressed={isActualSize(viewport)} onclick={() => (viewport = actualSize(stage, media))} attrs={{ 'data-canvas-actual': '' }}>1:1</Button>
		</div>
		{#if hint !== null}
			<div class="pointer-events-none absolute bottom-[18px] right-3.5 flex items-center gap-2 font-mono text-sm text-scrim-on/70" data-canvas-hint>
				{#if hint}
					{@render hint()}
				{:else}
					<CanvasChip caps={false}>Space</CanvasChip>drag to pan
				{/if}
			</div>
		{/if}
	{/if}
</div>
