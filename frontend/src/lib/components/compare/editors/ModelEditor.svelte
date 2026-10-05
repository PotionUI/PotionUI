<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import ModelPickerDropdown from './ModelPickerDropdown.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { refFor } from '$lib/utils/modelRef';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { MAX_AXIS_VALUES, type AxisCandidate, type CompareAxis, type CompareAxisValue } from '$lib/generation/compare/types';

	let {
		candidate,
		axis,
		tabId,
		onChange
	}: {
		candidate: AxisCandidate;
		axis: CompareAxis | null;
		tabId: string;
		onChange: (values: CompareAxisValue[]) => void;
	} = $props();

	let values = $derived(axis?.values ?? []);
	let presetId = $derived($tabsStore.tabs.find((tab) => tab.id === tabId)?.selectedPreset ?? '');
	let chosen = $derived(new Set(values.map((entry) => String(entry.value))));
	let paths = $state<Record<string, string>>({});

	function add(model: any) {
		const ref = refFor(model);
		if (!ref || chosen.has(ref) || values.length >= MAX_AXIS_VALUES) return;
		paths = { ...paths, [ref]: String(model.filename ?? model.path ?? '') };
		onChange([...values, { value: ref, label: modelDisplayName(model) || String(model.filename ?? ref) }]);
	}

	function remove(index: number) {
		onChange(values.filter((_, i) => i !== index));
	}
</script>

<div class="space-y-2">
	{#each values as entry, index (String(entry.value))}
		<div class="flex items-center gap-2 rounded border border-line bg-surface-2 px-2.5 py-1.5">
			<div class="min-w-0 flex-1">
				<p class="truncate text-sm font-semibold text-fg">{entry.label}</p>
				{#if paths[String(entry.value)]}
					<p class="truncate font-mono text-xs text-fg-subtle">{paths[String(entry.value)]}</p>
				{/if}
			</div>
			<button type="button" class="p-1 text-fg-subtle hover:text-fg" aria-label="Remove {entry.label}" onclick={() => remove(index)}>
				<Icon name="close" className="h-3.5 w-3.5" />
			</button>
		</div>
	{/each}
	<ModelPickerDropdown
		modelType={candidate.modelType ?? 'checkpoint'}
		{presetId}
		label="Add model"
		disabled={values.length >= MAX_AXIS_VALUES}
		onSelect={add}
	/>
	<p class="text-xs text-fg-muted">Cells run grouped by model, so each model loads once.</p>
</div>
