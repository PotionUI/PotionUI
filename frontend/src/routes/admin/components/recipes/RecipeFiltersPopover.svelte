<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { clearAllRecipeFilters, type RecipeFilters } from './recipeFilters';

	let {
		filters,
		sources,
		engines,
		onChange,
		onClose
	}: {
		filters: RecipeFilters;
		sources: readonly string[];
		engines: readonly string[];
		onChange: (filters: RecipeFilters) => void;
		onClose: () => void;
	} = $props();

	const sourceOptions = $derived([
		{ value: '', label: 'All' },
		...sources.map((source) => ({ value: source, label: source }))
	]);
	const engineOptions = $derived([
		{ value: '', label: 'All' },
		...engines.map((engine) => ({ value: engine, label: engine }))
	]);

	function set(patch: Partial<RecipeFilters>) {
		onChange({ ...filters, ...patch });
	}
</script>

<FilterPopoverFrame label="Recipe filters" onClearAll={() => onChange(clearAllRecipeFilters(filters))} {onClose}>
	<SegmentedFilterGroup label="Source" options={sourceOptions} value={filters.source} onChange={(source: string) => set({ source })} />
	<SegmentedFilterGroup label="Engine" options={engineOptions} value={filters.engine} onChange={(engine: string) => set({ engine })} />
</FilterPopoverFrame>
