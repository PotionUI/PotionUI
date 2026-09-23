<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import TagCloudFilter from '$lib/components/library/TagCloudFilter.svelte';
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
</script>

<FilterPopoverFrame label="Template filters" onClearAll={() => onChange(clearAllTemplateFilters(filters))} {onClose}>
	<SegmentedFilterGroup
		label="Slots"
		options={TEMPLATE_SLOT_OPTIONS}
		value={filters.slots}
		onChange={(slots: TemplateSlotsFilter) => set({ slots })}
	/>
	<TagCloudFilter tags={tags} selected={filters.tags} onToggle={toggleTag} />
</FilterPopoverFrame>
