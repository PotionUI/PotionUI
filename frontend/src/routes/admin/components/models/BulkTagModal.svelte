<script lang="ts">
	import { api } from '$lib/services/api/index';
	import type { BulkModelTagsResult, ModelSelectionTag } from '$lib/services/api/models';
	import { toasts } from '$lib/stores/toast';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmFooter from '$lib/components/modals/ConfirmFooter.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button, IconButton, Input, Spinner } from '$lib/components/ui';
	import {
		addChip,
		buildBulkTagsBody,
		hasBulkChanges,
		pendingSummary,
		removeChip,
		resultSummary,
		selectionRows,
		suggestTags,
		toggleRemoval,
		type KnownTag
	} from './bulkTags';

	let {
		isOpen,
		modelIds,
		onClose,
		onApplied
	}: {
		isOpen: boolean;
		modelIds: string[];
		onClose: () => void;
		onApplied?: (result: BulkModelTagsResult) => void | Promise<void>;
	} = $props();

	let knownTags = $state<KnownTag[]>([]);
	let selectionTags = $state<ModelSelectionTag[]>([]);
	let loading = $state(false);
	let loadError = $state<string | null>(null);
	let query = $state('');
	let chips = $state<string[]>([]);
	let removeIds = $state<string[]>([]);
	let applying = $state(false);
	let inputFocused = $state(false);
	let highlighted = $state(-1);

	const count = $derived(modelIds.length);
	const rows = $derived(selectionRows(selectionTags, count));
	const suggestions = $derived(suggestTags(knownTags, query, chips));
	const body = $derived(buildBulkTagsBody(modelIds, chips, removeIds, rows));
	const canApply = $derived(hasBulkChanges(body) && !applying);
	const showSuggestions = $derived(inputFocused && suggestions.length > 0);
	const listboxId = 'bulk-tag-suggestions';

	let loadedFor = '';

	$effect(() => {
		const key = isOpen ? modelIds.join(',') : '';
		if (!key || key === loadedFor) return;
		loadedFor = key;
		chips = [];
		removeIds = [];
		query = '';
		void load(modelIds);
	});

	async function load(ids: string[]) {
		loading = true;
		loadError = null;
		try {
			const [selection, all] = await Promise.all([api.getModelSelectionTags(ids), api.getTags('MODEL')]);
			if (!selection.success) throw new Error(selection.message || 'Could not load tags');
			selectionTags = selection.data?.tags ?? [];
			knownTags = (all.data?.tags ?? []).map((tag: KnownTag) => ({ id: tag.id, name: tag.name }));
		} catch (e) {
			loadError = getApiErrorMessage(e, 'Could not load tags');
		} finally {
			loading = false;
		}
	}

	function commit(value: string) {
		chips = addChip(chips, value);
		query = '';
		highlighted = -1;
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' && suggestions.length) {
			event.preventDefault();
			highlighted = (highlighted + 1) % suggestions.length;
		} else if (event.key === 'ArrowUp' && suggestions.length) {
			event.preventDefault();
			highlighted = highlighted <= 0 ? suggestions.length - 1 : highlighted - 1;
		} else if (event.key === 'Enter' || event.key === ',') {
			const picked = highlighted >= 0 ? suggestions[highlighted]?.name : query;
			if (picked && picked.trim()) {
				event.preventDefault();
				event.stopPropagation();
				commit(picked);
			}
		} else if (event.key === 'Backspace' && !query && chips.length) {
			chips = chips.slice(0, -1);
		}
	}

	async function apply() {
		if (!canApply) return;
		applying = true;
		try {
			const response = await api.bulkUpdateModelTags(body);
			if (!response.success || !response.data) {
				toasts.error(response.message || 'Could not update tags');
				return;
			}
			toasts.success(resultSummary(response.data));
			loadedFor = '';
			await onApplied?.(response.data);
			onClose();
		} catch (e) {
			toasts.error(getApiErrorMessage(e, 'Could not update tags'));
		} finally {
			applying = false;
		}
	}

	function close() {
		if (applying) return;
		loadedFor = '';
		onClose();
	}
</script>

<BaseModal {isOpen} title={`Tags — ${count} model${count === 1 ? '' : 's'}`} size="md" closeable={!applying} on:close={close}>
	<div class="flex flex-col gap-6 p-6">
		<div class="flex flex-col gap-2">
			<label for="bulk-tag-input" class="text-sm font-medium text-fg">Add tags</label>
			<div class="relative">
				<Input
					id="bulk-tag-input"
					class="w-full"
					placeholder="Type a tag and press Enter"
					autocomplete="off"
					role="combobox"
					aria-expanded={showSuggestions}
					aria-controls={listboxId}
					bind:value={query}
					oninput={() => (highlighted = -1)}
					onkeydown={handleKeydown}
					onfocus={() => (inputFocused = true)}
					onblur={() => (inputFocused = false)}
				/>
				{#if showSuggestions}
					<div
						id={listboxId}
						role="listbox"
						aria-label="Existing tags"
						class="absolute left-0 right-0 top-full z-10 mt-1 max-h-56 overflow-y-auto rounded-lg border border-line-strong bg-surface-2 py-1 shadow-floating"
					>
						{#each suggestions as tag, i (tag.id)}
							<button
								type="button"
								role="option"
								aria-selected={i === highlighted}
								class="flex w-full items-center px-3 py-1.5 text-left text-md text-fg hover:bg-surface-3 {i === highlighted ? 'bg-surface-3' : ''}"
								onmousedown={(e) => {
									e.preventDefault();
									commit(tag.name);
								}}
							>
								<span class="truncate">{tag.name}</span>
							</button>
						{/each}
					</div>
				{/if}
			</div>
			{#if chips.length}
				<div class="flex flex-wrap gap-1.5" data-testid="bulk-tag-add-chips">
					{#each chips as chip (chip)}
						<Badge variant="signal" class="text-sm">
							<span class="max-w-[14rem] truncate">{chip}</span>
							<Tooltip text={`Don't add ${chip}`}>
								<button
									type="button"
									aria-label={`Don't add ${chip}`}
									class="-mr-1 rounded p-0.5 hover:bg-signal/20"
									onclick={() => (chips = removeChip(chips, chip))}
								>
									<Icon name="close" className="h-3.5 w-3.5" />
								</button>
							</Tooltip>
						</Badge>
					{/each}
				</div>
			{/if}
		</div>

		<div class="flex flex-col gap-2">
			<div class="flex items-baseline justify-between gap-3">
				<span class="text-sm font-medium text-fg">Currently on selection</span>
				{#if rows.length}
					<span class="font-mono text-sm tabular-nums text-fg-subtle">{rows.length}</span>
				{/if}
			</div>
			{#if loading}
				<div class="flex items-center gap-2 py-3 text-sm text-fg-muted"><Spinner size="sm" /> Loading tags…</div>
			{:else if loadError}
				<div class="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface-1 px-3 py-2 text-sm text-danger">
					<span>{loadError}</span>
					<Button size="sm" variant="ghost" onclick={() => load(modelIds)}>Retry</Button>
				</div>
			{:else if rows.length === 0}
				<p class="rounded-lg border border-line bg-surface-1 px-3 py-3 text-sm text-fg-muted">None of the selected models have tags yet.</p>
			{:else}
				<ul class="max-h-72 divide-y divide-line overflow-y-auto rounded-lg border border-line bg-surface-1" data-testid="bulk-tag-selection">
					{#each rows as row (row.id)}
						{@const removing = removeIds.includes(row.id)}
						<li class="flex items-center gap-3 px-3 py-2">
							<span class="min-w-0 flex-1 truncate text-md {removing ? 'text-fg-subtle line-through' : 'text-fg'}">{row.name}</span>
							{#if removing}
								<Badge variant="danger" class="text-sm">Removing</Badge>
							{/if}
							<span class="font-mono text-sm tabular-nums {row.onAll ? 'text-fg' : 'text-fg-muted'}">{row.label}</span>
							<Tooltip text={removing ? `Keep ${row.name}` : `Remove ${row.name} from ${row.count} model${row.count === 1 ? '' : 's'}`}>
								<IconButton
									size="sm"
									icon={removing ? 'undo' : 'trash'}
									label={removing ? `Keep ${row.name}` : `Remove ${row.name}`}
									active={removing}
									onclick={() => (removeIds = toggleRemoval(removeIds, row.id))}
								/>
							</Tooltip>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel="Apply"
			busy={applying}
			confirmDisabled={!canApply}
			summary={pendingSummary(body) || undefined}
			onCancel={close}
			onConfirm={apply}
		/>
	</svelte:fragment>
</BaseModal>
