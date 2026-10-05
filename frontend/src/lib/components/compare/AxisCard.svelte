<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import FieldPicker from './FieldPicker.svelte';
	import AxisValueEditor from './AxisValueEditor.svelte';
	import { setAxis } from '$lib/generation/compare/compareStore.svelte';
	import { checkboxAxisValues } from '$lib/generation/compare/axisValues';
	import type { AxisCandidate, CompareAxis, CompareAxisValue } from '$lib/generation/compare/types';

	let {
		tabId,
		slot,
		candidates,
		axis,
		otherField
	}: {
		tabId: string;
		slot: 'x' | 'y';
		candidates: AxisCandidate[];
		axis: CompareAxis | null;
		otherField: string | null;
	} = $props();

	let open = $state(false);
	let trigger = $state<HTMLButtonElement>();

	let candidate = $derived(axis ? (candidates.find((c) => c.field === axis.field) ?? null) : null);
	let otherSlot = $derived<'x' | 'y'>(slot === 'x' ? 'y' : 'x');
	let countText = $derived.by(() => {
		if (!axis) return '';
		if (candidate && candidate.options.length > 0) return `${axis.values.length} of ${candidate.options.length}`;
		return `${axis.values.length} ${axis.values.length === 1 ? 'value' : 'values'}`;
	});

	function seedValues(picked: AxisCandidate): CompareAxisValue[] {
		if (picked.editor === 'chips') {
			return picked.options.slice(0, 4).map((option) => ({ value: option.value, label: option.label }));
		}
		if (picked.editor === 'checkbox') return checkboxAxisValues();
		if (picked.editor === 'resolution' && typeof picked.currentValue === 'string' && picked.currentValue) {
			const hit = picked.options.find((option) => option.value === picked.currentValue);
			return [{ value: picked.currentValue, label: hit?.label ?? picked.currentValue }];
		}
		return [];
	}

	function pick(picked: AxisCandidate) {
		open = false;
		setAxis(tabId, slot, {
			field: picked.field,
			type: picked.type,
			label: picked.label,
			values: seedValues(picked)
		});
	}

	function updateValues(values: CompareAxisValue[]) {
		if (!axis) return;
		setAxis(tabId, slot, { ...axis, values });
	}

	function remove() {
		open = false;
		setAxis(tabId, slot, null);
	}
</script>

{#if !axis && slot === 'y'}
	<div class="relative">
		<button
			bind:this={trigger}
			type="button"
			class="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong px-3 py-3 text-sm text-fg-muted transition-colors hover:border-line-hover hover:text-fg"
			aria-haspopup="dialog"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			<Icon name="plus" className="h-4 w-4" />
			Add a second field (optional)
		</button>
	</div>
{:else}
	<div class="rounded-lg border border-line bg-surface-2 p-3" data-testid="axis-card-{slot}">
		<div class="mb-2 flex items-center justify-between gap-2">
			<span class="font-mono text-[10px] uppercase tracking-wider text-fg-subtle">Field</span>
			<span class="flex items-center gap-1">
				{#if axis}
					<span class="font-mono text-xs tabular-nums text-fg-subtle" data-testid="axis-count-{slot}">{countText}</span>
					<IconButton icon="close" label="Remove {slot.toUpperCase()} axis" size="xs" onclick={remove} />
				{/if}
			</span>
		</div>
		<button
			bind:this={trigger}
			type="button"
			class="flex w-full items-center gap-2 rounded border border-line bg-surface-1 px-2.5 py-2 text-left text-sm text-fg transition-colors hover:border-line-hover"
			aria-haspopup="dialog"
			aria-expanded={open}
			aria-label="{slot.toUpperCase()} axis field"
			onclick={() => (open = !open)}
		>
			<span
				class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border border-signal/40 bg-signal/10 font-mono text-[10px] text-signal"
			>
				{slot.toUpperCase()}
			</span>
			<span class="min-w-0 flex-1 truncate {axis ? '' : 'text-fg-subtle'}">{axis?.label ?? 'Pick a field'}</span>
			{#if candidate}
				<span class="flex-shrink-0 text-xs text-fg-subtle">{candidate.group}</span>
			{/if}
			<Icon name="chevron-down" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
		</button>
		{#if axis && candidate}
			<div class="mt-3">
				<AxisValueEditor {candidate} {axis} {tabId} onChange={updateValues} />
			</div>
		{/if}
	</div>
{/if}

{#if open && trigger}
	<FieldPicker
		{candidates}
		selectedField={axis?.field ?? null}
		{otherField}
		{otherSlot}
		anchor={trigger}
		onSelect={pick}
		onClose={() => (open = false)}
	/>
{/if}
