<script lang="ts">
	import CanvasChip from '$lib/components/tool-tabs/frame/CanvasChip.svelte';
	import { moveCropRect, resizeCropRect, type CropCorner, type CropRect } from '$lib/media/editors/cropGeometry';

	let {
		rect,
		ratio = null,
		stageAspect,
		label = null,
		onchange
	}: {
		rect: CropRect;
		ratio?: number | null;
		stageAspect: number;
		label?: string | null;
		onchange: (rect: CropRect) => void;
	} = $props();

	let box = $state<HTMLElement | null>(null);

	const corners: CropCorner[] = ['nw', 'ne', 'sw', 'se'];
	const cornerClass: Record<CropCorner, string> = {
		nw: '-left-0.5 -top-0.5 cursor-nwse-resize border-l-2 border-t-2',
		ne: '-right-0.5 -top-0.5 cursor-nesw-resize border-r-2 border-t-2',
		sw: '-bottom-0.5 -left-0.5 cursor-nesw-resize border-b-2 border-l-2',
		se: '-bottom-0.5 -right-0.5 cursor-nwse-resize border-b-2 border-r-2'
	};

	function start(event: PointerEvent, corner: CropCorner | null) {
		if (event.button !== 0 || !box) return;
		event.preventDefault();
		event.stopPropagation();
		const bounds = box.getBoundingClientRect();
		if (bounds.width <= 0 || bounds.height <= 0) return;
		const origin = { x: event.clientX, y: event.clientY };
		const from = rect;
		const target = event.currentTarget as HTMLElement;
		target.setPointerCapture?.(event.pointerId);
		const move = (next: PointerEvent) => {
			const dx = (next.clientX - origin.x) / bounds.width;
			const dy = (next.clientY - origin.y) / bounds.height;
			onchange(corner ? resizeCropRect(from, corner, dx, dy, { ratio, stageAspect }) : moveCropRect(from, dx, dy));
		};
		const up = () => {
			target.removeEventListener('pointermove', move);
			target.removeEventListener('pointerup', up);
			target.removeEventListener('pointercancel', up);
		};
		target.addEventListener('pointermove', move);
		target.addEventListener('pointerup', up);
		target.addEventListener('pointercancel', up);
	}
</script>

<div bind:this={box} class="absolute inset-0" data-crop-overlay>
	<div class="pointer-events-none absolute inset-x-0 top-0 bg-scrim/60" style="height: {rect.y * 100}%"></div>
	<div class="pointer-events-none absolute inset-x-0 bottom-0 bg-scrim/60" style="height: {(1 - rect.y - rect.height) * 100}%"></div>
	<div class="pointer-events-none absolute left-0 bg-scrim/60" style="top: {rect.y * 100}%; height: {rect.height * 100}%; width: {rect.x * 100}%"></div>
	<div class="pointer-events-none absolute right-0 bg-scrim/60" style="top: {rect.y * 100}%; height: {rect.height * 100}%; width: {(1 - rect.x - rect.width) * 100}%"></div>
	<div
		class="absolute cursor-move border border-scrim-on"
		style="left: {rect.x * 100}%; top: {rect.y * 100}%; width: {rect.width * 100}%; height: {rect.height * 100}%;"
		role="presentation"
		onpointerdown={(event) => start(event, null)}
		data-crop-rect
	>
		<div class="pointer-events-none absolute inset-0 grid grid-cols-3 grid-rows-3">
			{#each Array(9) as _, i (i)}
				<span class="border-scrim-on/25 {i % 3 < 2 ? 'border-r' : ''} {i < 6 ? 'border-b' : ''}"></span>
			{/each}
		</div>
		{#each corners as corner (corner)}
			<span
				class="absolute h-6 w-6 touch-none border-scrim-on {cornerClass[corner]}"
				role="presentation"
				onpointerdown={(event) => start(event, corner)}
				data-crop-corner={corner}
			></span>
		{/each}
		{#if label}
			<div class="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2">
				<CanvasChip caps={false}>{label}</CanvasChip>
			</div>
		{/if}
	</div>
</div>
