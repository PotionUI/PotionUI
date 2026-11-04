<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import LaneMenu, { type LaneMenuData } from './LaneMenu.svelte';
	import { stepNeighbours, type StepBarModel } from '$lib/elixirs/stepBar';

	interface Props {
		model: StepBarModel;
		phone?: boolean;
	}

	let { model, phone = false }: Props = $props();

	let menu = $state.raw<LaneMenuData | null>(null);
	let stepsButton = $state<HTMLElement | null>(null);

	let near = $derived(stepNeighbours(model.steps, model.index));
	let busy = $derived(model.saving || model.opening);
	let unsaved = $derived(!model.follow && model.edits > 0);

	function openSteps(anchor: HTMLElement | null) {
		menu = {
			title: `Steps in ${model.elixirName}`,
			anchor,
			groups: [
				{
					items: model.steps.map((step) => ({
						name: step.label,
						desc: step.detail,
						radio: true,
						checked: step.id === model.steps[model.index]?.id,
						run: () => model.go(step.id)
					}))
				},
				...(phone && !model.follow
					? [{ items: [{ icon: 'undo', name: 'Discard changes', run: () => model.back() }] }]
					: [])
			]
		};
	}
</script>

<div
	class="step-bar flex min-w-0 flex-shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-3 py-2 sm:px-4"
	role="region"
	aria-label="Editing {model.stepLabel} of {model.elixirName}"
	data-testid="step-edit-bar"
	data-phone={phone ? '' : undefined}
>
	{#if phone}
		<IconButton icon="arrow-left" label="Back to {model.elixirName}" onclick={() => model.back()} disabled={busy} />
		<span class="min-w-0 flex-1 truncate text-md text-fg-muted">Editing <b class="font-semibold text-fg">{model.stepLabel}</b></span>
		{#if model.follow}
			<span class="follow-chip" data-testid="step-follow-chip">
				<Icon name="plug" className="h-3.5 w-3.5" />
				Following
			</span>
		{/if}
		<IconButton
			icon="more"
			label="Steps"
			onclick={(event) => openSteps(event.currentTarget as HTMLElement)}
			ariaExpanded={!!menu}
		/>
		{#if !model.follow}
			<Button variant="primary" size="sm" loading={model.saving} disabled={busy} onclick={() => model.save()}>Save</Button>
		{/if}
	{:else}
		<Button variant="ghost" size="sm" icon="arrow-left" disabled={busy} onclick={() => model.back()}>
			<span class="max-w-[14rem] truncate">Back to {model.elixirName}</span>
		</Button>
		<span class="h-5 w-px bg-line" aria-hidden="true"></span>
		<span class="min-w-0 truncate text-md text-fg-muted">Editing <b class="font-semibold text-fg">{model.stepLabel}</b></span>
		{#if model.follow}
			<span class="follow-chip" data-testid="step-follow-chip">
				<Icon name="plug" className="h-3.5 w-3.5" />
				<span class="max-w-[14rem] truncate">Following {model.sessionName ?? 'a session'}</span>
			</span>
		{/if}
		<span class="inline-flex flex-shrink-0 items-center gap-0.5" role="group" aria-label="Step">
			<Tooltip text={near.previous ? `Previous: ${near.previous.label}` : 'First step'}>
				<IconButton
					icon="chevron-left"
					label="Previous step"
					size="sm"
					disabled={!near.previous || busy}
					onclick={() => near.previous && model.go(near.previous.id)}
				/>
			</Tooltip>
			<button
				bind:this={stepsButton}
				type="button"
				class="steps-button"
				aria-haspopup="menu"
				aria-expanded={!!menu}
				disabled={busy}
				onclick={() => openSteps(stepsButton)}
			>
				<span class="font-mono tabular-nums">{near.label}</span>
				<span>Steps</span>
				<Icon name="chevron-down" className="h-3.5 w-3.5" />
			</button>
			<Tooltip text={near.next ? `Next: ${near.next.label}` : 'Last step'}>
				<IconButton
					icon="chevron-right"
					label="Next step"
					size="sm"
					disabled={!near.next || busy}
					onclick={() => near.next && model.go(near.next.id)}
				/>
			</Tooltip>
		</span>
		<Tooltip text="A test Generate here is a normal generation. It goes to History and never becomes an output of this elixir.">
			<span class="note" data-testid="step-history-note">
				<Icon name="info" className="h-3.5 w-3.5" />
				Test runs go to History
			</span>
		</Tooltip>
		<span class="flex-1"></span>
		{#if model.follow}
			<span class="saved-note" data-testid="step-autosaved">
				<Icon name="check" className="h-3.5 w-3.5" />
				Saved automatically
			</span>
		{:else}
			{#if unsaved}
				<span class="text-sm text-fg-subtle" data-testid="step-unsaved">Unsaved</span>
			{/if}
			<Button variant="secondary" size="sm" disabled={busy} onclick={() => model.back()}>Discard</Button>
			<Button variant="primary" size="sm" loading={model.saving} disabled={busy} onclick={() => model.save()}>Save to elixir</Button>
		{/if}
	{/if}
</div>

<LaneMenu {menu} {phone} onclose={() => (menu = null)} />

<style>
	.follow-chip {
		display: inline-flex;
		flex-shrink: 0;
		align-items: center;
		gap: 6px;
		min-height: 24px;
		padding: 0 8px;
		border-radius: 4px;
		border: 1px solid rgb(var(--signal) / 0.3);
		background: rgb(var(--signal) / 0.1);
		color: rgb(var(--signal));
		font-size: 12px;
	}
	.steps-button {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		min-height: 32px;
		padding: 0 8px;
		border-radius: 4px;
		font-size: 13px;
		color: rgb(var(--fg-muted));
	}
	.steps-button:hover:not(:disabled) {
		background: rgb(var(--surface-3) / 0.5);
		color: rgb(var(--fg));
	}
	.steps-button:disabled {
		opacity: 0.5;
	}
	:global(:where(html:not([data-input='pointer']))) .steps-button:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 1px;
	}
	.note,
	.saved-note {
		display: inline-flex;
		flex-shrink: 0;
		align-items: center;
		gap: 6px;
		font-size: 12px;
		color: rgb(var(--fg-subtle));
	}
	.saved-note {
		color: rgb(var(--fg-muted));
	}
</style>
