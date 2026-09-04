<script lang="ts">
	import { untrack } from 'svelte';
	import BaseModal from './modals/BaseModal.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction } from './modals/confirmKeyboard';
	import { Kbd } from '$lib/components/ui';
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
	<div class="segment-composer">
		<div class="p-4 sm:p-6">
			<PromptSegmentMetadataEditor segment={draft} on:change={handleChange} />

			{#if segment.template}
				<p class="mt-1 text-xs text-fg-subtle">
					From template {segment.template.name}, slot {segment.template.slot}
				</p>
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<div class="segment-composer details-footer w-full">
			<footer class="modal-foot">
				<button type="button" class="small-button" onclick={handleCancel}>
					Cancel
					<Kbd keys="Esc" />
				</button>
				<button type="button" class="small-button primary" onclick={handleConfirm}>
					Save details
					<Kbd keys="Enter" />
				</button>
			</footer>
		</div>
	</svelte:fragment>
</BaseModal>

<style>
	/* The Save button used to be autofocused via ConfirmFooter's `initialFocus`
	   (a focusTrap requestAnimationFrame `.focus()`), which browsers treat as
	   keyboard-origin focus and ring accordingly — now the mock's own footer
	   buttons, so that autofocus is gone with it; Escape/Enter still reach the
	   same confirm/cancel path via the window keydown handler above regardless
	   of where focus sits. */
	.details-footer :global(button:focus-visible) {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
</style>
