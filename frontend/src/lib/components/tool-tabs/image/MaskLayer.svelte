<script lang="ts">
	import { onMount } from 'svelte';
	import { canvasPoint, drawSegment } from '$lib/components/imageEditor/raster/brush';
	import { loadImage } from '$lib/media/editors/loadImage';
	import { canvasToPngFile } from '$lib/components/imageEditor/flatten';
	import { maskOverlay, parseRgb, type Rgb } from '$lib/tool-tabs/image/mask';

	let {
		width,
		height,
		existing = null,
		brush = 48,
		erase = false,
		disabled = false,
		onstroke
	}: {
		width: number;
		height: number;
		existing?: string | null;
		brush?: number;
		erase?: boolean;
		disabled?: boolean;
		onstroke?: (painted: boolean) => void;
	} = $props();

	let canvas = $state<HTMLCanvasElement>();
	let painting = false;
	let last: { x: number; y: number } | null = null;
	let tint: Rgb = [255, 255, 255];
	let tintCss = 'white';

	function surface(): CanvasRenderingContext2D | null {
		return canvas?.getContext('2d', { willReadFrequently: true }) ?? null;
	}

	onMount(async () => {
		if (!canvas) return;
		canvas.width = Math.max(1, Math.round(width));
		canvas.height = Math.max(1, Math.round(height));
		surface();
		const signal = getComputedStyle(document.documentElement).getPropertyValue('--signal');
		tint = parseRgb(signal, tint);
		tintCss = `rgb(${tint.join(' ')})`;
		if (existing) await restore(existing);
	});

	async function restore(url: string) {
		const context = surface();
		if (!canvas || !context) return;
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
			context.putImageData(new ImageData(data as Uint8ClampedArray<ArrayBuffer>, scratch.width, scratch.height), 0, 0);
		} catch {
			return;
		}
	}

	function point(event: PointerEvent) {
		if (!canvas) return null;
		return canvasPoint(canvas.getBoundingClientRect(), canvas, event.clientX, event.clientY);
	}

	function stroke(to: { x: number; y: number }) {
		const context = surface();
		if (!context || !canvas) return;
		const scale = canvas.width / Math.max(1, canvas.getBoundingClientRect().width);
		drawSegment(context, last ?? to, to, { size: brush * scale, color: tintCss, erase });
		last = to;
	}

	function down(event: PointerEvent) {
		if (disabled || event.button !== 0) return;
		const at = point(event);
		if (!at) return;
		event.preventDefault();
		canvas?.setPointerCapture?.(event.pointerId);
		painting = true;
		last = null;
		stroke(at);
	}

	function move(event: PointerEvent) {
		if (!painting) return;
		const at = point(event);
		if (at) stroke(at);
	}

	function up() {
		if (!painting) return;
		painting = false;
		last = null;
		onstroke?.(true);
	}

	export function hasStrokes(): boolean {
		const context = surface();
		if (!canvas || !context) return false;
		const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
		for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
		return false;
	}

	export function clear() {
		const context = surface();
		if (canvas && context) context.clearRect(0, 0, canvas.width, canvas.height);
	}

	export async function exportMask(name: string): Promise<File | null> {
		const context = surface();
		if (!canvas || !context) return null;
		const data = context.getImageData(0, 0, canvas.width, canvas.height);
		const out = document.createElement('canvas');
		out.width = canvas.width;
		out.height = canvas.height;
		const outContext = out.getContext('2d');
		if (!outContext) return null;
		const mask = outContext.createImageData(out.width, out.height);
		for (let i = 0; i < data.data.length; i += 4) {
			const lit = data.data[i + 3] > 64 ? 255 : 0;
			mask.data[i] = lit;
			mask.data[i + 1] = lit;
			mask.data[i + 2] = lit;
			mask.data[i + 3] = 255;
		}
		outContext.putImageData(mask, 0, 0);
		return canvasToPngFile(out, name);
	}
</script>

<canvas
	bind:this={canvas}
	class="absolute inset-0 h-full w-full touch-none opacity-60 {disabled ? 'pointer-events-none' : 'cursor-crosshair'}"
	aria-label="Mask, paint the area to change"
	onpointerdown={down}
	onpointermove={move}
	onpointerup={up}
	onpointercancel={up}
	data-mask-layer
></canvas>
