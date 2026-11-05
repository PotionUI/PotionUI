<script lang="ts">
	import { Button, IconButton } from '$lib/components/ui';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import type { SessionPanelActions, SessionPanelState } from '$lib/session/sessionDialogs';
	import type { StepBarModel } from '$lib/elixirs/stepBar';

	interface Props {
		model: StepBarModel;
		part?: 'phone' | 'lead' | 'actions';
		session?: SessionPanelState | null;
		actions?: SessionPanelActions | null;
		fieldLabels?: FieldLabels;
	}

	let { model, part = 'lead', session = null, actions = null, fieldLabels = {} }: Props = $props();

	let busy = $derived(model.saving || model.opening);
	let unsaved = $derived(!model.follow && model.edits > 0);
</script>

{#if part === 'phone'}
	<div
		class="flex min-w-0 flex-shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-3 py-2"
		role="region"
		aria-label="Editing {model.stepLabel} of {model.elixirName}"
		data-testid="step-edit-bar"
		data-phone=""
	>
		<IconButton icon="arrow-left" label="Back to {model.elixirName}" onclick={() => model.back()} disabled={busy} />
		{#if model.follow}
			{#if session && actions}
				<div class="flex min-w-0 flex-1 justify-end" data-testid="step-session">
					<SessionControl {session} {actions} {fieldLabels} compact />
				</div>
			{/if}
		{:else}
			<span class="min-w-0 flex-1"></span>
			{#if unsaved}
				<span class="text-sm text-fg-subtle" data-testid="step-unsaved">Unsaved</span>
			{/if}
			<Button variant="primary" size="sm" loading={model.saving} disabled={busy} onclick={() => model.save()}>Save</Button>
		{/if}
	</div>
{:else if part === 'lead'}
	<div class="flex min-w-0 items-center gap-2" role="region" aria-label="Editing {model.stepLabel} of {model.elixirName}" data-testid="step-edit-bar">
		<Button variant="ghost" size="sm" icon="arrow-left" disabled={busy} onclick={() => model.back()}>
			<span class="max-w-[14rem] truncate">Back to {model.elixirName}</span>
		</Button>
	</div>
{:else if !model.follow}
	<div class="flex flex-shrink-0 items-center gap-2" data-testid="step-edit-actions">
		{#if unsaved}
			<span class="text-sm text-fg-subtle" data-testid="step-unsaved">Unsaved</span>
		{/if}
		<Button variant="secondary" size="sm" disabled={busy} onclick={() => model.back()}>Discard</Button>
		<Button variant="primary" size="sm" loading={model.saving} disabled={busy} onclick={() => model.save()}>Save to elixir</Button>
	</div>
{/if}
