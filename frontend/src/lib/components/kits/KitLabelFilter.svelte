<script lang="ts">
	import type { KitLabelCount } from '$lib/kits/types';

	let {
		labels,
		total,
		value,
		onChange
	}: {
		labels: KitLabelCount[];
		total: number;
		value: string | null;
		onChange: (label: string | null) => void;
	} = $props();

	const base = 'flex items-center gap-1.5 whitespace-nowrap rounded-sm px-2 py-1 text-sm transition-colors duration-100';
</script>

<div class="flex items-center gap-0.5 rounded bg-surface-2/50 p-0.5" role="group" aria-label="Label" data-kit-label-filter>
	<button
		type="button"
		aria-pressed={value === null}
		class="{base} {value === null ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
		onclick={() => onChange(null)}
	>
		All<span class="font-mono tabular-nums">{total}</span>
	</button>
	{#each labels as entry (entry.label)}
		<button
			type="button"
			aria-pressed={value === entry.label}
			class="{base} {value === entry.label ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
			onclick={() => onChange(entry.label)}
		>
			{entry.label}{#if entry.count !== null}<span class="font-mono tabular-nums">{entry.count}</span>{/if}
		</button>
	{/each}
</div>
