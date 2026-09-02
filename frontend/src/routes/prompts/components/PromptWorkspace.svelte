<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import MediaPreview from '$lib/components/MediaPreview.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { MasterDetailLayout, DetailPane } from '$lib/components/master-detail';
	import { Pane, PaneRow } from '$lib/components/pane';
	import { Badge, Button, Card, EmptyState, IconButton, Input, Spinner } from '$lib/components/ui';
	import type { Prompt, PromptGenerationItem, PromptUsageHint, Segment } from '$lib/types/segments';
	import type { GenerationFile } from '$lib/types/history';
	import { createBlankEditorSegment, toEditorSegment, toRichSegment } from '$lib/utils/richSegments';
	import { leadIndex } from '$lib/generation/leadFile';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { promptsCollectionsStore } from '$lib/stores/collections';
	import AddToCollectionMenu from '$lib/components/collections/AddToCollectionMenu.svelte';
	import NewPromptComposerModal from './NewPromptComposerModal.svelte';
	import PromptModelField from './PromptModelField.svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import {
		DUPLICATE_THRESHOLD_PRESETS,
		removePromptsFromDuplicateGroup,
		type DuplicateGroup
	} from '$lib/utils/duplicatePrompts';

	let prompts: Prompt[] = [];
	let selected: Prompt | null = null;
	let name = '';
	let usageHint: PromptUsageHint | '' = '';
	let editModelId: string | null = null;
	let editModelLabel: string | null = null;
	let editorSegments: Segment[] = [createBlankEditorSegment()];
	let loading = false;
	let saving = false;
	let query = '';
	let usageFilter: 'all' | PromptUsageHint = 'all';
	let source = '';
	let modelId = '';
	let models: Array<{
		id: string;
		filename?: string;
		name?: string;
		model_type?: string;
		tags?: Array<{ name?: string } | string>;
		providers?: any[];
	}> = [];
	let showModelFilter = false;
	let selectedModelName = '';
	let showComposer = false;
	let structureExpanded = true;
	let collectionId: string | undefined = undefined;
	let addToCollectionOpen = false;

	$: promptCollections = $promptsCollectionsStore.collections;

	// "Used in generations" — usage summary for whichever prompt is selected.
	const USAGE_STRIP_LIMIT = 6;
	let usageItems: PromptGenerationItem[] = [];
	let usageTotal = 0;
	let usageLoading = false;
	let usageRequestId = 0;

	type DuplicateAction = { kind: 'delete' | 'keep'; groupIndex: number; promptId: string };

	let showDuplicatesModal = false;
	let duplicatesLoading = false;
	let duplicateGroups: DuplicateGroup[] | null = null;
	let duplicateThreshold = 0.1;
	let pendingDuplicateAction: DuplicateAction | null = null;
	let duplicateActionBusy = false;
	$: selectedModelLabel = modelId
		? selectedModelName || modelDisplayName(models.find((model) => model.id === modelId)) || 'Selected model'
		: 'All models';

	// Exposed to the page-level toolbar via bind:this — the models list, the
	// current model filter and the duplicate-scan state all live here, so the
	// toolbar triggers these instead of owning parallel copies of them.
	export function openComposer() {
		showComposer = true;
	}
	export function openDuplicatesScan() {
		openDuplicatesModal();
	}
	// Called by the toolbar once a plugin-hosted import modal reports it
	// created prompts, so the list reflects them without the toolbar owning
	// its own copy of `loadPrompts`.
	export async function reloadPrompts() {
		await loadPrompts();
	}
	// Called by the page's PromptsSidebar (a sibling, not a child) on folder
	// selection - PromptWorkspace has no dedicated filter store, so its other
	// filters (model, source, usage) follow the same bind:this pattern.
	export async function setCollectionFilter(id: string | undefined) {
		collectionId = id;
		await loadPrompts();
	}

	onMount(async () => {
		await Promise.all([loadPrompts(), loadModels(), promptsCollectionsStore.load()]);
	});

	async function loadModels() {
		try {
			const response = await api.getModels({ limit: 200, sort_by: 'filename', sort_order: 'asc' });
			models = response.data?.models || [];
		} catch {
			models = [];
		}
	}

	async function selectModelFilter(model: any | null) {
		modelId = model?.id || '';
		selectedModelName = model ? modelDisplayName(model) : '';
		if (model && !models.some((item) => item.id === model.id)) {
			models = [model, ...models];
		}
		showModelFilter = false;
		await loadPrompts();
	}

	async function loadPrompts() {
		loading = true;
		try {
			const response = query.trim()
				? await api.searchPrompts({
						q: query.trim(),
						limit: 100,
						model_id: modelId || undefined,
						source_provider: source || undefined
					})
				: await api.listPrompts({
						limit: 100,
						model_id: modelId || undefined,
						source_provider: source || undefined,
						usage_hint: usageFilter === 'all' ? undefined : usageFilter,
						collection_id: collectionId,
						sort_by: 'updated_at'
					});
			prompts = response.success
				? (Array.isArray(response.data) ? response.data : response.data?.items || [])
				: [];
			if (selected) {
				const refreshed = prompts.find((prompt) => prompt.id === selected?.id);
				if (refreshed) selectPrompt(refreshed);
			}
		} catch {
			toasts.error('Failed to load prompts');
		} finally {
			loading = false;
		}
	}

	function selectUsageFilter(next: 'all' | PromptUsageHint) {
		if (usageFilter === next) return;
		usageFilter = next;
		loadPrompts();
	}

	function selectPrompt(prompt: Prompt) {
		selected = prompt;
		name = prompt.name || '';
		usageHint = prompt.usage_hint || '';
		editModelId = prompt.model_id ?? null;
		editModelLabel = prompt.model_name ?? null;
		editorSegments = prompt.segments.map((segment) => toEditorSegment(segment));
		if (!editorSegments.length) editorSegments = [createBlankEditorSegment()];
		loadUsage(prompt.id);
	}

	async function loadUsage(promptId: string) {
		const requestId = ++usageRequestId;
		usageLoading = true;
		usageItems = [];
		usageTotal = 0;
		try {
			const response = await api.getPromptGenerations(promptId, { limit: USAGE_STRIP_LIMIT });
			if (requestId !== usageRequestId) return;
			if (response.success && response.data) {
				usageItems = response.data.items;
				usageTotal = response.data.total;
			}
		} catch {
			if (requestId === usageRequestId) toasts.error('Failed to load usage history');
		} finally {
			if (requestId === usageRequestId) usageLoading = false;
		}
	}

	function resetPrompt() {
		if (selected) selectPrompt(selected);
	}

	async function savePrompt() {
		if (!selected) return;
		saving = true;
		const payload = {
			name: name.trim() || null,
			usage_hint: usageHint || null,
			model_id: editModelId,
			segments: editorSegments.map(toRichSegment)
		};
		try {
			const response = await api.replacePrompt(selected.id, payload);
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success('Prompt updated');
			await loadPrompts();
			selectPrompt(response.data);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save prompt');
		} finally {
			saving = false;
		}
	}

	async function deletePrompt() {
		if (!selected) return;
		if (
			!(await confirmDialog({
				title: 'Delete',
				message: `Delete “${selected.display_name}”?`,
				variant: 'danger'
			}))
		)
			return;
		try {
			await api.deletePrompt(selected.id);
			toasts.success('Prompt deleted');
			selected = null;
			await loadPrompts();
		} catch {
			toasts.error('Failed to delete prompt');
		}
	}

	async function handleAddToCollection(targetCollectionId: string): Promise<boolean> {
		if (!selected) return false;
		try {
			const response = await api.addPromptsToCollection(targetCollectionId, [selected.id], 'prompts');
			if (response.success) {
				await promptsCollectionsStore.load();
				toasts.success('Added to collection');
				return true;
			}
			toasts.error('Failed to add to collection');
			return false;
		} catch {
			toasts.error('Failed to add to collection');
			return false;
		}
	}

	async function handleCreateAndAddToCollection(name: string): Promise<boolean> {
		const created = await promptsCollectionsStore.create(name);
		const collection = created.success ? created.data?.collection : undefined;
		if (!collection) {
			toasts.error('Failed to create collection');
			return false;
		}
		return await handleAddToCollection(collection.id);
	}

	async function duplicatePrompt() {
		if (!selected) return;
		try {
			const response = await api.createPrompt({
				name: selected.name ? `${selected.name} copy` : null,
				usage_hint: selected.usage_hint ?? null,
				model_id: selected.model_id ?? null,
				segments: selected.segments
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Duplicate failed');
			toasts.success('Prompt duplicated');
			await loadPrompts();
			selectPrompt(response.data);
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to duplicate prompt');
		}
	}

	function handlePromptCreated(prompt: Prompt) {
		loadPrompts().then(() => selectPrompt(prompt));
	}

	async function openDuplicatesModal() {
		showDuplicatesModal = true;
		await refreshDuplicates();
	}

	function closeDuplicatesModal() {
		showDuplicatesModal = false;
		duplicateGroups = null;
		pendingDuplicateAction = null;
	}

	async function refreshDuplicates() {
		duplicatesLoading = true;
		duplicateGroups = null;
		pendingDuplicateAction = null;
		try {
			const response = await api.findDuplicatePrompts({
				model_id: modelId || undefined,
				threshold: duplicateThreshold
			});
			duplicateGroups = response.data?.groups || [];
		} catch {
			toasts.error('Duplicate scan failed');
			duplicateGroups = [];
		} finally {
			duplicatesLoading = false;
		}
	}

	async function selectDuplicateThreshold(value: number) {
		if (duplicateThreshold === value || duplicatesLoading) return;
		duplicateThreshold = value;
		await refreshDuplicates();
	}

	function armDuplicateAction(action: DuplicateAction) {
		pendingDuplicateAction = action;
	}

	function cancelDuplicateAction() {
		pendingDuplicateAction = null;
	}

	async function forgetDeletedSelection(removedIds: string[]) {
		if (selected && removedIds.includes(selected.id)) selected = null;
		await loadPrompts();
	}

	async function confirmDeleteDuplicate(groupIndex: number, promptId: string) {
		duplicateActionBusy = true;
		try {
			await api.deletePrompt(promptId);
			if (duplicateGroups) {
				duplicateGroups = removePromptsFromDuplicateGroup(duplicateGroups, groupIndex, [promptId]);
			}
			toasts.success('Prompt deleted');
			await forgetDeletedSelection([promptId]);
		} catch {
			toasts.error('Failed to delete prompt');
		} finally {
			duplicateActionBusy = false;
			pendingDuplicateAction = null;
		}
	}

	async function confirmKeepOnly(groupIndex: number, keepId: string) {
		if (!duplicateGroups) return;
		const removeIds = duplicateGroups[groupIndex].prompts
			.filter((prompt) => prompt.id !== keepId)
			.map((prompt) => prompt.id);
		if (!removeIds.length) return;
		duplicateActionBusy = true;
		try {
			await api.bulkDeletePrompts(removeIds);
			duplicateGroups = removePromptsFromDuplicateGroup(duplicateGroups, groupIndex, removeIds);
			toasts.success(`Removed ${removeIds.length} duplicate${removeIds.length === 1 ? '' : 's'}`);
			await forgetDeletedSelection(removeIds);
		} catch {
			toasts.error('Failed to remove duplicates');
		} finally {
			duplicateActionBusy = false;
			pendingDuplicateAction = null;
		}
	}

	function sourceLabel(prompt: Prompt) {
		return prompt.source_provider || 'manual';
	}

	function usageLabel(prompt: Prompt): string {
		if (!prompt.usage_count) return '';
		const when = prompt.last_used_at ? timeAgo(prompt.last_used_at) : '';
		return when ? `${prompt.usage_count}× · ${when}` : `${prompt.usage_count}×`;
	}

	/** Same lead-file rule GenerationCard/HistoryGrid use, so the strip's
	 *  thumbnail matches what the history page itself would show first. */
	function leadFileOf(item: PromptGenerationItem): GenerationFile | null {
		const media = item.files.filter(
			(file) => file.is_final !== false && ['image', 'video', 'audio', 'mesh'].includes(file.file_type.toLowerCase())
		);
		if (!media.length) return null;
		return media[leadIndex(media)];
	}
</script>

<div class="h-full">
	<MasterDetailLayout
		leftWidth={360}
		minWidth={280}
		maxWidth={520}
		storageKey="prompt-library-panel-width"
	>
		<svelte:fragment slot="list">
			<Pane
				label="Prompts"
				count={prompts.length}
				searchable
				bind:search={query}
				searchPlaceholder="Search name, content, tags..."
				onSearch={loadPrompts}
				{loading}
				isEmpty={prompts.length === 0}
			>
				{#snippet filters()}
					<div class="space-y-2 border-b border-line p-3">
						<div class="flex flex-wrap items-center gap-1.5">
							{#each [{ id: 'all', label: 'All' }, { id: 'positive', label: 'Positive' }, { id: 'negative', label: 'Negative' }] as pill (pill.id)}
								<button
									type="button"
									class="rounded px-2.5 py-1 text-xs font-medium transition-colors {usageFilter === pill.id
										? 'border border-signal/25 bg-signal/10 text-signal'
										: 'border border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
									onclick={() => selectUsageFilter(pill.id as 'all' | PromptUsageHint)}
								>
									{pill.label}
								</button>
							{/each}
						</div>
						<div class="grid grid-cols-2 gap-2">
							<button
								class="input flex min-w-0 items-center gap-1.5 py-1.5 text-left text-xs"
								title={selectedModelLabel}
								onclick={() => (showModelFilter = true)}
							>
								<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
								<span class="min-w-0 flex-1 truncate">{selectedModelLabel}</span>
								<Icon name="chevron-down" className="h-3 w-3 flex-shrink-0 text-fg-subtle" />
							</button>
							<label>
								<span class="sr-only">Source</span>
								<select class="input py-1.5 text-xs" bind:value={source} onchange={loadPrompts}>
									<option value="">All sources</option>
									<option value="manual">Manual</option>
									<option value="text_import">Text import</option>
									<option value="civitai">CivitAI</option>
								</select>
							</label>
						</div>
					</div>
				{/snippet}

				{#snippet empty()}
					<div class="px-4 py-10 text-center">
						<Icon name="document" className="mx-auto mb-3 h-9 w-9 text-fg-disabled" strokeWidth={1.5} />
						<p class="text-sm text-fg-muted">
							{query.trim() || modelId || source || usageFilter !== 'all' || collectionId
								? 'No prompts match these filters'
								: 'No prompts yet'}
						</p>
						{#if !query.trim() && !modelId && !source && usageFilter === 'all' && !collectionId}
							<Button class="mt-3" size="xs" variant="ghost" icon="plus" onclick={openComposer}>
								Create your first prompt
							</Button>
						{/if}
					</div>
				{/snippet}

				{#snippet children()}
					{#each prompts as prompt (prompt.id)}
						{#snippet meta()}
							<div class="mt-2 flex flex-wrap items-center gap-1.5">
								<Badge size="sm">
									{prompt.segments.length} segment{prompt.segments.length === 1 ? '' : 's'}
								</Badge>
								<Badge size="sm" variant="info">{sourceLabel(prompt)}</Badge>
								{#if prompt.usage_hint}
									<Badge size="sm" variant={prompt.usage_hint === 'negative' ? 'danger' : 'success'}>
										{prompt.usage_hint}
									</Badge>
								{/if}
								<span class="flex-1"></span>
								{#if usageLabel(prompt)}
									<span class="font-mono text-2xs tabular-nums text-fg-subtle">{usageLabel(prompt)}</span>
								{/if}
							</div>
						{/snippet}
						<PaneRow
							title={prompt.display_name}
							subtitle={prompt.flattened_text || 'Empty composition'}
							selected={selected?.id === prompt.id}
							onclick={() => selectPrompt(prompt)}
							{meta}
						/>
					{/each}
				{/snippet}
			</Pane>
		</svelte:fragment>

		<svelte:fragment slot="detail">
			{#if !selected}
				<div class="flex h-full items-center justify-center">
					<EmptyState
						icon="document"
						title="No prompt selected"
						description="Pick a prompt from the list, or start a new one from the toolbar."
					>
						{#snippet actions()}
							<Button size="sm" variant="primary" icon="plus" onclick={openComposer}>Create a prompt</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<DetailPane
					title="Edit Prompt"
					showDelete
					showCancel
					saveLabel="Save changes"
					isLoading={saving}
					on:save={savePrompt}
					on:cancel={resetPrompt}
					on:delete={deletePrompt}
				>
					{#snippet headerActions()}
						{#if usageHint}
							<Badge size="sm" variant={usageHint === 'negative' ? 'danger' : 'success'}>{usageHint}</Badge>
						{/if}
						<Button size="sm" icon="copy" onclick={duplicatePrompt}>Duplicate</Button>
						<AddToCollectionMenu
							collections={promptCollections}
							open={addToCollectionOpen}
							placement="down"
							onToggle={() => (addToCollectionOpen = !addToCollectionOpen)}
							onClose={() => (addToCollectionOpen = false)}
							onAdd={handleAddToCollection}
							onCreateAndAdd={handleCreateAndAddToCollection}
						/>
					{/snippet}

					<div class="space-y-4">
						<Card padding="sm">
							<div class="mb-3 flex items-center gap-2">
								<span class="font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle">
									Used in generations
								</span>
								{#if usageTotal > 0}
									<Badge size="sm" variant="signal">
										<span class="font-mono tabular-nums">{usageTotal}</span>
										use{usageTotal === 1 ? '' : 's'}
									</Badge>
								{/if}
								<span class="flex-1"></span>
								{#if usageItems[0]?.created_at}
									<span class="font-mono text-2xs tabular-nums text-fg-subtle">
										Last used {timeAgo(usageItems[0].created_at)}
									</span>
								{/if}
							</div>

							{#if usageLoading}
								<div class="flex h-16 items-center justify-center">
									<Spinner size="sm" />
								</div>
							{:else if usageItems.length === 0}
								<p class="text-xs text-fg-subtle">Not used in any generation yet.</p>
							{:else}
								<div class="flex gap-2.5 overflow-x-auto pb-1">
									{#each usageItems as item (item.id)}
										{@const leadFile = leadFileOf(item)}
										<div class="w-16 flex-shrink-0">
											<div
												class="h-16 w-16 overflow-hidden rounded border border-line-strong bg-surface-2"
												title="{item.preset_name || item.preset_id || 'Unknown preset'} · {item.created_at
													? timeAgo(item.created_at)
													: ''}"
											>
												{#if leadFile}
													<MediaPreview
														file={leadFile}
														generationId={item.id}
														thumbnailSize="small"
														loadFullOnClick={false}
													/>
												{/if}
											</div>
											<div class="mt-1 truncate text-center font-mono text-3xs text-fg-subtle">
												{item.preset_name || item.preset_id || '—'}
											</div>
											{#if item.created_at}
												<div class="text-center font-mono text-3xs tabular-nums text-fg-disabled">
													{timeAgo(item.created_at)}
												</div>
											{/if}
										</div>
									{/each}
									{#if usageTotal > usageItems.length}
										<div class="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded border border-dashed border-line-strong text-xs font-medium text-fg-subtle">
											+{usageTotal - usageItems.length}
										</div>
									{/if}
								</div>
							{/if}
						</Card>

						<Card padding="sm">
							<h3 class="label mb-3">Prompt details</h3>
							<div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_12rem]">
								<label>
									<span class="mb-1.5 block text-xs font-medium text-fg-muted">
										Name <span class="font-normal text-fg-subtle">(optional)</span>
									</span>
									<Input
										class="text-sm"
										bind:value={name}
										placeholder="Content preview is used when unnamed"
									/>
								</label>
								<label>
									<span class="mb-1.5 block text-xs font-medium text-fg-muted">Usage hint</span>
									<select class="input text-sm" bind:value={usageHint}>
										<option value="">None</option>
										<option value="positive">Positive</option>
										<option value="negative">Negative</option>
									</select>
								</label>
							</div>
							<div class="mt-3">
								<PromptModelField
									modelId={editModelId}
									modelLabel={editModelLabel}
									disabled={saving}
									onChange={(model) => {
										editModelId = model?.id ?? null;
										editModelLabel = model?.label ?? null;
									}}
								/>
							</div>
						</Card>

						<Card padding="none" class="overflow-hidden">
							<button
								type="button"
								class="flex w-full items-center gap-2 border-b border-line px-4 py-2.5"
								onclick={() => (structureExpanded = !structureExpanded)}
							>
								<span class="font-mono text-2xs font-semibold uppercase tracking-[0.13em] text-fg-subtle">
									Prompt structure
								</span>
								<span class="flex-1"></span>
								<span class="font-mono text-2xs tabular-nums text-fg-subtle">
									{editorSegments.length} segment{editorSegments.length === 1 ? '' : 's'}
								</span>
								<Icon
									name="chevron-down"
									className="h-3 w-3 text-fg-subtle transition-transform {structureExpanded ? 'rotate-180' : ''}"
								/>
							</button>
							{#if structureExpanded}
								<div class="p-4">
									<SegmentedPromptEditor
										segments={editorSegments}
										label="Prompt composition"
										compact
										showLibraryActions={false}
										on:segmentsChange={(event) => (editorSegments = event.detail)}
									/>
								</div>
							{/if}
						</Card>

						{#if selected?.source_url}
							<Card padding="sm" class="text-xs text-fg-muted shadow-none">
								<div class="flex items-start gap-2">
									<Icon name="info" className="mt-0.5 h-4 w-4 flex-shrink-0 text-info" />
									<p>
										Browsing metadata: {selected.model_name || selected.base_model || 'no model'} ·
										{sourceLabel(selected)} ·
										<a class="text-signal hover:underline" href={selected.source_url} target="_blank" rel="noreferrer">
											view source
										</a>.
										Applying this Prompt never changes generation settings.
									</p>
								</div>
							</Card>
						{/if}
					</div>
				</DetailPane>
			{/if}
		</svelte:fragment>
	</MasterDetailLayout>
</div>

{#if showModelFilter}
	<ModelAssignmentModal
		selectionMode="single"
		selectedModelId={modelId || null}
		allowClear={true}
		title="Filter prompts by model"
		subtitle="Search the model catalog or narrow it by type, then select one model."
		onSelect={selectModelFilter}
		onClear={() => selectModelFilter(null)}
		onClose={() => (showModelFilter = false)}
	/>
{/if}

{#if showComposer}
	<NewPromptComposerModal
		initialModelId={modelId || null}
		initialModelLabel={modelId ? selectedModelLabel : null}
		onClose={() => (showComposer = false)}
		onCreated={handlePromptCreated}
	/>
{/if}

{#if showDuplicatesModal}
	<BaseModal
		isOpen={true}
		title="Duplicate prompts"
		sizeClass="md:max-w-2xl md:w-full"
		on:close={closeDuplicatesModal}
	>
		<svelte:fragment slot="headerIcon">
			<Icon name="copy" className="h-5 w-5 flex-shrink-0 text-fg-muted" />
		</svelte:fragment>

		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-6 py-3">
			<div class="flex items-center gap-2">
				<span class="text-xs font-medium text-fg-muted">Similarity</span>
				<div class="inline-flex overflow-hidden rounded border border-line-strong">
					{#each DUPLICATE_THRESHOLD_PRESETS as preset (preset.value)}
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-50 {duplicateThreshold ===
							preset.value
								? 'bg-signal/10 text-signal'
								: 'bg-surface-1 text-fg-muted hover:bg-surface-3/50'}"
							disabled={duplicatesLoading}
							onclick={() => selectDuplicateThreshold(preset.value)}
						>
							{preset.label}
						</button>
					{/each}
				</div>
			</div>
			{#if !duplicatesLoading && duplicateGroups}
				<Badge>
					<span class="font-mono tabular-nums">{duplicateGroups.length}</span>
					group{duplicateGroups.length === 1 ? '' : 's'}
				</Badge>
			{/if}
		</div>

		<div class="max-h-[60vh] min-h-[12rem] space-y-3 overflow-y-auto p-6">
			{#if duplicatesLoading || duplicateGroups === null}
				<div class="flex h-32 items-center justify-center">
					<Spinner size="lg" />
				</div>
			{:else if duplicateGroups.length === 0}
				<EmptyState
					icon="check"
					title="No duplicates found"
					description="Every saved prompt at this similarity level is unique. Try Loose if you expect near-matches to show up."
					compact
				/>
			{:else}
				{#each duplicateGroups as group, groupIndex (group.prompts.map((prompt) => prompt.id).join('-'))}
					<Card padding="sm" class="shadow-none">
						<div class="mb-2 flex items-center justify-between gap-3">
							<div class="flex items-center gap-2">
								<Badge variant="signal">
									<span class="font-mono tabular-nums">{Math.round(group.similarity * 100)}%</span> match
								</Badge>
								<Badge size="sm">{group.prompts.length} prompts</Badge>
							</div>
						</div>
						<div class="divide-y divide-line">
							{#each group.prompts as prompt, promptIndex (prompt.id)}
								<div class="py-2 first:pt-0 last:pb-0">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0 flex-1">
											<div class="flex items-center gap-1.5">
												<p class="truncate text-sm font-medium text-fg">{prompt.display_name}</p>
												{#if promptIndex === 0}
													<Badge size="sm" variant="success">Suggested keep</Badge>
												{/if}
											</div>
											<p class="mt-0.5 line-clamp-2 text-xs text-fg-subtle">
												{prompt.flattened_text || 'Empty composition'}
											</p>
										</div>
										{#if pendingDuplicateAction?.groupIndex === groupIndex && pendingDuplicateAction.promptId === prompt.id}
											{@const action = pendingDuplicateAction}
											<div class="flex flex-shrink-0 items-center gap-1.5">
												<span class="text-xs text-fg-muted">
													{action.kind === 'keep' ? 'Remove the rest?' : 'Remove this prompt?'}
												</span>
												<Button
													size="xs"
													variant="ghost"
													disabled={duplicateActionBusy}
													onclick={cancelDuplicateAction}
												>
													Cancel
												</Button>
												<Button
													size="xs"
													variant="danger"
													loading={duplicateActionBusy}
													onclick={() =>
														action.kind === 'keep'
															? confirmKeepOnly(groupIndex, prompt.id)
															: confirmDeleteDuplicate(groupIndex, prompt.id)}
												>
													Remove
												</Button>
											</div>
										{:else}
											<div class="flex flex-shrink-0 items-center gap-1.5">
												<Button
													size="xs"
													variant="ghost"
													disabled={duplicateActionBusy}
													onclick={() =>
														armDuplicateAction({ kind: 'keep', groupIndex, promptId: prompt.id })}
												>
													Keep only this
												</Button>
												<IconButton
													icon="trash"
													label="Delete this prompt"
													disabled={duplicateActionBusy}
													onclick={() =>
														armDuplicateAction({ kind: 'delete', groupIndex, promptId: prompt.id })}
												/>
											</div>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					</Card>
				{/each}
			{/if}
		</div>

		<svelte:fragment slot="footer">
			<div class="flex justify-end px-6 py-4">
				<Button onclick={closeDuplicatesModal}>Close</Button>
			</div>
		</svelte:fragment>
	</BaseModal>
{/if}
