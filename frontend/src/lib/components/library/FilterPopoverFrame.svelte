<script lang="ts">
	import { getContext, type Snippet } from 'svelte';
	import { Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { ACTIVE_FILTERS_KEY, type ActiveFiltersContext } from './activeFilters';

	let {
		label = 'Filters',
		width = 'w-[30rem]',
		onClearAll,
		onClose,
		children
	}: {
		label?: string;
		width?: string;
		onClearAll: () => void;
		onClose: () => void;
		children: Snippet;
	} = $props();

	const active = getContext<ActiveFiltersContext | undefined>(ACTIVE_FILTERS_KEY);
	const activeChips = $derived(active?.chips() ?? []);
</script>

<div class="{width} max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating" role="dialog" aria-label={label}>
	<div class="mb-3 flex items-center">
		<strong class="text-sm font-semibold text-fg">Filters</strong>
		<button type="button" class="ml-auto text-xs text-fg-subtle hover:text-fg" onclick={onClearAll}>Clear all</button>
	</div>

	{#if active && activeChips.length > 0}
		<div class="mb-3 border-b border-line pb-3">
			<span class="mb-1.5 block text-sm uppercase tracking-[0.07em] text-fg-subtle">Active</span>
			<div class="flex flex-wrap gap-1.5">
				{#each activeChips as chip (chip.key)}
					<span class="inline-flex h-6 max-w-full items-center gap-1 rounded border border-signal/25 bg-signal/10 pl-2 pr-1 text-sm text-signal">
						<span class="truncate">{chip.label}</span>
						<button
							type="button"
							class="inline-flex h-4 w-4 flex-none items-center justify-center rounded-sm hover:bg-signal/10"
							aria-label="Remove {chip.label}"
							onclick={() => active.remove(chip.key)}
						>
							<Icon name="close" className="h-3 w-3" />
						</button>
					</span>
				{/each}
			</div>
		</div>
	{/if}

	<div class="grid grid-cols-2 gap-x-4 gap-y-3">
		{@render children()}
	</div>

	<div class="mt-3.5 flex items-center justify-between border-t border-line pt-3">
		<span class="text-xs text-fg-subtle">Filters apply as you change them.</span>
		<Button size="xs" variant="secondary" onclick={onClose}>Done</Button>
	</div>
</div>
