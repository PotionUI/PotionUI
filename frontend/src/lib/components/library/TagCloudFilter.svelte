<script lang="ts">
	let {
		label = 'Tags',
		tags,
		selected,
		onToggle,
		emptyLabel = 'No tags yet.'
	}: {
		label?: string;
		tags: ReadonlyArray<{ tag: string; count: number }>;
		selected: readonly string[];
		onToggle: (tag: string) => void;
		emptyLabel?: string;
	} = $props();
</script>

<div class="col-span-2 flex flex-col gap-1.5">
	<span class="text-xs font-medium text-fg-muted">{label}</span>
	{#if tags.length === 0}
		<p class="text-xs text-fg-subtle">{emptyLabel}</p>
	{:else}
		<div class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded border border-line-strong bg-surface-2 p-1.5">
			{#each tags as entry (entry.tag)}
				<button
					type="button"
					class="inline-flex h-6 items-center gap-1 rounded border px-1.5 text-xs font-medium {selected.includes(entry.tag)
						? 'border-signal/28 bg-signal/10 text-signal'
						: 'border-line-strong text-fg-muted hover:text-fg'}"
					aria-pressed={selected.includes(entry.tag)}
					onclick={() => onToggle(entry.tag)}
				>
					{entry.tag}
					<span class="font-mono tabular-nums opacity-60">{entry.count}</span>
				</button>
			{/each}
		</div>
	{/if}
</div>
