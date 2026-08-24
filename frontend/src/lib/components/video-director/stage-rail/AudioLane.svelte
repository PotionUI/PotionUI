<script lang="ts">
	import type { RailModel } from './railModel';
	import { railSelection, selectRailObject, isRailObjectSelected } from './railSelection';

	const LANE_HEIGHT = 30;

	let {
		model,
		zoom
	}: {
		model: RailModel;
		zoom: number;
	} = $props();
</script>

<div class="relative rounded bg-canvas ring-1 ring-inset ring-surface-2" style="height: {LANE_HEIGHT}px">
	{#each model.audio as clip (clip.id)}
		{@const selected = isRailObjectSelected($railSelection, 'audio', clip.id)}
		{@const left = clip.startSeconds * zoom}
		{@const width = Math.max(3, (clip.endSeconds - clip.startSeconds) * zoom)}
		<button
			type="button"
			class="absolute top-0 flex items-center gap-1.5 overflow-hidden rounded px-2 text-left transition-colors
				{selected
					? 'bg-signal/10 ring-1 ring-inset ring-signal'
					: clip.hasMedia
						? 'bg-surface-1 ring-1 ring-inset ring-line-strong hover:bg-surface-2'
						: 'text-fg-subtle ring-1 ring-inset ring-dashed ring-line-strong hover:text-signal hover:ring-signal'}"
			style="left: {left}px; width: {width}px; height: {LANE_HEIGHT}px"
			onclick={() => selectRailObject('audio', clip.id)}
		>
			<span class="truncate font-mono text-2xs uppercase tracking-wide text-fg-muted">
				{clip.hasMedia ? (clip.role ?? 'condition') : 'empty'}
			</span>
		</button>
	{/each}
</div>
