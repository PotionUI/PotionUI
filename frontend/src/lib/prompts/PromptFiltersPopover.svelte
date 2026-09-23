<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import Icon from '$lib/components/Icon.svelte';
	import { Switch } from '$lib/components/ui';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import TagCloudFilter from '$lib/components/library/TagCloudFilter.svelte';
	import { clearAllPromptFilters, type PromptFilters, type PromptLastUsedFilter, type PromptSourceFilter, type PromptUsedFilter } from './promptFilters';

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

<FilterPopoverFrame width="w-[34rem]" onClearAll={() => onChange(clearAllPromptFilters(filters))} {onClose}>
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

	<SegmentedFilterGroup label="Usage hint" options={usageHintOptions} value={filters.usageHint} onChange={(usageHint) => set({ usageHint })} />
	<SegmentedFilterGroup label="Source" options={sourceOptions} value={filters.source} onChange={(source) => set({ source })} />
	<SegmentedFilterGroup label="Used in generations" options={usedOptions} value={filters.used} onChange={(used) => set({ used })} />

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

	<TagCloudFilter tags={tags} selected={filters.tags} onToggle={toggleTag} />

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
</FilterPopoverFrame>
