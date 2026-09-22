<script lang="ts">
	import { Button } from '$lib/components/ui';
	import {
		TEMPLATE_SLOT_OPTIONS,
		clearAllTemplateFilters,
		type TemplateFilters,
		type TemplateSlotsFilter
	} from './templateFilters';

	let {
		filters,
		tags,
		onChange,
		onClose
	}: {
		filters: TemplateFilters;
		tags: ReadonlyArray<{ tag: string; count: number }>;
		onChange: (filters: TemplateFilters) => void;
		onClose: () => void;
	} = $props();

	function set(patch: Partial<TemplateFilters>) {
		onChange({ ...filters, ...patch });
	}

	function toggleTag(tag: string) {
		const next = filters.tags.includes(tag) ? filters.tags.filter((entry) => entry !== tag) : [...filters.tags, tag];
		set({ tags: next });
	}

	function clearAll() {
		onChange(clearAllTemplateFilters(filters));
	}
</script>

<div
	class="w-[30rem] max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating"
	role="dialog"
	aria-label="Template filters"
>
	<div class="mb-3 flex items-center">
		<strong class="text-sm font-semibold text-fg">Filters</strong>
		<button type="button" class="ml-auto text-xs text-fg-subtle hover:text-fg" onclick={clearAll}>Clear all</button>
	</div>

	<div class="grid grid-cols-2 gap-x-4 gap-y-3">
		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Slots</span>
			<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
				{#each TEMPLATE_SLOT_OPTIONS as option (option.value)}
					<button
						type="button"
						class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {filters.slots === option.value
							? 'bg-surface-1 text-fg shadow-raised'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => set({ slots: option.value as TemplateSlotsFilter })}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>

		<div class="col-span-2 flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Tags</span>
			{#if tags.length === 0}
				<p class="text-xs text-fg-subtle">No tags yet.</p>
			{:else}
				<div class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded border border-line-strong bg-surface-2 p-1.5">
					{#each tags as entry (entry.tag)}
						<button
							type="button"
							class="inline-flex h-6 items-center gap-1 rounded border px-1.5 text-xs font-medium {filters.tags.includes(
								entry.tag
							)
								? 'border-signal/28 bg-signal/10 text-signal'
								: 'border-line-strong text-fg-muted hover:text-fg'}"
							aria-pressed={filters.tags.includes(entry.tag)}
							onclick={() => toggleTag(entry.tag)}
						>
							{entry.tag}
							<span class="font-mono tabular-nums opacity-60">{entry.count}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	<div class="mt-3.5 flex items-center justify-between border-t border-line pt-3">
		<span class="text-xs text-fg-subtle">Filters apply as you change them.</span>
		<Button size="xs" variant="secondary" onclick={onClose}>Done</Button>
	</div>
</div>
