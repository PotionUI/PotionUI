<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { PickerEntry } from './pickerEntries';

	let { entry }: { entry: PickerEntry } = $props();

	let failed = $state(false);
	let bars = Array.from({ length: 28 }, (_, i) => 6 + Math.round(Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.6)) * 22));

	$effect(() => {
		entry.thumb;
		failed = false;
	});
</script>

{#if entry.kind === 'text'}
	<span class="absolute inset-0 flex flex-col gap-1.5 bg-surface-2 p-3 text-fg-muted">
		<Icon name="document" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
		<span class="line-clamp-4 text-sm">{entry.text}</span>
	</span>
{:else if entry.kind === 'audio'}
	<span class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-surface-2 text-fg-subtle">
		<Icon name="audio" className="h-5 w-5" />
		<span class="flex h-8 items-center gap-[3px]" aria-hidden="true">
			{#each bars as height, i (i)}
				<i class="inline-block w-[3px] rounded-[1.5px] bg-fg-subtle" style="height:{height}px"></i>
			{/each}
		</span>
	</span>
{:else if entry.thumb && !failed}
	<img
		src={entry.thumb}
		alt=""
		loading="lazy"
		class="absolute inset-0 h-full w-full object-cover"
		onerror={() => (failed = true)}
	/>
	{#if entry.kind === 'video'}
		<span class="absolute left-1.5 top-1.5 inline-flex h-6 items-center gap-1 rounded bg-scrim/60 px-1.5 text-scrim-on backdrop-blur-sm">
			<Icon name="video" className="h-3.5 w-3.5" />
		</span>
	{/if}
{:else}
	<span class="absolute inset-0 flex items-center justify-center bg-surface-2 text-fg-subtle">
		<Icon name={entry.kind === 'video' ? 'video' : entry.kind === 'mesh' ? 'cube' : 'image'} className="h-6 w-6" />
	</span>
{/if}

{#if entry.duration && (entry.kind === 'video' || entry.kind === 'audio')}
	<span class="absolute bottom-1.5 right-1.5 rounded bg-scrim/60 px-1.5 font-mono text-sm tabular-nums text-scrim-on backdrop-blur-sm">
		{entry.duration}
	</span>
{/if}
