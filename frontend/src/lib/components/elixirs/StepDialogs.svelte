<script lang="ts">
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import type { StepBarModel } from '$lib/elixirs/stepBar';

	interface Props {
		model: StepBarModel;
	}

	let { model }: Props = $props();

	const confirmId = `step-confirm-${Math.random().toString(36).slice(2, 9)}`;
	const conflictId = `step-conflict-${Math.random().toString(36).slice(2, 9)}`;

	let gone = $derived(model.conflict?.reason === 'gone');
</script>

<ConfirmDialog
	isOpen={!!model.confirm}
	size="md"
	compact
	hideCloseButton
	dialogRole="alertdialog"
	labelledBy={confirmId}
	busy={model.saving}
	confirmLabel="Save to elixir"
	cancelLabel="Cancel"
	secondaryLabel="Discard"
	onConfirm={() => model.answerConfirm('save')}
	onCancel={() => model.answerConfirm('cancel')}
	onSecondary={() => model.answerConfirm('discard')}
>
	<div class="p-7" data-testid="step-confirm">
		<h3 id={confirmId} class="mb-1.5 text-base font-semibold text-fg break-words">Save changes to {model.stepLabel}?</h3>
		<p class="text-sm leading-relaxed text-fg-muted break-words">
			You changed this step's settings. Save them to {model.elixirName}, or leave them out.
		</p>
	</div>
</ConfirmDialog>

<ConfirmDialog
	isOpen={!!model.conflict}
	size="md"
	compact
	hideCloseButton
	dialogRole="alertdialog"
	labelledBy={conflictId}
	busy={model.saving}
	confirmLabel="Overwrite"
	confirmDisabled={!model.conflict?.canOverwrite}
	cancelLabel="Cancel"
	secondaryLabel="Reload step"
	onConfirm={() => model.resolveConflict('overwrite')}
	onCancel={() => model.resolveConflict('cancel')}
	onSecondary={() => model.resolveConflict('reload')}
>
	<div class="p-7" data-testid="step-conflict">
		<h3 id={conflictId} class="mb-1.5 text-base font-semibold text-fg break-words">{model.elixirName} changed since you opened this step</h3>
		<p class="text-sm leading-relaxed text-fg-muted break-words">
			{#if gone}
				{model.stepLabel} was removed in another tab or on another device. Reload to see the elixir as it is now.
			{:else}
				{model.stepLabel} was edited in another tab or on another device. Saving now would replace those changes.
			{/if}
		</p>
	</div>
</ConfirmDialog>
