<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Switch } from '$lib/components/ui';
	import type { PromptFilters, PromptLastUsedFilter, PromptSourceFilter, PromptUsedFilter } from './promptFilters';

	let {
		filters,
		modelLabel = 'Any',
		onChange,
		onOpenModelPicker,
		onClose
	}: {
		filters: PromptFilters;
		modelLabel?: string;
		onChange: (filters: PromptFilters) => void;
		onOpenModelPicker: () => void;
		onClose: () => void;
	} = $props();

	let tags = $state<Array<{ tag: string; count: number }>>([]);

	onMount(async () => {
		try {
			const response = await api.listPromptTags();
			if (response.success && response.data) tags = response.data.tags;
		} catch {
			tags = [];
		}
	});

	function set(patch: Partial<PromptFilters>) {
		onChange({ ...filters, ...patch });
	}

	function toggleTag(tag: string) {
		const next = filters.tags.includes(tag) ? filters.tags.filter((t) => t !== tag) : [...filters.tags, tag];
		set({ tags: next });
	}

	function clearAll() {
		onChange({ ...filters, modelId: '', baseModel: '', usageHint: '', source: 'any', used: 'any', lastUsed: '', tags: [], hasVariables: false, showNsfw: false });
	}

	const usageHintOptions: Array<{ value: '' | 'positive' | 'negative'; label: string }> = [
		{ value: '', label: 'Any' },
		{ value: 'positive', label: 'Positive' },
		{ value: 'negative', label: 'Negative' }
	];
	const sourceOptions: Array<{ value: PromptSourceFilter; label: string }> = [
		{ value: 'any', label: 'Any' },
		{ value: 'mine', label: 'Mine' },
		{ value: 'civitai', label: 'CivitAI' }
	];
	const usedOptions: Array<{ value: PromptUsedFilter; label: string }> = [
		{ value: 'any', label: 'Any' },
		{ value: 'used', label: '≥ 1' },
		{ value: 'never', label: 'Never' }
	];
	const lastUsedOptions: Array<{ value: PromptLastUsedFilter; label: string }> = [
		{ value: '', label: 'Any time' },
		{ value: '24h', label: 'Last 24h' },
		{ value: '7d', label: 'Last 7 days' },
		{ value: '30d', label: 'Last 30 days' }
	];
</script>

<div
	class="w-[34rem] max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating"
	role="dialog"
	aria-label="Filters"
>
	<div class="mb-3 flex items-center">
		<strong class="text-sm font-semibold text-fg">Filters</strong>
		<button type="button" class="ml-auto text-xs text-fg-subtle hover:text-fg" onclick={clearAll}>
			Clear all
		</button>
	</div>

	<div class="grid grid-cols-2 gap-x-4 gap-y-3">
		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Model</span>
			<button type="button" class="input flex items-center gap-2 text-left text-xs" onclick={onOpenModelPicker}>
				<span class="min-w-0 flex-1 truncate">{modelLabel}</span>
				<Icon name="chevron-down" className="h-3 w-3 flex-shrink-0 text-fg-subtle" />
			</button>
		</label>

		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Base model</span>
			<input
				type="text"
				class="input text-xs"
				placeholder="Any"
				value={filters.baseModel}
				oninput={(e) => set({ baseModel: (e.currentTarget as HTMLInputElement).value })}
			/>
		</label>

		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Usage hint</span>
			<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
				{#each usageHintOptions as option (option.value)}
					<button
						type="button"
						class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {filters.usageHint ===
						option.value
							? 'bg-surface-1 text-fg shadow-raised'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => set({ usageHint: option.value })}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>

		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Source</span>
			<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
				{#each sourceOptions as option (option.value)}
					<button
						type="button"
						class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {filters.source ===
						option.value
							? 'bg-surface-1 text-fg shadow-raised'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => set({ source: option.value })}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>

		<div class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Used in generations</span>
			<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
				{#each usedOptions as option (option.value)}
					<button
						type="button"
						class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {filters.used ===
						option.value
							? 'bg-surface-1 text-fg shadow-raised'
							: 'text-fg-muted hover:text-fg'}"
						onclick={() => set({ used: option.value })}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>

		<label class="flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Last used</span>
			<select
				class="input text-xs"
				value={filters.lastUsed}
				onchange={(e) => set({ lastUsed: (e.currentTarget as HTMLSelectElement).value as PromptLastUsedFilter })}
			>
				{#each lastUsedOptions as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
		</label>

		<div class="col-span-2 flex flex-col gap-1.5">
			<span class="text-xs font-medium text-fg-muted">Tags</span>
			{#if tags.length === 0}
				<p class="text-xs text-fg-subtle">No tags yet.</p>
			{:else}
				<div class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded border border-line-strong bg-surface-2 p-1.5">
					{#each tags as entry (entry.tag)}
						<button
							type="button"
							class="inline-flex h-5 items-center gap-1 rounded border px-1.5 text-xs font-medium {filters.tags.includes(
								entry.tag
							)
								? 'border-signal/28 bg-signal/10 text-signal'
								: 'border-line-strong text-fg-muted hover:text-fg'}"
							onclick={() => toggleTag(entry.tag)}
						>
							{entry.tag}
							<span class="font-mono tabular-nums opacity-60">{entry.count}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<div class="flex items-center gap-2">
			<Switch
				size="sm"
				label="Has variables"
				checked={filters.hasVariables}
				onchange={(checked) => set({ hasVariables: checked })}
			/>
			<span class="text-xs text-fg">Has variables</span>
		</div>

		<div class="flex items-center gap-2">
			<Switch size="sm" label="Show NSFW" checked={filters.showNsfw} onchange={(checked) => set({ showNsfw: checked })} />
			<span class="text-xs text-fg">Show NSFW</span>
		</div>
	</div>

	<div class="mt-3.5 flex items-center justify-between border-t border-line pt-3">
		<span class="text-xs text-fg-subtle">Filters apply as you change them.</span>
		<Button size="xs" variant="secondary" onclick={onClose}>Done</Button>
	</div>
</div>
