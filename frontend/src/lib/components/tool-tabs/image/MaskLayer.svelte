<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { canvasPoint } from '$lib/paint/raster/brush';
	import { loadImage } from '$lib/media/loadImage';
	import { canvasToPngFile } from '$lib/paint/flatten';
	import { overlays } from '$lib/editors/overlays';
	import { entryKey } from '$lib/editors/registry';
	import type { Point, Rect } from '$lib/paint/types';
	import { alphaToMask, maskOverlay, parseRgb, type Rgb } from '$lib/tool-tabs/image/mask';
	import { ensureCoreMaskOverlays } from '$lib/tool-tabs/image/coreMaskOverlays';
	import {
		BRUSH_OVERLAY,
		activeOverlay,
		chooseOverlay,
		maskArea,
		maskToolEntries,
		maskToolState,
		type MaskOverlayProps,
		type MaskSurface
	} from '$lib/tool-tabs/image/maskOverlays';
	import { regionBounds, stampShape } from '$lib/tool-tabs/image/maskRegion';
	import { alphaPatch, applyAlphaPatch, trimPatches, type AlphaPatch } from '$lib/tool-tabs/image/maskHistory';
	import { backgroundMarkFrom } from '$lib/tool-tabs/image/generate/subject';
	import CanvasChip from '$lib/components/tool-tabs/frame/CanvasChip.svelte';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';

	let {
		width,
		height,
		existing = null,
		brush = 48,
		erase = false,
		disabled = false,
		phone = false,
		seed = null,
		onseeded,
		onstroke,
		onhistory
	}: {
		width: number;
		height: number;
		existing?: string | null;
		brush?: number;
		erase?: boolean;
		disabled?: boolean;
		phone?: boolean;
		seed?: { path: Point[]; width: number; height: number } | null;
		onseeded?: () => void;
		onstroke?: (painted: boolean) => void;
		onhistory?: (state: { canUndo: boolean; canRedo: boolean }) => void;
	} = $props();

	ensureCoreMaskOverlays();

	const revision = overlays.revision;

	let canvas = $state<HTMLCanvasElement>();
	let area = $state<Rect | null>(null);
	let tint: Rgb = [255, 255, 255];
	let tintCss = 'white';

	let entries = $derived.by(() => {
		void $revision;
		return maskToolEntries();
	});
	let active = $derived(activeOverlay(entries, $maskToolState));
	let cursor = $derived(active?.cursor === 'default' ? 'cursor-default' : 'cursor-crosshair');

	function context(): CanvasRenderingContext2D | null {
		return canvas?.getContext('2d', { willReadFrequently: true }) ?? null;
	}

	function toPicture(clientX: number, clientY: number) {
		if (!canvas) return null;
		return canvasPoint(canvas.getBoundingClientRect(), canvas, clientX, clientY);
	}

	function scale(): number {
		return canvas ? canvas.width / Math.max(1, canvas.getBoundingClientRect().width) : 1;
	}

	function refreshArea(pixels?: Uint8ClampedArray) {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return;
		try {
			area = regionBounds(pixels ?? surfaceContext.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
		} catch {
			area = null;
		}
		maskArea.set(area ? { width: area.width, height: area.height } : null);
	}

	let baseline: Uint8Array | null = null;
	let past: AlphaPatch[] = [];
	let future: AlphaPatch[] = [];

	function alphaOf(pixels?: Uint8ClampedArray): Uint8Array | null {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return null;
		const data = pixels ?? surfaceContext.getImageData(0, 0, canvas.width, canvas.height).data;
		const alpha = new Uint8Array(canvas.width * canvas.height);
		for (let i = 0; i < alpha.length; i++) alpha[i] = data[i * 4 + 3];
		return alpha;
	}

	function paintPatch(patch: AlphaPatch, side: 'before' | 'after') {
		const surfaceContext = context();
		if (!surfaceContext) return;
		const source = patch[side];
		const image = surfaceContext.createImageData(patch.width, patch.height);
		for (let i = 0; i < source.length; i++) {
			image.data[i * 4] = tint[0];
			image.data[i * 4 + 1] = tint[1];
			image.data[i * 4 + 2] = tint[2];
			image.data[i * 4 + 3] = source[i];
		}
		surfaceContext.putImageData(image, patch.x, patch.y);
	}

	function paintAlpha(alpha: Uint8Array) {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return;
		const image = surfaceContext.createImageData(canvas.width, canvas.height);
		for (let i = 0; i < alpha.length; i++) {
			image.data[i * 4] = tint[0];
			image.data[i * 4 + 1] = tint[1];
			image.data[i * 4 + 2] = tint[2];
			image.data[i * 4 + 3] = alpha[i];
		}
		surfaceContext.putImageData(image, 0, 0);
	}

	function announce() {
		onhistory?.({ canUndo: past.length > 0, canRedo: future.length > 0 });
	}

	function record(pixels?: Uint8ClampedArray) {
		const next = alphaOf(pixels);
		if (baseline && next && canvas) {
			const patch = alphaPatch(baseline, next, canvas.width, canvas.height);
			if (patch) {
				past = trimPatches([...past, patch]);
				future = [];
			}
		} else future = [];
		baseline = next;
		announce();
	}

	export function undo(): boolean {
		const patch = past[past.length - 1];
		if (!patch || !baseline || !canvas) return false;
		applyAlphaPatch(baseline, patch, canvas.width, 'before');
		future = [...future, patch];
		past = past.slice(0, -1);
		paintPatch(patch, 'before');
		refreshArea();
		onstroke?.(true);
		announce();
		return true;
	}

	export function redo(): boolean {
		const patch = future[future.length - 1];
		if (!patch || !baseline || !canvas) return false;
		applyAlphaPatch(baseline, patch, canvas.width, 'after');
		past = [...past, patch];
		future = future.slice(0, -1);
		paintPatch(patch, 'after');
		refreshArea();
		onstroke?.(true);
		announce();
		return true;
	}

	function changed() {
		const surfaceContext = context();
		const pixels = canvas && surfaceContext ? surfaceContext.getImageData(0, 0, canvas.width, canvas.height).data : undefined;
		refreshArea(pixels);
		record(pixels);
		onstroke?.(true);
	}

	async function commitSelection(mask: Blob | null) {
		if (!mask) {
			clear();
			onstroke?.(true);
			return;
		}
		const url = URL.createObjectURL(mask);
		try {
			await restore(url);
		} finally {
			URL.revokeObjectURL(url);
		}
		changed();
	}

	let surface = $derived<MaskSurface | null>(
		canvas
			? {
					element: canvas,
					context,
					get tint() {
						return tint;
					},
					get tintCss() {
						return tintCss;
					},
					toPicture,
					scale,
					changed
				}
			: null
	);

	let pictureWidth = $derived(Math.max(1, Math.round(width)));
	let pictureHeight = $derived(Math.max(1, Math.round(height)));

	let overlayProps = $derived<MaskOverlayProps | null>(
		surface
			? { surface, width: pictureWidth, height: pictureHeight, phone, size: brush, subtract: active?.modes?.length ? erase : false, disabled, commitSelection }
			: null
	);

	let chipStyle = $derived(
		area
			? `left: min(${(area.x / pictureWidth) * 100}%, calc(100% - 6rem)); top: ${((area.y + area.height) / pictureHeight) * 100}%; transform: translateY(${
					(area.y + area.height) / pictureHeight > 0.9 ? 'calc(-100% - 6px)' : '6px'
				});`
			: ''
	);

	onMount(async () => {
		if (!canvas) return;
		canvas.width = pictureWidth;
		canvas.height = pictureHeight;
		context();
		const signal = getComputedStyle(document.documentElement).getPropertyValue('--signal');
		tint = parseRgb(signal, tint);
		tintCss = `rgb(${tint.join(' ')})`;
		if (existing) {
			await restore(existing);
			refreshArea();
		}
		baseline = alphaOf();
		if (seed) {
			fillFrom(seed);
			onseeded?.();
		}
	});

	function fillFrom(shape: { path: Point[]; width: number; height: number }) {
		const surfaceContext = context();
		if (!canvas || !surfaceContext || shape.width <= 0 || shape.height <= 0) return;
		const scaleX = canvas.width / shape.width;
		const scaleY = canvas.height / shape.height;
		const image = surfaceContext.createImageData(canvas.width, canvas.height);
		stampShape(image.data, canvas.width, canvas.height, shape.path.map((point) => ({ x: point.x * scaleX, y: point.y * scaleY })), false, tint);
		surfaceContext.putImageData(image, 0, 0);
		changed();
	}

	export async function importBackground(url: string): Promise<boolean> {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return false;
		try {
			const image = await loadImage(url);
			const scratch = document.createElement('canvas');
			scratch.width = canvas.width;
			scratch.height = canvas.height;
			const scratchContext = scratch.getContext('2d', { willReadFrequently: true });
			if (!scratchContext) return false;
			scratchContext.drawImage(image, 0, 0, scratch.width, scratch.height);
			paintAlpha(backgroundMarkFrom(scratchContext.getImageData(0, 0, scratch.width, scratch.height).data));
			changed();
			return true;
		} catch {
			return false;
		}
	}

	export function invert() {
		const alpha = alphaOf();
		if (!alpha) return;
		for (let i = 0; i < alpha.length; i++) alpha[i] = 255 - alpha[i];
		paintAlpha(alpha);
		changed();
	}

	onDestroy(() => maskArea.set(null));

	async function restore(url: string) {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return;
		try {
			const image = await loadImage(url);
			const scratch = document.createElement('canvas');
			scratch.width = canvas.width;
			scratch.height = canvas.height;
			const scratchContext = scratch.getContext('2d');
			if (!scratchContext) return;
			scratchContext.drawImage(image, 0, 0, scratch.width, scratch.height);
			const source = scratchContext.getImageData(0, 0, scratch.width, scratch.height);
			const { data } = maskOverlay(source.data, tint);
			surfaceContext.putImageData(new ImageData(data as Uint8ClampedArray<ArrayBuffer>, scratch.width, scratch.height), 0, 0);
		} catch {
			return;
		}
	}

	export function hasStrokes(): boolean {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return false;
		const data = surfaceContext.getImageData(0, 0, canvas.width, canvas.height).data;
		for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
		return false;
	}

	export function clear() {
		const surfaceContext = context();
		if (canvas && surfaceContext) surfaceContext.clearRect(0, 0, canvas.width, canvas.height);
		area = null;
		maskArea.set(null);
		record();
	}

	export function renderMask(cutoff = 64): HTMLCanvasElement | null {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return null;
		const data = surfaceContext.getImageData(0, 0, canvas.width, canvas.height);
		const out = document.createElement('canvas');
		out.width = canvas.width;
		out.height = canvas.height;
		const outContext = out.getContext('2d');
		if (!outContext) return null;
		const mask = outContext.createImageData(out.width, out.height);
		alphaToMask(data.data, mask.data, cutoff);
		outContext.putImageData(mask, 0, 0);
		return out;
	}

	export function renderSoftMask(): HTMLCanvasElement | null {
		const surfaceContext = context();
		if (!canvas || !surfaceContext) return null;
		const data = surfaceContext.getImageData(0, 0, canvas.width, canvas.height).data;
		const out = document.createElement('canvas');
		out.width = canvas.width;
		out.height = canvas.height;
		const outContext = out.getContext('2d');
		if (!outContext) return null;
		const soft = outContext.createImageData(out.width, out.height);
		for (let i = 0; i < data.length; i += 4) {
			const value = data[i + 3];
			soft.data[i] = value;
			soft.data[i + 1] = value;
			soft.data[i + 2] = value;
			soft.data[i + 3] = 255;
		}
		outContext.putImageData(soft, 0, 0);
		return out;
	}

	export async function exportMask(name: string, cutoff = 64): Promise<File | null> {
		const out = renderMask(cutoff);
		return out ? canvasToPngFile(out, name) : null;
	}
</script>

<div class="pointer-events-none absolute inset-0" data-mask-host>
	<canvas
		bind:this={canvas}
		class="absolute inset-0 h-full w-full touch-none opacity-60 {disabled ? 'pointer-events-none' : `pointer-events-auto ${cursor}`}"
		aria-label="Mask, mark the area to change"
		data-mask-layer
	></canvas>
	{#if active && overlayProps}
		{#key active.id}
			<EditorEntryComponent entry={entryKey('image', active.id)} componentRef={active.component} props={{ ...overlayProps }} onclose={() => chooseOverlay(BRUSH_OVERLAY)} />
		{/key}
	{/if}
	{#if area}
		<span class="pointer-events-none absolute" style={chipStyle} data-mask-area>
			<CanvasChip caps={false}>{area.width}×{area.height}</CanvasChip>
		</span>
	{/if}
</div>
