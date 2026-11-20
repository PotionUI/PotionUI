<script lang="ts">
	import type { SegmentCategory } from '$lib/types/segments';
	import HeaderDropdown from '$lib/components/page-header/HeaderDropdown.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import TagCloudFilter from '$lib/components/library/TagCloudFilter.svelte';
	import { type SegmentEnabledFilter, type SegmentFilters, segmentFilters } from './segmentFilters';

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

	const enabledOptions: Array<{ value: SegmentEnabledFilter; label: string }> = [
		{ value: '', label: 'Any' },
		{ value: 'on', label: 'Enabled' },
		{ value: 'off', label: 'Disabled' }
	];

	const categoryOptions = $derived([
		{ value: '', label: 'Any' },
		...categories.map((category) => ({ value: category.id, label: category.name }))
	]);

	function set(patch: Partial<SegmentFilters>) {
		onChange({ ...filters, ...patch });
	}

	function toggleTag(tag: string) {
		const next = filters.tags.includes(tag) ? filters.tags.filter((entry) => entry !== tag) : [...filters.tags, tag];
		set({ tags: next });
	}
</script>

<FilterPopoverFrame onClearAll={() => onChange(segmentFilters.clearAll(filters))} {onClose}>
	<div class="flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">Category</span>
		<HeaderDropdown
			block
			ariaLabel="Category"
			value={filters.category}
			options={categoryOptions}
			onchange={(category) => set({ category })}
		/>
	</div>

	<SegmentedFilterGroup
		label="Enabled"
		options={enabledOptions}
		value={filters.enabled}
		onChange={(enabled: SegmentEnabledFilter) => set({ enabled })}
	/>

	<TagCloudFilter tags={tags} selected={filters.tags} onToggle={toggleTag} />
</FilterPopoverFrame>
