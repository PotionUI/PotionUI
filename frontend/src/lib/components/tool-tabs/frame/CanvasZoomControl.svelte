<script lang="ts">
	import { Button, IconButton } from '$lib/components/ui';

	let {
		percent,
		canZoomIn,
		canZoomOut,
		fitted,
		actual,
		onzoomin,
		onzoomout,
		onfit,
		onactual
	}: {
		percent: number;
		canZoomIn: boolean;
		canZoomOut: boolean;
		fitted: boolean;
		actual: boolean;
		onzoomin: () => void;
		onzoomout: () => void;
		onfit: () => void;
		onactual: () => void;
	} = $props();
</script>

<div
	class="absolute right-3 top-3 z-10 flex items-center gap-0.5 rounded-xl border border-scrim-on/10 bg-scrim/70 p-[3px] text-scrim-on shadow-floating backdrop-blur"
	role="group"
	aria-label="Zoom"
	data-canvas-zoom
	data-zoom-corner="top-right"
>
	<IconButton icon="minus" label="Zoom out" size="xs" variant="overlay" class="h-7 w-7" disabled={!canZoomOut} onclick={onzoomout} />
	<span class="min-w-[52px] text-center font-mono text-sm tabular-nums" aria-live="polite" data-canvas-zoom-value>{percent}%</span>
	<IconButton icon="plus" label="Zoom in" size="xs" variant="overlay" class="h-7 w-7" disabled={!canZoomIn} onclick={onzoomin} />
	<span class="mx-[3px] h-4 w-px bg-scrim-on/15" aria-hidden="true"></span>
	<Button variant="overlay" size="xs" class="h-7 font-mono" pressed={fitted} onclick={onfit} attrs={{ 'data-canvas-fit': '' }}>Fit</Button>
	<Button variant="overlay" size="xs" class="h-7 font-mono" pressed={actual} onclick={onactual} attrs={{ 'data-canvas-actual': '' }}>1:1</Button>
</div>
