<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { PromptUsageHint, Segment } from '$lib/types/segments';
	import { flattenRichSegments, toRichSegment } from '$lib/utils/richSegments';
	import type { VariablesMap } from '$lib/utils/variableDefs';
	import { selectReferencedVariables, summarizeVariables } from '$lib/utils/variablesTransfer';
	import { toasts } from '$lib/stores/toast';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let segments: Segment[] = [];
	export let usageHint: PromptUsageHint = 'positive';
		export let variables: VariablesMap = {};
	const dispatch = createEventDispatcher<{ close: void; saved: { id: string } }>();
	let name = '';
	let saving = false;
	let previousOpen = false;
	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen !== previousOpen) { previousOpen = isOpen; if (isOpen) { name = ''; settlementGate.reset(); } }
	$: preview = flattenRichSegments(segments);
	$: referencedVariables = selectReferencedVariables(preview, variables);
	$: referencedSummary = summarizeVariables(referencedVariables);
	$: variablesSummaryLine =
		referencedSummary.variables > 0
			? `Includes ${referencedSummary.variables} variable${referencedSummary.variables === 1 ? '' : 's'} · ${referencedSummary.conditions} condition${referencedSummary.conditions === 1 ? '' : 's'}`
			: '';

	async function save() {
		saving = true;
		try {
			const response = await api.createPrompt({
				name: name.trim() || null,
				usage_hint: usageHint,
				segments: segments.map(toRichSegment),
				variables: Object.keys(referencedVariables).length ? referencedVariables : null
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Failed to save Prompt');
			toasts.success('New detached Prompt saved');
			dispatch('saved', { id: response.data.id });
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save Prompt');
		} finally {
			saving = false;
		}
	}

	function handleCancel() {
		settlementGate.settle(() => dispatch('close'));
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !saving, save);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} title="Save as Prompt" sizeClass="md:max-w-lg md:w-full" handleEscapeKey={false} on:close={handleCancel}>
	<svelte:fragment slot="headerIcon"><Icon name="save" className="h-5 w-5 text-fg-muted" /></svelte:fragment>
	<div class="space-y-4 p-4 sm:p-6">
		<div><label class="mb-1.5 block text-sm font-medium text-fg" for="saved-prompt-name">Name <span class="font-normal text-fg-subtle">(optional)</span></label><input id="saved-prompt-name" class="input w-full" bind:value={name} placeholder="Content preview is used when unnamed" /></div>
		<div><span class="mb-1.5 block text-sm font-medium text-fg">Composition</span><div class="max-h-36 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3 text-sm text-fg-muted">{preview || 'Blank prompt'} </div><p class="mt-1.5 text-xs text-fg-subtle">{segments.length} segment{segments.length === 1 ? '' : 's'} · {usageHint} usage hint</p></div>
		{#if variablesSummaryLine}
			<p class="font-mono text-xs text-fg-subtle">{variablesSummaryLine}</p>
		{/if}
		<p class="text-xs text-fg-subtle">Only this segment list is saved. Preset, mode, form values, session, backend, seed, tags, and generation settings are not included.</p>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter confirmLabel="Save Prompt" busy={saving} onCancel={handleCancel} onConfirm={handleConfirm} />
	</svelte:fragment>
</BaseModal>
