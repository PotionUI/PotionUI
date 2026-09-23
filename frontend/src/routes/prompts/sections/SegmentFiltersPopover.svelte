<script lang="ts">
	import type { SegmentCategory } from '$lib/types/segments';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import TagCloudFilter from '$lib/components/library/TagCloudFilter.svelte';
	import {
		clearAllSegmentFilters,
		type SegmentEnabledFilter,
		type SegmentFilters,
		type SegmentTypeFilter
	} from './segmentFilters';

	let {
		filters,
		categories,
		tags,
		onChange,
		onClose
	}: {
		filters: SegmentFilters;
		categories: readonly SegmentCategory[];
		tags: ReadonlyArray<{ tag: string; count: number }>;
		onChange: (filters: SegmentFilters) => void;
		onClose: () => void;
	} = $props();

	const typeOptions: Array<{ value: SegmentTypeFilter; label: string }> = [
		{ value: '', label: 'Any' },
		{ value: 'content', label: 'Content' },
		{ value: 'break', label: 'Break' }
	];
	const enabledOptions: Array<{ value: SegmentEnabledFilter; label: string }> = [
		{ value: '', label: 'Any' },
		{ value: 'on', label: 'Enabled' },
		{ value: 'off', label: 'Disabled' }
	];

	function set(patch: Partial<SegmentFilters>) {
		onChange({ ...filters, ...patch });
	}

	function toggleTag(tag: string) {
		const next = filters.tags.includes(tag) ? filters.tags.filter((entry) => entry !== tag) : [...filters.tags, tag];
		set({ tags: next });
	}
</script>

<FilterPopoverFrame onClearAll={() => onChange(clearAllSegmentFilters(filters))} {onClose}>
	<SegmentedFilterGroup label="Type" options={typeOptions} value={filters.type} onChange={(type: SegmentTypeFilter) => set({ type })} />

	<label class="flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">Category</span>
		<select
			class="input text-xs"
			value={filters.category}
			onchange={(event) => set({ category: (event.currentTarget as HTMLSelectElement).value })}
		>
			<option value="">Any</option>
			{#each categories as category (category.id)}
				<option value={category.id}>{category.name}</option>
			{/each}
		</select>
	</label>

	<SegmentedFilterGroup
		label="Enabled"
		options={enabledOptions}
		value={filters.enabled}
		onChange={(enabled: SegmentEnabledFilter) => set({ enabled })}
	/>

	<TagCloudFilter tags={tags} selected={filters.tags} onToggle={toggleTag} />
</FilterPopoverFrame>
