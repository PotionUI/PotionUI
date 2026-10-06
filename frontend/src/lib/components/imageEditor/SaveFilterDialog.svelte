<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { Button, Input } from '$lib/components/ui';
	import { selectFilterItem, suggestedName } from '$lib/filters/actions';
	import { createMineFilter, errorText, filterCatalog, isNameTaken } from '$lib/filters/catalog';
	import { editedSteps, opLookup, serializeSteps } from '$lib/filters/steps';
	import { toasts } from '$lib/stores/toast';
	import type { PaintSession, SessionSnapshot } from './session';

	export let isOpen = false;
	export let session: PaintSession;
	export let state: SessionSnapshot;

	const dispatch = createEventDispatcher<{ close: void }>();
	const NAME_MAX = 24;
	const DESCRIPTION_MAX = 240;

	let name = '';
	let description = '';
	let error: string | null = null;
	let saving = false;
	let wasOpen = false;

	$: active = state.filter.active;
	$: lookup = opLookup($filterCatalog.ops);
	$: edited = active ? editedSteps(active.steps, state.filter.steps, lookup) : 0;
	$: stepCount = state.filter.steps.length;
	$: lutBlocked = !!active?.hasLut;
	$: trimmed = name.trim();
	$: canSave = !!active && !lutBlocked && trimmed.length > 0 && trimmed.length <= NAME_MAX && !saving;
	$: if (isOpen !== wasOpen) {
		wasOpen = isOpen;
		if (isOpen && active) {
			name = suggestedName(active.name, active.owned);
			description = active.description.slice(0, DESCRIPTION_MAX);
			error = null;
			saving = false;
		}
	}

	async function save() {
		if (!active || !canSave) return;
		saving = true;
		error = null;
		try {
			const created = await createMineFilter({
				name: trimmed,
				description: description.trim() || undefined,
				intensity: state.filter.intensity,
				steps: serializeSteps(state.filter.steps)
			});
			await selectFilterItem(session, created);
			toasts.success(`Saved ${created.name} to My filters`);
			dispatch('close');
		} catch (failure) {
			error = isNameTaken(failure)
				? 'You already have a filter with that name.'
				: errorText(failure, 'The filter could not be saved.');
		} finally {
			saving = false;
		}
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' && !event.isComposing) {
			event.preventDefault();
			void save();
		}
	}
</script>

<BaseModal
	{isOpen}
	title="Save as filter"
	subtitle="Keep this look and use it on any image of yours."
	size="sm"
	handleEscapeKey={false}
	on:close={() => dispatch('close')}
>
	<div class="flex flex-col gap-3 p-4">
		<div class="flex flex-col gap-1">
			<label for="save-filter-name" class="text-xs text-fg-muted">Name</label>
			<Input
				id="save-filter-name"
				bind:value={name}
				maxlength={NAME_MAX}
				invalid={error !== null}
				autocomplete="off"
				data-autofocus
				onkeydown={onKeydown}
			/>
			{#if error}
				<p class="text-xs text-danger" role="alert">{error}</p>
			{/if}
		</div>

		<div class="flex flex-col gap-1">
			<label for="save-filter-description" class="text-xs text-fg-muted">Description</label>
			<Input
				id="save-filter-description"
				bind:value={description}
				maxlength={DESCRIPTION_MAX}
				autocomplete="off"
				onkeydown={onKeydown}
			/>
		</div>

		<dl class="flex flex-col gap-1.5 rounded-lg border border-line bg-surface-2 p-3 text-xs">
			<div class="flex justify-between gap-3">
				<dt class="text-fg-muted">Steps</dt>
				<dd class="font-mono tabular-nums text-fg">{stepCount} from {active?.name ?? ''}, {edited} edited</dd>
			</div>
			<div class="flex justify-between gap-3">
				<dt class="text-fg-muted">Default intensity</dt>
				<dd class="font-mono tabular-nums text-fg">{state.filter.intensity}%</dd>
			</div>
			<div class="flex justify-between gap-3">
				<dt class="text-fg-muted">Saved to</dt>
				<dd class="text-fg">My filters</dd>
			</div>
		</dl>

		{#if lutBlocked}
			<p class="text-xs text-warning" data-testid="lut-refusal">LUT filters can't be copied yet.</p>
		{:else}
			<p class="text-xs leading-relaxed text-fg-subtle">
				Only you can see it. You can rename or delete it any time from the Mine group.
			</p>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 px-4 py-3">
			<Button variant="ghost" size="sm" onclick={() => dispatch('close')}>Cancel</Button>
			<Button variant="primary" size="sm" icon="check" loading={saving} disabled={!canSave} onclick={save}>
				Save filter
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
