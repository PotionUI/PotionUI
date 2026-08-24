<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { Prompt, SegmentTemplate } from '$lib/types/segments';
	import { flattenRichSegments, type SegmentApplyMode } from '$lib/utils/richSegments';
	import { logger } from '$lib/utils/logger';
	import BaseModal from './BaseModal.svelte';
	import ConfirmModal from './ConfirmModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner, Alert } from '$lib/components/ui';

	export let isOpen = false;
	export let kind: 'prompt' | 'template' = 'prompt';
	export let targetHasMeaningfulContent = false;

	type LibraryItem = Prompt | SegmentTemplate;

	const dispatch = createEventDispatcher<{
		close: void;
		apply: { item: LibraryItem; mode: SegmentApplyMode };
	}>();

	let items: LibraryItem[] = [];
	let loading = false;
	let error = '';
	let searchTerm = '';
	let selectedId: string | null = null;
	let applyMode: SegmentApplyMode | null = null;
	let previousOpen = false;
	let showReplaceConfirmation = false;
	const applyModes: SegmentApplyMode[] = ['append', 'prepend', 'replace'];

	$: selectedItem = items.find((item) => item.id === selectedId) || null;
	$: normalizedSearch = searchTerm.trim().toLowerCase();
	$: filteredItems = items.filter((item) => {
		if (!normalizedSearch) return true;
		return [itemName(item), itemPreview(item), itemDescription(item), ...(item.tags || [])]
			.join(' ')
			.toLowerCase()
			.includes(normalizedSearch);
	});

	$: if (isOpen !== previousOpen) {
		previousOpen = isOpen;
		if (isOpen) {
			resetSelection();
			loadItems();
		}
	}

	function itemName(item: LibraryItem): string {
		if (kind === 'prompt') {
			const prompt = item as Prompt;
			return prompt.name || prompt.display_name || itemPreview(item) || 'Untitled Prompt';
		}
		return item.name || 'Untitled Segment Template';
	}

	function itemPreview(item: LibraryItem): string {
		if ('flattened_text' in item && item.flattened_text) return item.flattened_text;
		return flattenRichSegments(item.segments);
	}

	function itemDescription(item: LibraryItem): string {
		return 'description' in item ? item.description || '' : '';
	}

	function resetSelection() {
		searchTerm = '';
		selectedId = null;
		applyMode = null;
		showReplaceConfirmation = false;
	}

	async function loadItems() {
		loading = true;
		error = '';
		try {
			if (kind === 'prompt') {
				const response = await api.listPrompts({ limit: 100 });
				if (!response.success) throw new Error(response.error || 'Failed to load Prompts');
				items = response.data?.items || [];
			} else {
				const response = await api.listSegmentTemplates();
				if (!response.success) {
					throw new Error(response.error || 'Failed to load Segment Templates');
				}
				items = response.data?.templates || [];
			}
		} catch (loadError) {
			logger.error(`Failed to load ${kind} library:`, loadError);
			error = loadError instanceof Error ? loadError.message : `Failed to load ${kind} library`;
			items = [];
		} finally {
			loading = false;
		}
	}

	function handleClose() {
		resetSelection();
		dispatch('close');
	}

	function requestApply() {
		if (!selectedItem || !applyMode) return;
		if (applyMode === 'replace' && targetHasMeaningfulContent) {
			showReplaceConfirmation = true;
			return;
		}
		applySelection();
	}

	function applySelection() {
		if (!selectedItem || !applyMode) return;
		dispatch('apply', { item: selectedItem, mode: applyMode });
		resetSelection();
	}
</script>

<BaseModal
	{isOpen}
	title={kind === 'prompt' ? 'Apply Prompt' : 'Apply Segment Template'}
	sizeClass="md:max-w-3xl md:w-full md:max-h-[85vh]"
	on:close={handleClose}
>
	<svelte:fragment slot="headerIcon">
		<Icon name={kind === 'prompt' ? 'book-open' : 'layout-template'} className="h-5 w-5 text-fg-muted" />
	</svelte:fragment>

	<div class="space-y-4 p-4 sm:p-6">
		<div>
			<label for="segment-library-search" class="mb-1.5 block text-xs font-medium text-fg-muted">
				Search {kind === 'prompt' ? 'Prompts' : 'Segment Templates'}
			</label>
			<input
				id="segment-library-search"
				type="search"
				class="input w-full"
				bind:value={searchTerm}
				placeholder={kind === 'prompt' ? 'Search prompt names and content…' : 'Search template names and content…'}
			/>
		</div>

		{#if loading}
			<div class="flex justify-center py-12"><Spinner size="lg" /></div>
		{:else if error}
			<Alert variant="danger" live="polite">{error}</Alert>
		{:else if filteredItems.length === 0}
			<div class="rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">
				No {kind === 'prompt' ? 'Prompts' : 'Segment Templates'} found.
			</div>
		{:else}
			<div class="max-h-[46vh] space-y-2 overflow-y-auto pr-1" role="listbox" aria-label={`${kind} library`}>
				{#each filteredItems as item (item.id)}
					<button
						type="button"
						class="w-full rounded-lg border p-3 text-left transition-colors {selectedId === item.id
							? 'border-signal bg-signal/10'
							: 'border-line bg-surface-2 hover:border-line-hover hover:bg-surface-3'}"
						role="option"
						aria-selected={selectedId === item.id}
						on:click={() => (selectedId = item.id)}
					>
						<div class="flex items-start justify-between gap-3">
							<div class="min-w-0 flex-1">
								<div class="truncate text-sm font-medium text-fg">{itemName(item)}</div>
								<div class="mt-1 line-clamp-2 text-xs leading-relaxed text-fg-muted">
									{itemPreview(item) || 'Blank starter segments'}
								</div>
							</div>
							<span class="flex-shrink-0 font-mono text-2xs text-fg-subtle">
								{item.segments.length} segment{item.segments.length === 1 ? '' : 's'}
							</span>
						</div>
					</button>
				{/each}
			</div>
		{/if}

		<fieldset>
			<legend class="mb-2 text-xs font-medium text-fg-muted">Choose how to apply</legend>
			<div class="grid grid-cols-3 gap-2">
				{#each applyModes as mode}
					<label
						class="cursor-pointer rounded-lg border p-2.5 text-center text-xs font-medium capitalize transition-colors {applyMode === mode
							? 'border-signal bg-signal/10 text-fg'
							: 'border-line text-fg-muted hover:border-line-hover'}"
					>
						<input class="sr-only" type="radio" name="segment-apply-mode" value={mode} bind:group={applyMode} />
						{mode}
					</label>
				{/each}
			</div>
			<p class="mt-2 text-2xs text-fg-subtle">
				Incoming segments are detached copies. Other generation settings are not changed.
			</p>
		</fieldset>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-2 px-4 py-3 sm:px-6">
			<Button variant="secondary" onclick={handleClose}>Cancel</Button>
			<Button variant="primary" disabled={!selectedItem || !applyMode} onclick={requestApply}>
				Apply
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>

<ConfirmModal
	isOpen={showReplaceConfirmation}
	title="Replace current segments?"
	message="This will replace the meaningful content in this editor. Preset, mode, form values, session, backend, seed, tags, and generation settings stay unchanged."
	variant="warning"
	on:confirm={() => {
		showReplaceConfirmation = false;
		applySelection();
	}}
	on:cancel={() => (showReplaceConfirmation = false)}
/>
