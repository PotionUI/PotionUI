<script lang="ts">
	import { buildTicks, RULER_H } from '../timelineCore';

	let {
		totalSeconds,
		zoom
	}: {
		totalSeconds: number;
		zoom: number;
	} = $props();

	let ticks = $derived(buildTicks(totalSeconds, zoom));
</script>

<div class="relative border-b border-line" style="height: {RULER_H}px">
	{#each ticks as tick (tick.time)}
		<div class="absolute bottom-0 flex flex-col items-start" style="left: {tick.time * zoom}px">
			<span class="mb-1 h-1.5 w-px bg-line-strong"></span>
			<span class="absolute bottom-1.5 -translate-x-1/2 whitespace-nowrap font-mono text-2xs tabular-nums text-fg-subtle">
				{tick.label}
			</span>
		</div>
	{/each}
</div>
