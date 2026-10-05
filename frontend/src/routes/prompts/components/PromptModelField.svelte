<script lang="ts">
	import { api } from '$lib/services/api';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import { ModelPickTrigger } from '$lib/components/ui';
	import { modelDisplayName } from '$lib/utils/modelDisplay';

	export let modelId: string | null = null;
	/** Display fallback when the prompt already carries a `model_name`. */
	export let modelLabel: string | null = null;
	export let onChange: (model: { id: string; label: string } | null) => void;
	export let disabled = false;
	/** Bound by a host modal so it can leave Escape to the picker while it is open. */
	export let pickerOpen = false;
	/** For consumers mounted through the plugin host, which cannot `bind:pickerOpen`. */
	export let onPickerOpenChange: ((open: boolean) => void) | undefined = undefined;

	let lastPickerOpen = pickerOpen;
	$: if (pickerOpen !== lastPickerOpen) {
		lastPickerOpen = pickerOpen;
		onPickerOpenChange?.(pickerOpen);
	}

	let resolvedFor: string | null = null;
	let resolvedLabel = '';

	// A prompt can carry a model_id without a model_name (rows that predate
	// name resolution); look the name up from the catalog in that case.
	$: if (modelId && !modelLabel && resolvedFor !== modelId) resolveLabel(modelId);
	$: label = modelId
		? modelLabel || (resolvedFor === modelId ? resolvedLabel : '') || 'Selected model'
		: 'No model';

	async function resolveLabel(id: string) {
		resolvedFor = id;
		resolvedLabel = '';
		try {
			const response = await api.getModelById(id);
			if (resolvedFor === id) resolvedLabel = modelDisplayName(response.data?.model);
		} catch {
			// The fallback label stands.
		}
	}

	function select(model: any) {
		pickerOpen = false;
		onChange({ id: model.id, label: modelDisplayName(model) });
	}

	function clear() {
		pickerOpen = false;
		onChange(null);
	}
</script>

<div class="min-w-0">
	<span class="mb-1.5 block text-xs font-medium text-fg-muted">
		Model <span class="font-normal text-fg-subtle">(optional)</span>
	</span>
	<ModelPickTrigger
		class="py-1.5 text-sm"
		value={modelId ? label : null}
		placeholder="No model"
		{disabled}
		onopen={() => (pickerOpen = true)}
		onclear={clear}
	/>
</div>

{#if pickerOpen}
	<ModelAssignmentModal
		selectionMode="single"
		selectedModelId={modelId}
		allowClear={true}
		title="Assign a model"
		subtitle="Search the model catalog or narrow it by type, then select one model."
		onSelect={select}
		onClear={clear}
		onClose={() => (pickerOpen = false)}
	/>
{/if}
