<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	let { session, state: snapshot }: { session: PaintSession; state: SessionSnapshot } = $props();

	let docWidth = $derived(snapshot.width);
	let docHeight = $derived(snapshot.height);
	let width = $state(0);
	let height = $state(0);

	$effect(() => {
		width = docWidth;
		height = docHeight;
	});
</script>

<div class="flex flex-col gap-3" data-canvas-size>
	<div class="flex items-baseline justify-between">
		<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Canvas</span>
		<span class="font-mono text-sm tabular-nums text-fg-muted">{snapshot.width} × {snapshot.height}</span>
	</div>
	<div class="flex items-center gap-1.5">
		<input type="number" min="16" max="4096" bind:value={width} aria-label="Canvas width" class="input min-w-0 flex-1 font-mono tabular-nums" />
		<span class="text-fg-subtle">×</span>
		<input type="number" min="16" max="4096" bind:value={height} aria-label="Canvas height" class="input min-w-0 flex-1 font-mono tabular-nums" />
	</div>
	<div class="flex items-center gap-2">
		<Button variant="secondary" size="sm" class="flex-1" onclick={() => session.resizeCanvas('scale', width, height)}>Scale</Button>
		<Button variant="secondary" size="sm" class="flex-1" onclick={() => session.resizeCanvas('extend', width, height)}>Extend</Button>
	</div>
	<p class="text-sm text-fg-subtle">Scale resizes the picture with the canvas. Extend adds or removes space around it. Both change the image size.</p>
</div>
