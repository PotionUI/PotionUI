<script lang="ts">
	import SegmentedPromptEditor from '$lib/components/SegmentedPromptEditor.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import JustifiedGenerationGallery from '$lib/components/JustifiedGenerationGallery.svelte';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DetailFooter, KVGrid, KVItem } from '$lib/components/detail';
	import { Badge, Button, IconButton, Input, Spinner } from '$lib/components/ui';
	import AddToCollectionMenu from '$lib/components/collections/AddToCollectionMenu.svelte';
	import PromptModelField from './PromptModelField.svelte';
	import { normalizeVariableDef } from '$lib/utils/variableDefs';
	import type { VariablesMap, VariableDef } from '$lib/utils/variableDefs';
	import type { Prompt, PromptUsageHint, Segment } from '$lib/types/segments';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import type { CollectionLike } from '$lib/components/collections/types';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { hasMeaningfulSegments } from '$lib/utils/richSegments';
	import { summarizePromptVariables } from '$lib/prompts/promptCardDisplay';

	const NAME_FIELD_ID = 'prompt-detail-name-field';

	let {
		mode,
		prompt,
		name = $bindable(),
		usageHint = $bindable(),
		editModelId = $bindable(),
		editModelLabel = $bindable(),
		editorSegments = $bindable(),
		editorVariables = $bindable(),
		usageItems,
		usageTotal,
		usageLoading,
		saving,
		dirtyCount,
		promptCollections,
		onBack,
		onSave,
		onDiscard,
		onDelete,
		onDuplicate,
		onUse,
		onAddToCollection,
		onCreateAndAddToCollection,
		onRemoveFromCollection,
		onOpenVariableManager,
		onVariableDefChange
	}: {
		mode: 'create' | 'edit';
		prompt: Prompt | null;
		name: string;
		usageHint: PromptUsageHint | '';
		editModelId: string | null;
		editModelLabel: string | null;
		editorSegments: Segment[];
		editorVariables: VariablesMap;
		usageItems: GenerationHistoryItem[];
		usageTotal: number;
		usageLoading: boolean;
		saving: boolean;
		dirtyCount: number;
		promptCollections: CollectionLike[];
		onBack: () => void;
		onSave: () => void;
		onDiscard: () => void;
		onDelete: () => void;
		onDuplicate: () => void;
		onUse: () => void;
		onAddToCollection: (collectionId: string) => Promise<boolean>;
		onCreateAndAddToCollection: (name: string) => Promise<boolean>;
		onRemoveFromCollection: (collectionId: string) => Promise<boolean>;
		onOpenVariableManager: () => void;
		onVariableDefChange: (name: string, def: VariableDef) => void;
	} = $props();

	let addToCollectionOpen = $state(false);
	let selectedGeneration = $state<GenerationHistoryItem | null>(null);
	let selectedFileIndex = $state(0);

	const title = $derived(mode === 'create' ? 'New prompt' : prompt?.display_name || 'Untitled');
	const memberships = $derived(prompt?.collections ?? []);
	const variablesSummary = $derived(summarizePromptVariables(editorVariables));
	const variableRows = $derived(
		Object.entries(editorVariables).map(([varName, stored]) => {
			const def = normalizeVariableDef(stored);
			return {
				name: varName,
				kind: def.type === 'choice' ? `${def.mode} · ${def.options.length} option${def.options.length === 1 ? '' : 's'}` : 'text'
			};
		})
	);

	function openGeneration(generation: GenerationHistoryItem, fileIndex: number) {
		selectedGeneration = generation;
		selectedFileIndex = fileIndex;
	}

	function closeGenerationModal() {
		selectedGeneration = null;
		selectedFileIndex = 0;
	}
</script>

<div class="flex h-full flex-col">
	<DetailHeader {title} icon="document" backLabel="Prompts" {onBack}>
		{#snippet chips()}
			{#if usageHint}
				<Badge size="sm" variant={usageHint === 'negative' ? 'danger' : 'success'}>{usageHint}</Badge>
			{/if}
			{#if variablesSummary.count > 0}
				<Badge size="sm" class="font-mono tabular-nums">
					{variablesSummary.count}{variablesSummary.linked > 0 ? ` · ${variablesSummary.linked} linked` : ''}
				</Badge>
			{/if}
		{/snippet}
		{#snippet subtitle()}
			{#if mode === 'edit' && prompt}
				<span>used {prompt.usage_count ?? 0}&times;</span>
				{#if prompt.created_at}<span>&middot; created {timeAgo(prompt.created_at)}</span>{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if mode === 'edit'}
				<Tooltip text="Duplicate prompt">
					<IconButton icon="copy" label="Duplicate prompt" onclick={onDuplicate} />
				</Tooltip>
				<Tooltip text="Delete prompt">
					<IconButton icon="trash" label="Delete prompt" onclick={onDelete} />
				</Tooltip>
			{/if}
			<Button size="sm" variant="primary" onclick={onUse}>Use in Generate</Button>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Prompt details">
					<div class="grid items-end gap-3 lg:grid-cols-[minmax(0,1fr)_10rem_minmax(0,18rem)]">
						<label class="min-w-0">
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">
								Name <span class="font-normal text-fg-subtle">(optional)</span>
							</span>
							<Input
								id={NAME_FIELD_ID}
								class="text-sm"
								bind:value={name}
								placeholder="Content preview is used when unnamed"
							/>
						</label>
						<label class="min-w-0">
							<span class="mb-1.5 block text-xs font-medium text-fg-muted">Usage hint</span>
							<select class="input text-sm" bind:value={usageHint}>
								<option value="">None</option>
								<option value="positive">Positive</option>
								<option value="negative">Negative</option>
							</select>
						</label>
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
				</DetailSection>

				<SegmentedPromptEditor
					segments={editorSegments}
					label="Prompt composition"
					showLibraryActions={false}
					variables={editorVariables}
					{onVariableDefChange}
					{onOpenVariableManager}
					on:segmentsChange={(event) => (editorSegments = event.detail)}
				/>

				{#if mode === 'edit'}
					<DetailSection label="Used in generations">
						{#snippet headerExtra()}
							{#if usageTotal > 0}
								<Badge size="sm" variant="signal">
									<span class="font-mono tabular-nums">{usageTotal}</span>
									use{usageTotal === 1 ? '' : 's'}
								</Badge>
							{/if}
						{/snippet}
						{#if usageLoading}
							<div class="flex h-16 items-center justify-center">
								<Spinner size="sm" />
							</div>
						{:else if usageItems.length === 0}
							<p class="text-xs text-fg-subtle">Not used in any generation yet.</p>
						{:else}
							<JustifiedGenerationGallery generations={usageItems} onOpen={openGeneration} showActions={false} />
							{#if usageTotal > usageItems.length}
								<p class="mt-3 font-mono text-xs text-fg-subtle">
									+{usageTotal - usageItems.length} more
								</p>
							{/if}
						{/if}
					</DetailSection>
				{/if}
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Variables">
					{#if variableRows.length === 0}
						<p class="text-xs text-fg-subtle">No variables used in this prompt.</p>
					{:else}
						<ul class="space-y-2">
							{#each variableRows as row (row.name)}
								<li class="flex items-center justify-between gap-2 text-xs">
									<span class="truncate font-mono text-signal">${row.name}</span>
									<span class="flex-shrink-0 text-fg-subtle">{row.kind}</span>
								</li>
							{/each}
						</ul>
					{/if}
					<div class="mt-3">
						<Button size="xs" variant="secondary" onclick={onOpenVariableManager}>Manage variables</Button>
					</div>
				</DetailSection>

				{#if mode === 'edit'}
					<DetailSection label="Collections">
						{#if memberships.length === 0}
							<p class="text-xs text-fg-subtle">Not in any collection.</p>
						{:else}
							<ul class="flex flex-wrap gap-1.5" data-testid="prompt-collection-chips">
								{#each memberships as membership (membership.id)}
									<li
										class="inline-flex h-6 items-center gap-1.5 rounded border border-line-strong bg-surface-2 px-2 text-xs"
									>
										<Icon name="folder" className="h-3 w-3 flex-shrink-0 text-fg-muted" />
										<span class="truncate">{membership.name}</span>
										<Tooltip text="Remove from {membership.name}" position="top">
											<button
												type="button"
												class="flex h-4 w-4 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg"
												aria-label="Remove from {membership.name}"
												onclick={() => void onRemoveFromCollection(membership.id)}
											>
												<Icon name="close" className="h-3 w-3" />
											</button>
										</Tooltip>
									</li>
								{/each}
							</ul>
						{/if}
						<div class="mt-3">
							<AddToCollectionMenu
								collections={promptCollections}
								open={addToCollectionOpen}
								placement="down"
								onToggle={() => (addToCollectionOpen = !addToCollectionOpen)}
								onClose={() => (addToCollectionOpen = false)}
								onAdd={onAddToCollection}
								onCreateAndAdd={onCreateAndAddToCollection}
							/>
						</div>
					</DetailSection>

					<DetailSection label="Source">
						<KVGrid>
							<KVItem label="Source" mono>{prompt?.source_provider || 'manual'}</KVItem>
							{#if prompt?.base_model}<KVItem label="Base model" mono>{prompt.base_model}</KVItem>{/if}
						</KVGrid>
						{#if prompt?.source_url}
							<a
								href={prompt.source_url}
								target="_blank"
								rel="noreferrer"
								class="mt-3 inline-flex items-center gap-1.5 text-xs text-signal hover:underline"
							>
								View on {prompt.source_provider || 'source'}
								<Icon name="upload" className="h-3 w-3 rotate-45" />
							</a>
						{/if}
					</DetailSection>
				{/if}
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter {dirtyCount} {mode} {saving} canSave={!(mode === 'create' && !hasMeaningfulSegments(editorSegments))} {onSave} {onDiscard} />
</div>

{#if selectedGeneration}
	<GenerationDetailsModal
		generation={selectedGeneration}
		isOpen={true}
		initialFileIndex={selectedFileIndex}
		on:close={closeGenerationModal}
	/>
{/if}
