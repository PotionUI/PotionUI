<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { CategoryFilters, CategoryHasFilter } from './categoryFilters';

	let {
		filters,
		onChange,
		onClose
	}: {
		filters: CategoryFilters;
		onChange: (filters: CategoryFilters) => void;
		onClose: () => void;
	} = $props();

	const hasOptions: Array<{ value: CategoryHasFilter; label: string }> = [
		{ value: '', label: 'Any' },
		{ value: 'segments', label: 'With segments' },
		{ value: 'empty', label: 'Empty' }
	];

	function set(patch: Partial<CategoryFilters>) {
		onChange({ ...filters, ...patch });
	}
</script>

<div
	class="w-[30rem] max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating"
	role="dialog"
	aria-label="Filters"
>
	<div class="mb-3 flex items-center">
		<strong class="text-sm font-semibold text-fg">Filters</strong>
		<button type="button" class="ml-auto text-xs text-fg-subtle hover:text-fg" onclick={() => set({ has: '' })}>
			Clear all
		</button>
	</div>

	<div class="grid grid-cols-2 gap-x-4 gap-y-3">
		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Segments</span>
			<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
				{#each hasOptions as option (option.value)}
					<button
						type="button"
						class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {filters.has === option.value
							? 'bg-surface-1 text-fg shadow-raised'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => set({ has: option.value })}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>
	</div>

	<div class="mt-3.5 flex items-center justify-between border-t border-line pt-3">
		<span class="text-xs text-fg-subtle">Filters apply as you change them.</span>
		<Button size="xs" variant="secondary" onclick={onClose}>Done</Button>
	</div>
</div>
