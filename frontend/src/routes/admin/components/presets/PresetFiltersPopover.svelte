<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import SelectFilterField from '$lib/components/library/SelectFilterField.svelte';
	import {
		PRESET_ASSIGNMENT_OPTIONS,
		PRESET_INSTALL_OPTIONS,
		PRESET_REQUIREMENTS_OPTIONS,
		clearAllPresetFilters,
		type PresetAssignmentFilter,
		type PresetFilters,
		type PresetInstallFilter,
		type PresetRequirementsFilter
	} from './presetFilters';

	let {
		filters,
		engines,
		onChange,
		onClose
	}: {
		filters: PresetFilters;
		engines: readonly string[];
		onChange: (filters: PresetFilters) => void;
		onClose: () => void;
	} = $props();

	function set(patch: Partial<PresetFilters>) {
		onChange({ ...filters, ...patch });
	}

	const engineOptions = $derived([
		{ value: '', label: 'All engines' },
		...engines.map((engine) => ({ value: engine, label: engine }))
	]);
</script>

<FilterPopoverFrame label="Preset filters" onClearAll={() => onChange(clearAllPresetFilters(filters))} {onClose}>
	<SelectFilterField label="Engine" value={filters.engine} options={engineOptions} onChange={(engine) => set({ engine })} />
	<SegmentedFilterGroup
		label="Status"
		options={PRESET_INSTALL_OPTIONS}
		value={filters.install}
		onChange={(install: PresetInstallFilter) => set({ install })}
	/>
	<SegmentedFilterGroup
		label="Requirements"
		options={PRESET_REQUIREMENTS_OPTIONS}
		value={filters.requirements}
		onChange={(requirements: PresetRequirementsFilter) => set({ requirements })}
	/>
	<SegmentedFilterGroup
		label="Assignment"
		options={PRESET_ASSIGNMENT_OPTIONS}
		value={filters.assignment}
		onChange={(assignment: PresetAssignmentFilter) => set({ assignment })}
	/>
</FilterPopoverFrame>
