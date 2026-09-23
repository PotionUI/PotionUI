<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { clearAllCategoryFilters, type CategoryFilters, type CategoryHasFilter } from './categoryFilters';

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
</script>

<FilterPopoverFrame onClearAll={() => onChange(clearAllCategoryFilters(filters))} {onClose}>
	<SegmentedFilterGroup label="Segments" options={hasOptions} value={filters.has} onChange={(has) => onChange({ ...filters, has })} />
</FilterPopoverFrame>
