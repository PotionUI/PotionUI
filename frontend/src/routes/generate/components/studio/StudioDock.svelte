<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api';
	import { getCachedSchema } from '$lib/form/schemaCache';
	import type { Tab } from '$lib/types/tabs';
	import {
		downloadExtensionFor,
		entryFileType,
		galleryItemAt,
		galleryItemUrl,
		galleryTotal,
		type GalleryEntry,
		type WorkbenchBatches
	} from '$lib/components/workbench/workbenchGallery';
	import { ringCircumference, ringDashOffset } from './studioProgressRing';
	import { findAttachedMediaThumb } from './studioDockMedia';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import GenerationDetailsModal from '$lib/components/modals/GenerationDetailsModal.svelte';

	export let tab: Tab;
	export let promptPreviewText: string;
	export let promptlessActive: boolean = false;
	export let canGenerate: boolean;
	export let generateDisabledReason: string | undefined;
	export let onGenerate: () => void;
	export let onCancel: () => void;
	export let onOpenPrompt: () => void;
	export let onOpenSettings: () => void;
	export let onMoveToWorkbench: (event: CustomEvent<{ item: any; index: number }>) => void;

	const RING_RADIUS = 32;
	const ringCirc = ringCircumference(RING_RADIUS);

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

	$: progressFraction = generation.currentProgress?.progress ?? null;
	$: ringOffset = ringDashOffset(progressFraction, ringCirc);
	$: ringPercent = progressFraction !== null ? Math.round(progressFraction * 100) : null;

	$: attachedMedia = !isGenerating ? findAttachedMediaThumb(tab.formData) : null;

	// The resolution field's NAME varies per preset (it's whichever field the
	// preset author typed `type: resolution` on - see docs/presets.md), so the
	// aspect pill has to look it up from the schema rather than assume a fixed
	// field name. Shares DynamicForm's own schema cache/request, keyed
	// identically, so opening the settings sheet never double-fetches.
	let resolutionFieldName: string | null = null;
	let loadedSchemaKey = '';
	$: schemaKey = `${tab.selectedPreset ?? ''}:${tab.selectedMode ?? ''}:${tab.selectedVariant ?? ''}`;
	$: if (tab.selectedPreset && tab.selectedMode && schemaKey !== loadedSchemaKey) {
		loadedSchemaKey = schemaKey;
		loadResolutionFieldName(tab.selectedPreset, tab.selectedMode, tab.selectedVariant ?? undefined);
	}

	async function loadResolutionFieldName(presetId: string, mode: string, variant: string | undefined) {
		try {
			const schema = await getCachedSchema(
				presetId,
				mode,
				async () => {
					const response = await api.getPresetFormSchema(presetId, mode, variant);
					if (!response.success || !response.data?.form_schema) {
						throw new Error(response.error || 'no schema');
					}
					return response.data.form_schema;
				},
				false,
				variant
			);
			const entry = Object.entries((schema as any)?.properties || {}).find(
				([, config]: [string, any]) => config?.type === 'resolution'
			);
			if (schemaKey === `${presetId}:${mode}:${variant ?? ''}`) {
				resolutionFieldName = entry ? entry[0] : null;
			}
		} catch (error) {
			logger.error('[StudioDock] Failed to resolve the resolution field:', error);
			resolutionFieldName = null;
		}
	}

	$: aspectLabel = (() => {
		if (!resolutionFieldName) return null;
		const raw = tab.formData?.[resolutionFieldName];
		return typeof raw === 'string' && raw ? raw.replace(/x/i, ' × ') : null;
	})();

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

	// Tags — same TagSelector + generation-tags endpoints Workbench.svelte
	// wires, scoped to this component's own state.
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

	let paramsOpen = false;
</script>

<div
	class="studio-dock pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-3.5 px-4 pb-3.5 pt-6"
	style="background: linear-gradient(rgb(var(--canvas) / 0), rgb(var(--canvas) / 0.92) 55%);"
>
	{#if !promptlessActive}
		<div class="pointer-events-auto flex items-center gap-2">
			<button
				type="button"
				class="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-xl border border-line-strong bg-surface-1/90 px-3.5 text-left backdrop-blur-sm"
				aria-label="Prompt"
				on:click={onOpenPrompt}
			>
				<span class="min-w-0 flex-1 truncate text-sm {promptPreviewText ? 'text-fg' : 'text-fg-subtle'}">
					{promptPreviewText || 'Describe what you want…'}
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
		</div>
	{/if}

	{#if isResult}
		{#if total > 0}
			<div class="pointer-events-auto flex gap-2 overflow-x-auto pb-0.5">
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
							<!-- svelte-ignore a11y-media-has-caption -->
							<video src={thumbUrl} class="h-full w-full object-cover" muted></video>
						{:else if thumbUrl}
							<img src={thumbUrl} alt="" class="h-full w-full object-cover" />
						{/if}
					</button>
				{/each}
			</div>
		{/if}

		<div class="pointer-events-auto flex items-center justify-between">
			<button
				type="button"
				class="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong bg-surface-1/90 text-fg"
				on:click={downloadCurrent}
				aria-label="Download"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
				</svg>
			</button>

			<div class="flex h-11 w-11 items-center justify-center">
				<TagSelector
					{selectedTagIds}
					on:change={handleTagsChange}
					placeholder="Add tags..."
					allowCreate={true}
					compact={true}
					iconOnly={true}
				/>
			</div>

			<button
				type="button"
				class="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full border-4 border-accent/25 bg-accent text-accent-contrast"
				on:click={onGenerate}
				aria-label="Generate again"
			>
				<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
				</svg>
			</button>

			<button
				type="button"
				class="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong bg-surface-1/90 text-fg"
				on:click={() => (paramsOpen = true)}
				aria-label="Generation parameters"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
				</svg>
			</button>

			<button
				type="button"
				class="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong bg-surface-1/90 text-fg"
				on:click={onOpenSettings}
				aria-label="Settings"
			>
				<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
				</svg>
			</button>
		</div>
	{:else if isGenerating}
		<div class="pointer-events-auto flex items-center justify-center">
			<div class="relative h-[72px] w-[72px]">
				<svg class="absolute inset-0 -rotate-90" viewBox="0 0 72 72" aria-hidden="true">
					<circle cx="36" cy="36" r={RING_RADIUS} fill="none" stroke="rgb(var(--fg) / 0.15)" stroke-width="4" />
					<circle
						cx="36"
						cy="36"
						r={RING_RADIUS}
						fill="none"
						stroke="rgb(var(--signal))"
						stroke-width="4"
						stroke-linecap="round"
						stroke-dasharray={ringCirc}
						stroke-dashoffset={ringOffset ?? ringCirc * 0.75}
						class={ringOffset === null ? 'studio-ring-indeterminate' : ''}
					/>
				</svg>
				<button
					type="button"
					class="absolute inset-2 flex flex-col items-center justify-center gap-0.5 rounded-full bg-surface-1/95"
					on:click={onCancel}
					aria-label="Cancel generation"
				>
					<svg class="h-3.5 w-3.5 text-danger" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
						<rect x="7" y="7" width="10" height="10" rx="1.5" />
					</svg>
					<span class="font-mono text-2xs tabular-nums text-fg-muted">{ringPercent !== null ? `${ringPercent}%` : ''}</span>
				</button>
			</div>
		</div>
	{:else}
		<div class="pointer-events-auto flex items-center justify-between">
			<button
				type="button"
				class="flex h-[34px] items-center gap-1.5 rounded-full border border-line-strong bg-surface-1/90 px-3 text-2xs font-medium text-fg-muted"
				on:click={onOpenSettings}
			>
				<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
				</svg>
				Settings
			</button>

			<button
				type="button"
				class="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full border-4 border-accent/25 bg-accent text-accent-contrast disabled:cursor-not-allowed disabled:opacity-40"
				disabled={!canGenerate}
				on:click={onGenerate}
				aria-label="Generate"
				title={!canGenerate && generateDisabledReason ? generateDisabledReason : undefined}
			>
				<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
				</svg>
			</button>

			{#if aspectLabel}
				<button
					type="button"
					class="flex h-[34px] items-center gap-1.5 rounded-full border border-line-strong bg-surface-1/90 px-3 font-mono text-2xs tabular-nums text-fg-muted"
					on:click={onOpenSettings}
				>
					{aspectLabel}
					<svg class="h-2.5 w-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</button>
			{:else}
				<!-- No `type: resolution` field on this preset — nothing to read or open, so no dead pill. -->
				<div class="h-[34px] w-[34px]"></div>
			{/if}
		</div>
	{/if}
</div>

<GenerationDetailsModal
	isOpen={paramsOpen}
	generationId={currentGenerationId}
	initialFileIndex={workbenchIndex}
	on:close={() => (paramsOpen = false)}
/>

<style>
	.studio-ring-indeterminate {
		animation: studio-ring-spin 1.1s linear infinite;
		transform-origin: 36px 36px;
	}

	@keyframes studio-ring-spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}
</style>
