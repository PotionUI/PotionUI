<script lang="ts">
	import { canvasFromUrl } from '$lib/paint/loadPicked';
	import { parseRgb } from '$lib/tool-tabs/image/mask';
	import { maskOutline } from '$lib/tool-tabs/image/maskOutline';
	import type { PatchMask } from '$lib/tool-tabs/image/layers';

	const WORKING = 960;

	let { mask }: { mask: PatchMask } = $props();

	let canvas = $state<HTMLCanvasElement>();

	function signal(): [number, number, number] {
		const raw = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
		return parseRgb(raw, [91, 157, 255]);
	}

	$effect(() => {
		const target = canvas;
		const url = mask.url;
		if (!target || !url) return;
		let live = true;
		void canvasFromUrl(url)
			.then((image) => {
				if (!live) return;
				const scale = Math.min(1, WORKING / Math.max(image.width, image.height));
				const width = Math.max(1, Math.round(image.width * scale));
				const height = Math.max(1, Math.round(image.height * scale));
				const work = document.createElement('canvas');
				work.width = width;
				work.height = height;
				const context = work.getContext('2d', { willReadFrequently: true });
				const out = target.getContext('2d');
				if (!context || !out) return;
				context.drawImage(image, 0, 0, width, height);
				const pixels = context.getImageData(0, 0, width, height).data;
				target.width = width;
				target.height = height;
				out.putImageData(new ImageData(maskOutline(pixels, width, height, signal()), width, height), 0, 0);
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});
</script>

{#if mask.url}
	<canvas bind:this={canvas} class="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true" data-patch-mask-overlay></canvas>
{:else}
	<div
		class="pointer-events-none absolute border-2 border-dashed border-signal bg-signal/[0.12]"
		style="left: {mask.rect.x * 100}%; top: {mask.rect.y * 100}%; width: {mask.rect.width * 100}%; height: {mask.rect.height * 100}%"
		aria-hidden="true"
		data-patch-mask-overlay
	></div>
{/if}
