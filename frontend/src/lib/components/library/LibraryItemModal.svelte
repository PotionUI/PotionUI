<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';
	import { CopyButton, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import MediaDetailsShell from '$lib/components/media-details/MediaDetailsShell.svelte';
	import MediaDetailsHeader from '$lib/components/media-details/MediaDetailsHeader.svelte';
	import MediaStage from '$lib/components/media-details/MediaStage.svelte';
	import FactGrid, { type Fact } from '$lib/components/media-details/FactGrid.svelte';
	import DetailsSection from '$lib/components/generation/DetailsSection.svelte';
	import ParametersView from '$lib/components/generation/ParametersView.svelte';
	import { detailsPrefs } from '$lib/components/generation/detailsPrefs';
	import type { GenerationParameterMeta } from '$lib/types/generation';
	import { parseServerDate } from '$lib/utils/relativeTime';
	import { formatShortDay, formatShortWhen } from '$lib/utils/generationTime';
	import { libraryStore, LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import { formatBytes, formatSeconds } from '$lib/utils/format';
	import { libraryItemDisplayName, libraryItemIcon, sliceOrigin, sliceOriginLabel } from '$lib/library/libraryItemMeta';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import { plainLetter, windowOwnsKeys } from '$lib/utils/shortcutGuard';
	import DetailsActionBar, { type BarAction } from '$lib/media-tools/DetailsActionBar.svelte';
	import { buildLibraryToolContext } from '$lib/tools/tools';
	import { toasts } from '$lib/stores/toast';
	import { api } from '$lib/services/api/index';
	import type { EditedMediaItem } from '$lib/services/api/media';
	import type { LibraryItem } from '$lib/services/api/library';
	import ProvenanceLine from '$lib/components/organize/ProvenanceLine.svelte';
	import { startRuleFrom } from '$lib/organize/handoff';
	import { uploadItemRule } from '$lib/organize/itemPrefill';

	export let item: LibraryItem;
	export let onClose: () => void;
	export let onDeleteRequest: (item: LibraryItem) => void;
	export let generationLink: { generationId: string; fileIndex: number } | null = null;
	export let onBackToGeneration: ((link: { generationId: string; fileIndex: number }) => void) | undefined = undefined;
	export let navigation: { items: LibraryItem[]; onSelect: (item: LibraryItem) => void } | undefined = undefined;

	$: backLink =
		generationLink ?? (item.source_generation_id ? { generationId: item.source_generation_id, fileIndex: 0 } : null);
	let generationState: 'unknown' | 'present' | 'missing' = 'unknown';
	let generationPrompt = '';
	let checkedGeneration: string | null = null;

	$: if (backLink && backLink.generationId !== checkedGeneration) void checkGeneration(backLink.generationId);

	async function checkGeneration(id: string) {
		checkedGeneration = id;
		generationState = 'unknown';
		generationPrompt = '';
		try {
			const response = await api.getGenerationById(id);
			if (checkedGeneration !== id) return;
			if (response?.success && response.data) {
				generationState = 'present';
				const prompt = response.data.form_data?.prompt;
				generationPrompt = typeof prompt === 'string' ? prompt.split('\n')[0].slice(0, 160) : '';
			} else generationState = 'missing';
		} catch {
			if (checkedGeneration === id) generationState = 'missing';
		}
	}

	$: displayName = libraryItemDisplayName(item);
	$: slicedFrom = sliceOrigin(item.provenance);
	$: kind = (item.media_type ?? '').toLowerCase();
	$: selectedTagIds = item.tags.map((tag) => tag.id);
	$: audioTracks = [{ type: 'mixed' as const, url: item.url, duration: item.duration_seconds }];
	$: stageFile = {
		file_path: item.original_filename || item.url,
		file_type: kind,
		width: item.width,
		height: item.height,
		file_size: item.size
	};
	$: formatLabel = (item.mime_type?.split('/')[1] ?? '').toUpperCase() || undefined;
	$: addedShort = formatShortWhen(item.created_at);
	$: addedPhone = formatShortDay(item.created_at);
	$: addedExact = item.created_at ? (parseServerDate(item.created_at)?.toLocaleString() ?? '') : '';

	$: facts = [
		item.mime_type ? { label: 'Type', value: item.mime_type } : null,
		item.width && item.height ? { label: 'Dimensions', value: `${item.width} × ${item.height}`, mono: true } : null,
		typeof item.duration_seconds === 'number'
			? { label: 'Duration', value: formatSeconds(item.duration_seconds), mono: true }
			: null,
		typeof item.fps === 'number' && item.fps > 0
			? { label: 'Frame rate', value: `${Math.round(item.fps)} fps`, mono: true }
			: null,
		typeof item.size === 'number' && item.size > 0 ? { label: 'Size', value: formatBytes(item.size), mono: true } : null
	].filter((entry): entry is Fact => entry !== null);

	$: items = navigation?.items ?? [];
	$: currentIndex = items.findIndex((entry) => entry.id === item.id);
	$: canNavigate = !!navigation && currentIndex >= 0 && items.length > 1;
	$: hasPrevious = canNavigate && currentIndex > 0;
	$: hasNext = canNavigate && currentIndex < items.length - 1;
	$: position = canNavigate ? { index: currentIndex + 1, total: items.length } : null;

	function navigate(direction: -1 | 1) {
		if (!navigation || !canNavigate) return;
		if (direction === -1 ? !hasPrevious : !hasNext) return;
		navigation.onSelect(items[currentIndex + direction]);
	}

	function handleDownload() {
		const link = document.createElement('a');
		link.href = item.url;
		link.download = displayName;
		link.click();
	}

	async function handleTagsChange(ids: string[]) {
		await libraryStore.setItemTags(item.id, ids);
	}

	async function handleFavoriteToggle() {
		const ok = await libraryStore.setItemFavorite(item.id, !item.is_favorite);
		if (!ok) toasts.error('Could not update the favorite');
	}

	let detailsRoot: HTMLElement | undefined;
	let actionBar: DetailsActionBar | undefined;

	function handleKeyDown(event: KeyboardEvent) {
		const letter = plainLetter(event);
		if (letter && windowOwnsKeys(event, detailsRoot) && actionBar?.triggerShortcut(letter)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if ((event.target as HTMLElement | null)?.closest?.('[data-details-action-bar], [data-bar-menu]')) return;
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		if (isTypingInField(event) || !canNavigate) return;
		event.preventDefault();
		navigate(event.key === 'ArrowRight' ? 1 : -1);
	}

	$: rowItems = buildLibraryToolContext([item], null).items;

	function openInNewTab() {
		window.open(item.url, '_blank');
	}

	$: libraryActions = [
		{
			id: 'auto-file',
			label: 'Auto-file…',
			icon: 'sparkles',
			tooltip: 'Create a rule that files items like this one',
			onclick: () => startRuleFrom(uploadItemRule(item))
		}
	] satisfies BarAction[];

	$: downloadActions = [
		{
			id: 'download-file',
			label: 'Download file',
			icon: 'download',
			shortcut: 'D',
			tooltip: 'Download this file',
			description: [displayName, typeof item.size === 'number' && item.size > 0 ? formatBytes(item.size) : '']
				.filter(Boolean)
				.join(' · '),
			onclick: handleDownload
		}
	] satisfies BarAction[];

	$: moreActions = [
		{ id: 'open-new-tab', label: 'Open in new tab', icon: 'external-link', onclick: openInNewTab }
	] satisfies BarAction[];

	$: dangerActions = [
		{
			id: 'delete',
			label: 'Delete',
			icon: 'trash',
			tone: 'danger',
			onclick: () => onDeleteRequest(item)
		}
	] satisfies BarAction[];

	async function handleToolResult(results: EditedMediaItem[], info: { replaced: boolean }) {
		await libraryStore.applyToolResult(results, [item.id], { replaced: info.replaced });
	}

	const PARAMETERS_SECTION = 'section:library-parameters';
	$: parametersOpen = $detailsPrefs[PARAMETERS_SECTION] ?? false;
	$: canShowParameters = !!backLink && generationState !== 'missing';
	let sourceParams: Record<string, unknown> = {};
	let sourceMeta: Record<string, GenerationParameterMeta> | null = null;
	let sourceOrder: string[] | null = null;
	let sourceModels: Array<{ name?: string; filename?: string }> = [];
	let sourceParamsLoading = false;
	let loadedParamsFor: string | null = null;

	$: if (parametersOpen && canShowParameters && backLink) {
		const key = `${backLink.generationId}:${backLink.fileIndex}`;
		if (key !== loadedParamsFor) void loadSourceParams(key, backLink);
	}

	async function loadSourceParams(key: string, link: { generationId: string; fileIndex: number }) {
		loadedParamsFor = key;
		sourceParamsLoading = true;
		try {
			const response = await api.getGenerationParams(link.generationId, link.fileIndex);
			if (loadedParamsFor !== key) return;
			if (response.success && response.data) {
				sourceParams = response.data.parameters || {};
				sourceMeta = response.data.parameter_meta ?? null;
				sourceOrder = response.data.parameter_order ?? null;
				sourceModels = response.data.models ?? [];
			} else {
				sourceParams = {};
			}
		} catch {
			if (loadedParamsFor === key) sourceParams = {};
		} finally {
			if (loadedParamsFor === key) sourceParamsLoading = false;
		}
	}
</script>

<MediaDetailsShell
	isOpen={true}
	title={displayName}
	headerIconName={libraryItemIcon(item.media_type)}
	{onClose}
	onKeyDown={handleKeyDown}
	bind:root={detailsRoot}
	customHeader
>
	{#snippet headerRight()}
		<MediaDetailsHeader title={displayName}>
			{#snippet rating()}
				{#if item.is_favorite}
					<span class="inline-flex text-signal" role="img" aria-label="Favorite" data-library-favorite-mark use:tooltip={"Favorite"}>
						<svg viewBox="0 0 24 24" class="h-4 w-4" fill="currentColor" aria-hidden="true"><path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" /></svg>
					</span>
				{/if}
			{/snippet}
			{#snippet tools()}
				{#if canNavigate}
					<div class="flex flex-shrink-0 items-center gap-1">
						<Tooltip text="Previous item" kbd="←" position="bottom">
							<IconButton icon="chevron-left" label="Previous item" disabled={!hasPrevious} onclick={() => navigate(-1)} />
						</Tooltip>
						{#if position}
							<span class="font-mono tabular-nums text-xs text-fg-subtle max-sm:hidden" data-library-position>
								{position.index} / {position.total}
							</span>
						{/if}
						<Tooltip text="Next item" kbd="→" position="bottom">
							<IconButton icon="chevron-right" label="Next item" disabled={!hasNext} onclick={() => navigate(1)} />
						</Tooltip>
					</div>
				{/if}
			{/snippet}
			{#snippet meta()}
				{#if addedShort}
					<button
						type="button"
						class="min-w-0 cursor-default truncate rounded text-left"
						data-testid="library-added"
						use:tooltip={{ text: addedExact, focusSelf: true, wrap: true }}
					>
						<span class="max-sm:hidden">Added {addedShort}</span><span class="sm:hidden">{addedPhone}</span>
					</button>
					<span aria-hidden="true">·</span>
				{/if}
				<span class="shrink-0 uppercase tracking-wide" data-library-kind>{kind || 'media'}</span>
				<CopyButton text={displayName} ariaLabel="Copy file name" size="xs" class="flex-shrink-0 max-sm:hidden" />
			{/snippet}
		</MediaDetailsHeader>
	{/snippet}

	{#snippet toolbar()}
		<DetailsActionBar
			bind:this={actionBar}
			items={rowItems}
			scope="library"
			favorite={{ active: !!item.is_favorite, onToggle: handleFavoriteToggle }}
			tags={{ selectedIds: selectedTagIds, tagType: LIBRARY_TAG_TYPE, onChange: handleTagsChange }}
			pageActions={libraryActions}
			{downloadActions}
			{moreActions}
			{dangerActions}
			onResult={handleToolResult}
			onOpenedInTab={onClose}
		/>
	{/snippet}

	{#snippet stage()}
		<MediaStage
			file={stageFile}
			src={item.url}
			alt={displayName}
			{formatLabel}
			alphaCheckerboard={item.has_alpha === true}
			{audioTracks}
			tagNames={item.tags.map((tag) => tag.name)}
		/>
	{/snippet}

	{#snippet columnContent()}
		<div class="py-4">
			{#if facts.length > 0}
				<DetailsSection id="library-file" title="File" collapsible={false}>
					<FactGrid {facts} />
				</DetailsSection>
			{/if}

			<DetailsSection id="library-tags" title="Tags" count={item.tags.length} collapsible={false}>
				<div class="flex flex-wrap gap-1 px-3" data-library-item-tags>
					{#each item.tags as tag (tag.id)}
						<span class="rounded border border-line bg-surface-2 px-2 py-0.5 text-sm text-fg-muted">{tag.name}</span>
					{:else}
						<span class="text-sm text-fg-subtle">No tags yet</span>
					{/each}
				</div>
			</DetailsSection>

			{#if backLink || slicedFrom}
				<DetailsSection id="library-source" title="Source" collapsible={false}>
					<div class="space-y-2 px-3">
						{#if backLink}
							<div class="flex items-center gap-2" data-back-to-generation>
								{#if generationState === 'missing'}
									<span class="text-sm text-fg-subtle">Generation deleted</span>
								{:else if onBackToGeneration}
									<button
										type="button"
										class="inline-flex items-center gap-1.5 rounded text-sm text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
										use:tooltip={{ text: generationPrompt || 'Back to the generation', truncateOnly: false }}
										on:click={() => backLink && onBackToGeneration?.(backLink)}
									>
										<Icon name="chevron-left" className="h-4 w-4" />
										Back to the generation
									</button>
								{:else}
									<a
										class="inline-flex items-center gap-1.5 rounded text-sm text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
										href={`/history?generation=${encodeURIComponent(backLink.generationId)}`}
									>
										<Icon name="chevron-left" className="h-4 w-4" />
										Back to the generation
									</a>
								{/if}
							</div>
						{/if}
						{#if slicedFrom}
							<p class="flex items-center gap-1.5 text-sm text-fg-muted" data-slice-origin>
								<Icon name="editor-slice" className="h-3.5 w-3.5 flex-none" />
								<span class="min-w-0 truncate">{sliceOriginLabel(slicedFrom)}</span>
							</p>
						{/if}
					</div>
				</DetailsSection>
			{/if}

			{#if canShowParameters}
				<DetailsSection id="library-parameters" title="Parameters" defaultOpen={false}>
					<p class="px-3 pb-2 text-sm text-fg-subtle">From the source generation</p>
					<ParametersView
						params={sourceParams}
						meta={sourceMeta}
						order={sourceOrder}
						models={sourceModels}
						loading={sourceParamsLoading}
					/>
				</DetailsSection>
			{/if}
			<div class="mx-4 mt-4 empty:hidden"><ProvenanceLine itemType="upload" itemId={item.id} /></div>
		</div>
	{/snippet}
</MediaDetailsShell>
