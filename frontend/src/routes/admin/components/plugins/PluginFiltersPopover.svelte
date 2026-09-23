<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { PLUGIN_STATE_OPTIONS, PLUGIN_TYPE_OPTIONS, clearAllPluginFilters, type PluginFilters } from './pluginFilters';

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

	<div class="flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">Type</span>
		<select
			class="input h-8 text-xs"
			value={filters.type}
			onchange={(event) =>
				onChange({ ...filters, type: (event.currentTarget as HTMLSelectElement).value as PluginFilters['type'] })}
		>
			{#each PLUGIN_TYPE_OPTIONS as option (option.value)}
				<option value={option.value}>{option.label}</option>
			{/each}
		</select>
	</div>
</FilterPopoverFrame>
