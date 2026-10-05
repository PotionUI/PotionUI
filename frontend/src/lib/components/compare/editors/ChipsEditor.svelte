<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import {
		allChips,
		chipOptionsFor,
		isChipSelected,
		toggleChip
	} from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		candidate,
		axis,
		onChange
	}: {
		candidate: AxisCandidate;
		axis: CompareAxis | null;
		tabId?: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let options = $derived(chipOptionsFor(candidate.type, candidate.options));
	let selected = $derived(axis?.values ?? []);
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center gap-1.5">
		<button
			type="button"
			class="px-2 py-0.5 text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline"
			onclick={() => onChange(allChips(options))}
		>
			All
		</button>
		{#each options as option, index (index)}
			{@const on = isChipSelected(selected, option.value)}
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-xs transition-colors {on
					? 'border-signal/40 bg-signal/10 text-signal'
					: 'border-line bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
				aria-pressed={on}
				onclick={() => onChange(toggleChip(selected, options, option.value))}
			>
				{#if on}<Icon name="check" className="h-3 w-3" />{/if}
				{option.label}
			</button>
		{/each}
	</div>
	<p class="font-mono text-xs uppercase tracking-wide text-fg-subtle" data-testid="chips-count">
		{selected.length} of {options.length}
	</p>
</div>
