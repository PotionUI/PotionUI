<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from './modals/BaseModal.svelte';
	import ConfirmFooter from './modals/ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction } from './modals/confirmKeyboard';
	import type { Segment } from '$lib/types/segments';
	import PromptSegmentMetadataEditor from './PromptSegmentMetadataEditor.svelte';

	// Replaces the old inline reveal under the card/content: name, colour and
	// description now edit in a draft that only lands on the real segment when
	// Save (or Enter) commits it — Cancel (or Esc) discards it untouched.
	let {
		isOpen,
		segment,
		onClose,
		onSave
	}: {
		isOpen: boolean;
		segment: Segment;
		onClose: () => void;
		onSave: (updates: { name?: string; color?: string; description?: string }) => void;
	} = $props();

	let draft = $state<Segment>(segment);
	const settlementGate = createConfirmSettlementGate();

	$effect(() => {
		if (!isOpen) return;
		untrack(() => {
			draft = { ...segment };
			settlementGate.reset();
		});
	});

	function handleChange(
		event: CustomEvent<Partial<Pick<Segment, 'name' | 'color' | 'description'>>>
	) {
		draft = { ...draft, ...event.detail };
	}

	function handleCancel() {
		settlementGate.settle(onClose);
	}

	function handleConfirm() {
		settlementGate.settle(() => {
			onSave({ name: draft.name ?? undefined, color: draft.color ?? undefined, description: draft.description ?? undefined });
			onClose();
		});
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!isOpen) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} title="Segment details" size="lg" handleEscapeKey={false} on:close={handleCancel}>
	<div class="p-4 sm:p-6">
		<PromptSegmentMetadataEditor segment={draft} on:change={handleChange} />

		{#if segment.template}
			<p class="mt-1 text-xs text-fg-subtle">
				From template {segment.template.name}, slot {segment.template.slot}
			</p>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="details-footer">
			<ConfirmFooter confirmLabel="Save" onCancel={handleCancel} onConfirm={handleConfirm} />
		</div>
	</svelte:fragment>
</BaseModal>

<style>
	/* The Save button is autofocused on open (ConfirmFooter's `initialFocus`,
	   via focusTrap's requestAnimationFrame `.focus()`), which browsers treat
	   as keyboard-origin focus and ring accordingly — a wide default UA
	   outline, not a styling bug in this modal specifically. Swapped for a
	   thin signal ring on real keyboard focus, nothing on the autofocus/mouse
	   case, matching how the rest of this design system treats focus. */
	.details-footer :global(button:focus) {
		outline: none;
	}

	.details-footer :global(button:focus-visible) {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
</style>
