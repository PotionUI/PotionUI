<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { parseServerDate } from '$lib/utils/relativeTime';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import { Badge } from '$lib/components/ui';
	import { libraryStore, LIBRARY_TAG_TYPE } from '$lib/stores/library';
	import { formatBytes, formatSeconds } from '$lib/utils/format';
	import { libraryItemDisplayName, libraryItemIcon, sliceOrigin, sliceOriginLabel } from '$lib/library/libraryItemMeta';
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

	$: details = [
		item.width && item.height ? { label: 'Dimensions', value: `${item.width}×${item.height}` } : null,
		typeof item.duration_seconds === 'number'
			? { label: 'Duration', value: formatSeconds(item.duration_seconds) }
			: null,
		typeof item.fps === 'number' && item.fps > 0
			? { label: 'FPS', value: String(Math.round(item.fps)) }
			: null,
		typeof item.size === 'number' && item.size > 0
			? { label: 'Size', value: formatBytes(item.size) }
			: null,
		item.created_at
			? { label: 'Added', value: parseServerDate(item.created_at)?.toLocaleString() ?? 'Unknown' }
			: null,
		item.mime_type ? { label: 'Type', value: item.mime_type } : null
	].filter((entry): entry is { label: string; value: string } => entry !== null);

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
		if (!letter || !windowOwnsKeys(event, detailsRoot) || !actionBar?.triggerShortcut(letter)) return;
		event.preventDefault();
		event.stopPropagation();
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
</script>

<svelte:window on:keydown|capture={handleKeyDown} />

<BaseModal isOpen={true} size="xl" title={displayName} on:close={onClose}>
	<div bind:this={detailsRoot} data-details-window>
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
	{#if backLink}
		<div class="flex items-center gap-2 px-4 pt-3 md:px-6" data-back-to-generation>
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
	<div class="p-4 md:p-6 flex flex-col gap-4">
		<div class="relative rounded-lg border border-line-strong bg-black overflow-hidden flex items-center justify-center min-h-[16rem] max-h-[60vh]">
			{#if kind === 'video'}
				<video src={item.url} class="max-h-[60vh] max-w-full" controls playsinline>
					<track kind="captions" />
				</video>
			{:else if kind === 'audio'}
				<div class="w-full p-6 bg-surface-1">
					<AudioPlayer tracks={audioTracks} showWaveform={true} showDownload={false} />
				</div>
			{:else}
				<img src={item.url} alt={displayName} class="max-h-[60vh] max-w-full object-contain" />
			{/if}
		</div>

		<div class="flex flex-wrap items-center gap-2" data-library-item-tags>
			<Badge variant="neutral" class="inline-flex items-center gap-1.5">
				<Icon name={libraryItemIcon(item.media_type)} className="w-3 h-3" />
				{kind || 'media'}
			</Badge>
			<div class="flex-1"></div>
			{#each item.tags as tag (tag.id)}
				<span class="rounded border border-line bg-surface-2 px-2 py-0.5 text-[12px] leading-4 text-fg-muted">{tag.name}</span>
			{/each}
		</div>

		{#if details.length > 0}
			<dl class="grid grid-cols-2 md:grid-cols-3 gap-3">
				{#each details as detail (detail.label)}
					<div class="rounded border border-line bg-surface-2/50 px-3 py-2 min-w-0">
						<dt class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
							{detail.label}
						</dt>
						<dd class="mt-0.5 font-mono tabular-nums text-xs text-fg truncate" use:tooltip={{ text: detail.value, truncateOnly: true }}>
							{detail.value}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}

		{#if slicedFrom}
			<p class="flex items-center gap-1.5 text-sm text-fg-muted" data-slice-origin>
				<Icon name="editor-slice" className="h-3.5 w-3.5 flex-none" />
				<span class="min-w-0 truncate">{sliceOriginLabel(slicedFrom)}</span>
			</p>
		{/if}
				<ProvenanceLine itemType="upload" itemId={item.id} />
	</div>
	</div>
</BaseModal>
