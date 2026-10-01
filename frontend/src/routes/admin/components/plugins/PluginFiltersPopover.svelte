<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { PLUGIN_STATE_OPTIONS, clearAllPluginFilters, type PluginFilters } from './pluginFilters';

	let {
		filters,
		onChange,
		onClose
	}: {
		filters: PluginFilters;
		onChange: (filters: PluginFilters) => void;
		onClose: () => void;
	} = $props();
</script>

<FilterPopoverFrame onClearAll={() => onChange(clearAllPluginFilters(filters))} {onClose}>
	<SegmentedFilterGroup
		label="State"
		options={PLUGIN_STATE_OPTIONS}
		value={filters.state}
		onChange={(state) => onChange({ ...filters, state })}
	/>
</FilterPopoverFrame>
