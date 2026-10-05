<script lang="ts">
	import ModelPickerDropdown from './ModelPickerDropdown.svelte';
	import NumberValuesInput from './NumberValuesInput.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { refFor } from '$lib/utils/modelRef';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { loraAxisValues } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

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

	type Row = { model: string; strength?: number };

	let tab = $derived($tabsStore.tabs.find((entry) => entry.id === tabId));
	let rows = $derived.by(() => {
		const raw = tab?.formData?.[candidate.field];
		return (Array.isArray(raw) ? raw : []).filter((row): row is Row => !!row && typeof row.model === 'string');
	});
	let presetId = $derived(tab?.selectedPreset ?? '');

	const [savedLora, savedLabel] = (() => [
		(axis?.values[0]?.value as { lora?: string } | undefined)?.lora,
		axis?.values[0]?.label.split(' · ')[0]
	])();
	let extra = $state<Array<{ ref: string; name: string }>>(savedLora && savedLabel ? [{ ref: savedLora, name: savedLabel }] : []);
	let selectedRef = $state<string>(savedLora ?? '');
	let strengths = $state<number[]>([]);

	let choices = $derived.by(() => {
		const out: Array<{ ref: string; name: string }> = [];
		for (const row of rows) out.push({ ref: row.model, name: row.model.replace(/^model:/, '') });
		for (const item of extra) if (!out.some((entry) => entry.ref === item.ref)) out.push(item);
		return out;
	});

	let activeRef = $derived(selectedRef || choices[0]?.ref || '');
	let activeName = $derived(choices.find((entry) => entry.ref === activeRef)?.name ?? activeRef);
	let rowStrength = $derived(rows.find((row) => row.model === activeRef)?.strength);

	const step = 0.25;

	function emit(next: number[] = strengths) {
		strengths = next;
		if (!activeRef) {
			onChange([]);
			return;
		}
		onChange(loraAxisValues(activeRef, activeName, next, step));
	}

	function browse(model: any) {
		const ref = refFor(model);
		if (!ref) return;
		extra = [...extra.filter((entry) => entry.ref !== ref), { ref, name: modelDisplayName(model) || String(model.filename ?? ref) }];
		selectedRef = ref;
		emit();
	}

	function choose(ref: string) {
		selectedRef = ref;
		emit();
	}
</script>

<div class="space-y-3">
	<div class="space-y-1.5">
		<span class="block font-mono text-2xs uppercase tracking-wide text-fg-subtle">LoRA</span>
		{#if choices.length > 0}
			<select class="input" aria-label="LoRA" value={activeRef} onchange={(event) => choose(event.currentTarget.value)}>
				{#each choices as choice (choice.ref)}
					<option value={choice.ref}>{choice.name}</option>
				{/each}
			</select>
		{:else}
			<p class="text-xs text-fg-muted">No LoRA is in the form yet. Browse to pick one.</p>
		{/if}
		<ModelPickerDropdown modelType="lora" {presetId} label="Browse LoRAs" onSelect={browse} />
	</div>
	{#key activeRef}
		<NumberValuesInput
			min={0}
			max={2}
			{step}
			initial={typeof rowStrength === 'number' ? rowStrength : 0}
			emitOnMount={!!activeRef}
			onValues={(next) => emit(next)}
		/>
	{/key}
</div>
