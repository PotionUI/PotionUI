<script lang="ts">
	import CanvasChip from '$lib/components/tool-tabs/frame/CanvasChip.svelte';
	import { moveCropRect, resizeCropRect, type CropCorner, type CropRect } from '$lib/media/editors/cropGeometry';

	let {
		rect,
		ratio = null,
		stageAspect,
		label,
		onchange
	}: {
		rect: CropRect;
		ratio?: number | null;
		stageAspect: number;
		label: string;
		onchange: (rect: CropRect) => void;
	} = $props();

	let box = $state<HTMLElement>();

	const CORNERS: { corner: CropCorner; position: string; cursor: string }[] = [
		{ corner: 'nw', position: '-left-[2px] -top-[2px] border-l-2 border-t-2', cursor: 'nwse-resize' },
		{ corner: 'ne', position: '-right-[2px] -top-[2px] border-r-2 border-t-2', cursor: 'nesw-resize' },
		{ corner: 'sw', position: '-bottom-[2px] -left-[2px] border-b-2 border-l-2', cursor: 'nesw-resize' },
		{ corner: 'se', position: '-bottom-[2px] -right-[2px] border-b-2 border-r-2', cursor: 'nwse-resize' }
	];

	let dims = $derived([
		`left:0;top:0;width:100%;height:${rect.y * 100}%`,
		`left:0;top:${(rect.y + rect.height) * 100}%;width:100%;bottom:0`,
		`left:0;top:${rect.y * 100}%;width:${rect.x * 100}%;height:${rect.height * 100}%`,
		`left:${(rect.x + rect.width) * 100}%;top:${rect.y * 100}%;right:0;height:${rect.height * 100}%`
	]);

	function drag(event: PointerEvent, corner: CropCorner | null) {
		if (event.button !== 0 || !box) return;
		event.preventDefault();
		event.stopPropagation();
		const start = rect;
		const x0 = event.clientX;
		const y0 = event.clientY;
		const width = box.clientWidth || 1;
		const height = box.clientHeight || 1;
		const lock = ratio;
		const aspect = stageAspect;
		const move = (next: PointerEvent) => {
			const dx = (next.clientX - x0) / width;
			const dy = (next.clientY - y0) / height;
			onchange(corner ? resizeCropRect(start, corner, dx, dy, { ratio: lock, stageAspect: aspect }) : moveCropRect(start, dx, dy));
		};
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function nudge(event: KeyboardEvent) {
		const step = event.shiftKey ? 0.05 : 0.01;
		const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
		const delta = moves[event.key];
		if (!delta) return;
		event.preventDefault();
		onchange(moveCropRect(rect, delta[0], delta[1]));
	}
</script>

<div bind:this={box} class="absolute inset-0" data-crop-overlay>
	{#each dims as style, i (i)}
		<div class="pointer-events-none absolute bg-scrim/60" {style}></div>
	{/each}
	<div
		class="absolute cursor-move outline outline-1 outline-scrim-on focus-visible:outline-2 focus-visible:outline-signal"
		style="left:{rect.x * 100}%;top:{rect.y * 100}%;width:{rect.width * 100}%;height:{rect.height * 100}%"
		role="slider"
		tabindex="0"
		aria-label="Crop frame, drag to move, arrow keys to nudge"
		aria-valuenow={Math.round(rect.x * 100)}
		onpointerdown={(event) => drag(event, null)}
		onkeydown={nudge}
		data-crop-rect
	>
		<div class="pointer-events-none absolute inset-0">
			<div class="absolute inset-y-0 left-1/3 w-px bg-scrim-on/30"></div>
			<div class="absolute inset-y-0 left-2/3 w-px bg-scrim-on/30"></div>
			<div class="absolute inset-x-0 top-1/3 h-px bg-scrim-on/30"></div>
			<div class="absolute inset-x-0 top-2/3 h-px bg-scrim-on/30"></div>
		</div>
		{#each CORNERS as handle (handle.corner)}
			<button
				type="button"
				class="absolute {handle.position} h-6 w-6 border-scrim-on bg-transparent touch-none focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-signal"
				style="cursor:{handle.cursor}"
				aria-label="Resize from the {handle.corner} corner"
				onpointerdown={(event) => drag(event, handle.corner)}
				data-crop-corner={handle.corner}
			></button>
		{/each}
		<div class="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2">
			<CanvasChip caps={false}>{label}</CanvasChip>
		</div>
	</div>
</div>
