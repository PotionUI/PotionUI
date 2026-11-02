<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api';
	import type { GenerationTab } from '$lib/types/tabs';
	import { NO_PROMPT_COPY, type PromptCopy } from '$lib/utils/promptModeCopy';
	import {
		downloadExtensionFor,
		entryFileType,
		galleryItemAt,
		galleryItemUrl,
		galleryTotal,
		type GalleryEntry,
		type WorkbenchBatches
	} from '$lib/components/workbench/workbenchGallery';
	import { findAttachedMediaThumb } from './studioDockMedia';
	import TagSelector from '$lib/components/TagSelector.svelte';

	export let tab: GenerationTab;
	export let promptPreviewText: string;
	export let promptlessActive: boolean = false;
	export let promptCopy: PromptCopy = NO_PROMPT_COPY;
	export let onOpenPrompt: () => void;
	export let onOpenSettings: () => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;

	$: generation = tab.generation;
	$: isGenerating = generation.isGenerating;
	$: currentGeneration = generation.currentGeneration as any;
	$: workbenchIndex = generation.workbenchIndex ?? 0;
	$: batches = {
		images: generation.batchImages,
		videos: generation.batchVideos,
		audios: generation.batchAudios,
		meshes: generation.batchMeshes
	} as WorkbenchBatches;
	$: total = galleryTotal(batches);
	$: isResult = !isGenerating && total > 0 && currentGeneration?.status === 'completed';

	$: attachedMedia = !isGenerating ? findAttachedMediaThumb(tab.formData) : null;

	function selectThumb(index: number, entry: GalleryEntry | null) {
		if (!entry) return;
		onMoveToWorkbench(new CustomEvent('moveToWorkbench', { detail: { item: entry.item, index } }));
	}

	function downloadCurrent() {
		const entry = galleryItemAt(batches, workbenchIndex);
		const url = galleryItemUrl(entry?.item);
		if (!url) return;
		const kind = entryFileType(entry);
		const link = document.createElement('a');
		link.href = url;
		link.download = `generation-${currentGeneration?.id ?? 'output'}-${workbenchIndex + 1}.${downloadExtensionFor(kind, entry?.item)}`;
		link.click();
	}

	$: currentGenerationId = currentGeneration?.id as string | undefined;
	let selectedTagIds: string[] = [];
	let loadedTagsForGenerationId: string | null = null;
	$: if (isResult && currentGenerationId && currentGenerationId !== loadedTagsForGenerationId) {
		loadedTagsForGenerationId = currentGenerationId;
		loadGenerationTags(currentGenerationId);
	}

	async function loadGenerationTags(generationId: string) {
		try {
			const response = await api.getGenerationById(generationId, true, false);
			if (response.success && response.data?.tags) {
				selectedTagIds = response.data.tags.map((t: any) => t.id);
			}
		} catch (error) {
			logger.error('[StudioDock] Error loading tags:', error);
		}
	}

	async function handleTagsChange(event: CustomEvent<string[]>) {
		if (!currentGenerationId) return;
		const newTagIds = event.detail;
		try {
			const response = await api.updateGenerationTags(currentGenerationId, newTagIds);
			if (response.success) selectedTagIds = newTagIds;
		} catch (error) {
			logger.error('[StudioDock] Error updating tags:', error);
		}
	}
</script>

<div
	class="studio-dock pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-3 px-4 pb-3 pt-6"
	style="background: linear-gradient(rgb(var(--canvas) / 0), rgb(var(--canvas) / 0.92) 55%);"
>
	{#if isResult}
		<div class="pointer-events-auto flex items-center gap-2" data-studio-result-row>
			<div class="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-0.5">
				{#each Array.from({ length: total }) as _, i (i)}
					{@const entry = galleryItemAt(batches, i)}
					{@const thumbUrl = galleryItemUrl(entry?.item)}
					{@const thumbKind = entryFileType(entry)}
					<button
						type="button"
						class="h-[52px] w-[52px] flex-shrink-0 overflow-hidden rounded-lg bg-surface-2 {i === workbenchIndex
							? 'border-2 border-signal'
							: 'border border-line'}"
						on:click={() => selectThumb(i, entry)}
						aria-label="Result {i + 1} of {total}"
						aria-current={i === workbenchIndex}
					>
						{#if thumbKind === 'video' && thumbUrl}
							<video src={thumbUrl} class="h-full w-full object-cover" muted></video>
						{:else if thumbUrl}
							<img src={thumbUrl} alt="" class="h-full w-full object-cover" />
						{/if}
					</button>
				{/each}
			</div>

			<button
				type="button"
				class="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full border border-line-strong bg-surface-1/90 text-fg"
				on:click={downloadCurrent}
				aria-label="Download"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
				</svg>
			</button>

			<div class="flex h-11 w-11 flex-shrink-0 items-center justify-center">
				<TagSelector
					{selectedTagIds}
					on:change={handleTagsChange}
					placeholder="Add tags..."
					allowCreate={true}
					compact={true}
					iconOnly={true}
				/>
			</div>
		</div>
	{/if}

	<div class="pointer-events-auto flex items-center gap-2" data-studio-prompt-row>
		{#if !promptlessActive}
			<button
				type="button"
				class="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-xl border border-line-strong bg-surface-1/90 px-3.5 text-left backdrop-blur-sm"
				aria-label="Prompt"
				on:click={onOpenPrompt}
			>
				<span class="min-w-0 flex-1 truncate text-base {promptPreviewText ? 'text-fg' : 'text-fg-subtle'}">
					{promptPreviewText || promptCopy.placeholder || 'Describe what you want…'}
				</span>
			</button>
			{#if attachedMedia}
				<button
					type="button"
					class="h-8 w-8 flex-shrink-0 overflow-hidden rounded-lg border border-line-strong"
					on:click={onOpenSettings}
					aria-label="Attached: {attachedMedia.name ?? 'source image'} — open settings"
				>
					<img src={attachedMedia.url} alt="" class="h-full w-full object-cover" />
				</button>
			{/if}
		{/if}
		<button
			type="button"
			class="flex h-11 flex-shrink-0 items-center justify-center gap-2 rounded-xl border border-line-strong bg-surface-1/90 text-sm font-medium text-fg-muted backdrop-blur-sm {promptlessActive
				? 'flex-1'
				: 'w-11'}"
			aria-label="Settings"
			on:click={onOpenSettings}
		>
			<svg class="h-4 w-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
			</svg>
			{#if promptlessActive}Settings{/if}
		</button>
	</div>
</div>
