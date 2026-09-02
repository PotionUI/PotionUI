<script lang="ts">
	import { api } from '$lib/services/api';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import { modelDisplayName } from '$lib/utils/modelDisplay';

	export let modelId: string | null = null;
	/** Display fallback when the prompt already carries a `model_name`. */
	export let modelLabel: string | null = null;
	export let onChange: (model: { id: string; label: string } | null) => void;
	export let disabled = false;
	/** Bound by a host modal so it can leave Escape to the picker while it is open. */
	export let pickerOpen = false;

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

<div>
	<span class="mb-1.5 block text-xs font-medium text-fg-muted">
		Model <span class="font-normal text-fg-subtle">(optional)</span>
	</span>
	<div class="flex items-center gap-2">
		<div
			class="input flex min-w-0 flex-1 items-center py-1.5 text-sm {modelId ? 'text-fg' : 'text-fg-subtle'}"
			title={label}
		>
			<span class="truncate">{label}</span>
		</div>
		<Button variant="secondary" size="sm" {disabled} onclick={() => (pickerOpen = true)}>Change</Button>
		{#if modelId}
			<Tooltip text="Clear model" position="top">
				<IconButton icon="close" label="Clear model" size="sm" {disabled} onclick={clear} />
			</Tooltip>
		{/if}
	</div>
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
