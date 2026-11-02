<script lang="ts">
	import { moveCropRect, resizeCropRect, type CropCorner, type CropRect } from '$lib/media/editors/cropGeometry';

	let {
		rect,
		ratio = null,
		stageAspect,
		onchange
	}: {
		rect: CropRect;
		ratio?: number | null;
		stageAspect: number;
		onchange: (rect: CropRect) => void;
	} = $props();

	let box = $state<HTMLElement | null>(null);

	const corners: CropCorner[] = ['nw', 'ne', 'sw', 'se'];
	const cornerClass: Record<CropCorner, string> = {
		nw: '-left-1.5 -top-1.5 cursor-nwse-resize',
		ne: '-right-1.5 -top-1.5 cursor-nesw-resize',
		sw: '-bottom-1.5 -left-1.5 cursor-nesw-resize',
		se: '-bottom-1.5 -right-1.5 cursor-nwse-resize'
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
				class="absolute h-3 w-3 rounded-sm border border-scrim bg-scrim-on {cornerClass[corner]}"
				role="presentation"
				onpointerdown={(event) => start(event, corner)}
				data-crop-corner={corner}
			></span>
		{/each}
	</div>
</div>
