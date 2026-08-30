<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { createEventDispatcher, onDestroy } from 'svelte';
	import type { GenerationHistoryItem, Tag } from '$lib/types/history';
	import { api } from '$lib/services/api/index';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import portal from '$lib/actions/portal';
	import BaseModal from './BaseModal.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MeshPreview from '$lib/components/workbench/renderers/MeshPreview.svelte';
	import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import type { AudioTrack, AudioTrackType } from '$lib/types/audio';
	import StarRating from '$lib/components/StarRating.svelte';
	import FavoriteButton from '$lib/components/FavoriteButton.svelte';
	import { historyStore } from '$lib/stores/history';
	import { libraryStore } from '$lib/stores/library';
	import { summarizeCopyOutcome } from '$lib/library/copyToLibrary';
	import { toasts } from '$lib/stores/toast';
	import { nsfwFilterStore, visibleMediaFiles } from '$lib/stores/nsfwFilter';
	import { nsfwRevealStore, revealKey } from '$lib/stores/nsfwReveal';
	import { pickActiveGeneration, needsDetailFetch } from '$lib/utils/generationDetail';
	import { Badge, Spinner, CopyButton } from '$lib/components/ui';
	import PublishToInspirationsModal from './PublishToInspirationsModal.svelte';
	import { filesWithPreview, mediaFileThumbnailUrl } from '$lib/utils/modelPreview';

	// Support both ways of passing generation data
	export let generation: GenerationHistoryItem | null = null;
	export let generationId: string | undefined = undefined;
	export let isOpen: boolean = false;
	export let initialFileIndex: number = 0;

	const dispatch = createEventDispatcher();

	// Generation data (will be loaded if only ID is provided)
	let loadedGeneration: GenerationHistoryItem | null = null;
	let currentFileIndex = initialFileIndex;
	let loading = true;
	// Id whose detail fetch has been kicked off, so the reactive fetch below
	// doesn't re-fire while the request is in flight.
	let requestedDetailId: string | undefined = undefined;

	// Prefer the loaded detail (it carries `segments`) over the list item.
	$: activeGenerationId = generation?.id || generationId || '';
	$: activeGeneration = pickActiveGeneration(generation, loadedGeneration, activeGenerationId);
	$: shortGenerationId = activeGenerationId ? activeGenerationId.slice(0, 8) : '';
	$: if (!activeGenerationId) {
		logger.error('GenerationDetailsModal: No generation or generationId provided');
	}

	// Track if tags were initialized to prevent overwriting user changes
	let tagsInitialized = false;

	// A generation handed to us by the parent is already renderable, so the spinner ends
	// here. It used to end inside the tags block below, which meant any caller passing a
	// generation without a `tags` array left the modal loading forever.
	$: if (generation) {
		loading = false;
	}

	// Initialize tags from generation if provided (only once per generation)
	$: if (generation?.tags && !tagsInitialized) {
		selectedTags = generation.tags.map((tag: Tag) => ({
			id: tag.id,
			name: tag.name
		}));
		selectedTagIds = selectedTags.map(tag => tag.id);
		tagsInitialized = true;
	}

	// Reset tags initialization flag when generation changes
	$: if (generation?.id) {
		if (previousGenerationId && previousGenerationId !== generation.id) {
			tagsInitialized = false;
		}
		previousGenerationId = generation.id;
	}

	let previousGenerationId: string | undefined = undefined;

	// Parameters and models for current image
	let currentParams: Record<string, any> = {};
	let currentModels: any[] = [];
	let paramsLoading = false;
	let loadedParamsKey = '';

	// Tags
	let selectedTagIds: string[] = [];
	let selectedTags: Array<{ id: string; name: string }> = [];
	let isSavingTags: boolean = false;

	// Structured prompt segments recorded with this generation (may be absent on
	// old rows). Split by channel for display.
	$: segments = activeGeneration?.segments ?? [];
	$: positiveSegments = segments
		.filter((s) => s.channel === 'positive')
		.sort((a, b) => a.prompt_index - b.prompt_index || a.segment_index - b.segment_index);
	$: negativeSegments = segments
		.filter((s) => s.channel === 'negative')
		.sort((a, b) => a.prompt_index - b.prompt_index || a.segment_index - b.segment_index);

	// The Video Director wire document, stored verbatim on form_data.video_director
	// (src/features/video_director/normalize.py's contract) -- a generic prompt
	// `segments` (above) is empty for a Director generation, since its shots never
	// go through the segmented-prompt path.
	$: videoDirectorDoc = (activeGeneration?.form_data?.video_director ?? null) as {
		mode?: string;
		segments?: Array<{ id: string; prompt?: string; negative_prompt?: string; sub_type?: string }>;
		media?: Array<{ role?: string }>;
		audio?: Array<unknown>;
	} | null;
	$: directorSegments = videoDirectorDoc?.segments ?? [];
	$: directorKeyframeCount = (videoDirectorDoc?.media ?? []).filter((m) => m.role === 'keyframe').length;
	$: directorAudioCount = videoDirectorDoc?.audio?.length ?? 0;

	// Collapse state for director shot descriptions, keyed by shot id. A shot
	// only gets a toggle once `overflowCheck` below has measured it as taller
	// than the collapsed max-height.
	let expandedShots: Record<string, boolean> = {};
	let overflowingShots: Record<string, boolean> = {};

	// The Segments card is collapsed by default -- multi-segment generations can
	// run to dozens of chips and dominate the modal. Not persisted; it resets to
	// collapsed on every open.
	let segmentsExpanded = false;

	function toggleShotExpanded(id: string) {
		expandedShots = { ...expandedShots, [id]: !expandedShots[id] };
	}

	/** Marks `id` as overflowing once its collapsed box can't fit its content,
	 * so the "Show full description" toggle only appears when it does something.
	 * jsdom (component tests) has no ResizeObserver, so this degrades to a
	 * one-shot measurement there rather than throwing. */
	function overflowCheck(node: HTMLElement, id: string) {
		const measure = () => {
			const isOverflowing = node.scrollHeight > node.clientHeight + 1;
			if (overflowingShots[id] !== isOverflowing) {
				overflowingShots = { ...overflowingShots, [id]: isOverflowing };
			}
		};
		measure();
		const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
		ro?.observe(node);
		return {
			update: measure,
			destroy: () => ro?.disconnect()
		};
	}

	// A director shot description is free-form text that may embed its own
	// `field_name: value` structure (e.g. from an LLM-authored shot brief).
	// Detected generically off the leading-token shape rather than a fixed
	// field list, since the field set is per-family/per-preset.
	const DESCRIPTION_FIELD_PREFIX = /^([a-z][a-z0-9_]{2,63}):\s*(.*)$/i;
	function describeLines(text: string): Array<{ label: string | null; text: string }> {
		return text.split('\n').map((line) => {
			const match = line.match(DESCRIPTION_FIELD_PREFIX);
			return match ? { label: match[1], text: match[2] } : { label: null, text: line };
		});
	}

	// Reactive computed values; hide mode drops nsfw files from navigation.
	$: mediaFiles = visibleMediaFiles(activeGeneration?.files || [], $nsfwFilterStore.mode);
	$: currentFile = mediaFiles[currentFileIndex];
	$: canGoPrev = currentFileIndex > 0;
	$: canGoNext = currentFileIndex < mediaFiles.length - 1;

	// Auto-tagger output for the file on screen.
	$: systemTags = currentFile?.system_tags ?? [];

	// NSFW blur for the detail pane; reveal state is shared per file via nsfwRevealStore.
	nsfwFilterStore.init();
	$: currentRevealKey = currentFile ? revealKey(activeGenerationId, currentFile) : '';
	$: detailBlur =
		!!currentFile?.nsfw && $nsfwFilterStore.mode === 'blur' && !$nsfwRevealStore.has(currentRevealKey);

	function handleSystemTagClick(tag: string) {
		historyStore.setSystemTagFilter(tag);
		historyStore.loadGenerations();
		handleClose();
	}

	// The history list endpoint never carries `segments`; the detail one does. Fetch
	// whenever what we're holding lacks them, not merely when we were handed a bare id.
	$: if (needsDetailFetch(isOpen, activeGenerationId, activeGeneration, requestedDetailId)) {
		tagsInitialized = false; // Reset flag when loading new generation
		loadGenerationData();
	}

	// Keyed on what the request actually depends on. Svelte marks an object prop
	// dirty on every assignment even when the reference is unchanged, so a parent
	// re-render - history polls every 6s - would otherwise refetch on a loop and
	// blink this pane. Keying also picks up a file change, which reading
	// currentFileIndex inside the function did not.
	$: paramsKey =
		activeGeneration?.files?.length ? `${activeGenerationId}:${currentFileIndex}` : '';
	$: if (paramsKey && paramsKey !== loadedParamsKey) {
		loadedParamsKey = paramsKey;
		loadParametersForCurrentFile();
	}


	async function loadGenerationData() {
		if (!activeGenerationId) return;

		// Claim the id before awaiting, or the reactive statement re-fires.
		requestedDetailId = activeGenerationId;
		// Only block on the spinner when we have nothing to paint yet. When the
		// parent handed us a list item, the Segments card just appears on resolve —
		// the same way the per-file Parameters card already behaves.
		if (!generation) loading = true;
		try {
			const response = await api.getGenerationById(activeGenerationId, true);
			if (response.success && response.data) {
				loadedGeneration = response.data;
				selectedTags = response.data.tags?.map((tag: Tag) => ({
					id: tag.id,
					name: tag.name
				})) || [];
				selectedTagIds = selectedTags.map(tag => tag.id);
				tagsInitialized = true; // Mark as initialized after loading
			}
		} catch (error) {
			logger.error('Failed to load generation:', error);
		} finally {
			loading = false;
		}
	}

	async function loadParametersForCurrentFile() {
		if (!activeGenerationId) return;

		paramsLoading = true;
		try {
			const response = await api.getGenerationParams(activeGenerationId, currentFileIndex);
			if (response.success && response.data) {
				currentParams = response.data.parameters || {};
				currentModels = response.data.models || [];
			}
		} catch (error) {
			logger.error('Failed to load parameters:', error);
			currentParams = {};
			currentModels = [];
		} finally {
			paramsLoading = false;
		}
	}

	async function handleTagsChange(event: CustomEvent<string[]>) {
		await applyTagIds(event.detail);
	}

	async function applyTagIds(newTagIds: string[]) {
		if (!activeGenerationId) return;

		isSavingTags = true;

		try {
			const response = await api.updateGenerationTags(activeGenerationId, newTagIds);
			if (response.success && response.data) {
				selectedTagIds = newTagIds;
				selectedTags = response.data.tags?.map((tag: Tag) => ({
					id: tag.id,
					name: tag.name
				})) || [];
				// Update the loaded generation's tags if needed
				if (loadedGeneration) {
					loadedGeneration.tags = response.data.tags;
				}
			}
		} catch (error) {
			logger.error('Failed to update tags:', error);
		} finally {
			isSavingTags = false;
		}
	}

	function goToPreviousFile() {
		if (canGoPrev) {
			currentFileIndex--;
		}
	}

	function goToNextFile() {
		if (canGoNext) {
			currentFileIndex++;
		}
	}

	function getImageUrl(file: any) {
		const filename = file.file_path.split('/').pop() || file.file_path;
		return `/api/media/generations/${activeGenerationId}/${filename}`;
	}

	function isVideoFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'video';
	}

	function isMeshFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'mesh';
	}

	function isAudioFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'audio';
	}

	// No format column is persisted server-side for history rows at all (see
	// GenerationFile's track_type/sample_rate/channels comments - format never
	// made that list either) - derived from the filename exactly like
	// mapGenerationFiles' mesh_format fallback, so a container format still
	// shows without one.
	function resolveAudioFormat(file: any): string {
		const basename = typeof file?.file_path === 'string' ? file.file_path.split('/').pop() : null;
		const ext = basename && basename.includes('.') ? basename.split('.').pop() : null;
		return (ext || 'wav').toLowerCase();
	}

	function currentAudioTracks(file: any): AudioTrack[] {
		if (!file) return [];
		const url = getImageUrl(file);
		return [
			{
				type: (file.track_type as AudioTrackType) || 'mixed',
				url,
				originalUrl: url,
				duration: file.duration_seconds,
				sample_rate: file.sample_rate,
				channels: file.channels,
				format: resolveAudioFormat(file)
			}
		];
	}

	function formatBytes(bytes?: number) {
		if (!bytes) return 'Unknown';
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}

	function formatDate(dateString?: string) {
		if (!dateString) return 'N/A';
		return new Date(dateString).toLocaleString();
	}

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	function getStatusVariant(status: string): BadgeVariant {
		switch (status) {
			case 'completed':
				return 'success';
			case 'failed':
				return 'danger';
			case 'running':
				return 'signal';
			case 'pending':
				return 'warning';
			default:
				return 'neutral';
		}
	}

	function handleKeyDown(event: KeyboardEvent) {
		if (!isOpen) return;
		if (event.key === 'ArrowLeft') goToPreviousFile();
		else if (event.key === 'ArrowRight') goToNextFile();
		// Escape handled by BaseModal
	}

	function handleClose() {
		dispatch('close');
	}

	function handleRatingChange(rating: number) {
		if (!activeGenerationId) return;
		if (loadedGeneration && loadedGeneration.id === activeGenerationId) {
			loadedGeneration = { ...loadedGeneration, rating };
		}
		historyStore.setRating(activeGenerationId, rating);
	}

	function handleFavoriteToggle() {
		if (!activeGeneration || !activeGenerationId) return;
		if (loadedGeneration && loadedGeneration.id === activeGenerationId) {
			loadedGeneration = { ...loadedGeneration, is_favorite: !loadedGeneration.is_favorite };
		}
		historyStore.toggleFavorite(activeGenerationId);
	}

	// The library holds images, video and audio - a mesh has nowhere to go there.
	let copyingToLibrary = false;
	$: canCopyToLibrary =
		!!currentFile && ['image', 'video', 'audio'].includes(currentFile.file_type?.toLowerCase?.());

	// Copy, not move: this generation and its files stay exactly as they are.
	async function handleCopyToLibrary() {
		if (!currentFile || copyingToLibrary) return;
		copyingToLibrary = true;
		try {
			const { copied, failed } = await libraryStore.copyFromGenerations([{ files: [currentFile] }]);
			const message = summarizeCopyOutcome(copied, failed);
			if (copied > 0) toasts.success(message);
			else toasts.error(message);
		} finally {
			copyingToLibrary = false;
		}
	}

	let showPublishModal = false;

	let exportingBundle = false;

	async function handleExportBundle() {
		if (!activeGenerationId || exportingBundle) return;
		exportingBundle = true;
		try {
			await api.exportGenerationBundle(activeGenerationId);
		} catch (e) {
			logger.error('Export bundle failed:', e);
			toasts.error('Export failed. Please try again.');
		} finally {
			exportingBundle = false;
		}
	}

	function downloadCurrentFile() {
		if (!currentFile) return;
		const link = document.createElement('a');
		link.href = getImageUrl(currentFile);
		const ext = isVideoFile(currentFile)
			? 'mp4'
			: isMeshFile(currentFile)
				? resolveMeshFormat(currentFile)
				: isAudioFile(currentFile)
					? resolveAudioFormat(currentFile)
					: 'png';
		link.download = `generation-${activeGenerationId}-${currentFileIndex + 1}.${ext}`;
		link.click();
	}

	function openCurrentFileInTab() {
		if (!currentFile) return;
		window.open(getImageUrl(currentFile), '_blank');
	}

	function openModelInTab(model: any) {
		if (!model?.id) return;
		window.open(`/models/${model.id}`, '_blank');
	}

	// Same precedence as ModelCard: an admin-set preview_media (folded in by
	// filesWithPreview) wins over the provider-supplied thumbnail file.
	function modelThumbnailUrl(model: any): string | null {
		const file = filesWithPreview(model).find(
			(f: any) => f.file_type === 'image' || f.file_type === 'thumbnail' || f.file_type === 'video'
		);
		return file ? mediaFileThumbnailUrl(file) || null : null;
	}
</script>

<svelte:window on:keydown|capture={handleKeyDown} />

<!-- Callers may mount this from inside a slide-out drawer or another modal;
     an ancestor with a `transform` (the drawer's slide animation) becomes the
     containing block for `position: fixed`, so the dialog must portal to
     <body> to stay viewport-sized regardless of where it's mounted from. -->
<div use:portal>
<BaseModal
	{isOpen}
	title="Generation Details"
	sizeClass="md:w-[85vw] md:h-[85vh]"
	on:close={handleClose}
>
	<svelte:fragment slot="headerIcon">
		<div class="w-[30px] h-[30px] rounded bg-surface-2 border border-line flex items-center justify-center flex-shrink-0">
			<Icon name="image" className="w-4 h-4 text-fg-muted" />
		</div>
	</svelte:fragment>
	<svelte:fragment slot="header">
		{#if activeGeneration}
			<code class="font-mono text-2xs text-fg-disabled flex-shrink-0">{shortGenerationId}</code>
		{/if}
		<div class="flex-1"></div>
		{#if activeGeneration}
			<Badge variant={getStatusVariant(activeGeneration.status)} dot class="flex-shrink-0 uppercase tracking-wide">
				{activeGeneration.status}
			</Badge>
		{/if}
	</svelte:fragment>

	<!-- Body (height must fill the modal) -->
	{#if loading}
		<div class="flex-1 flex items-center justify-center h-full">
			<div class="flex flex-col items-center gap-3">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted">Loading...</p>
			</div>
		</div>
	{:else if activeGeneration}
		<div class="flex flex-col md:flex-row h-full min-h-0">
			<!-- Left Side - Media -->
			<div class="flex-1 bg-black relative group min-h-0 overflow-hidden">
				{#if currentFile}
					<!-- Navigation Arrows -->
					{#if canGoPrev}
						<button
							class="absolute left-4 top-1/2 transform -translate-y-1/2 z-10 p-3 rounded bg-black/70 hover:bg-black/80 text-white transition-colors"
							on:click={goToPreviousFile}
							aria-label="Previous file"
						>
							<Icon name="chevron-left" className="w-6 h-6" />
						</button>
					{/if}

					{#if canGoNext}
						<button
							class="absolute right-4 top-1/2 transform -translate-y-1/2 z-10 p-3 rounded bg-black/70 hover:bg-black/80 text-white transition-colors"
							on:click={goToNextFile}
							aria-label="Next file"
						>
							<Icon name="chevron-right" className="w-6 h-6" />
						</button>
					{/if}

					<!-- Action buttons - top-right corner, shown on hover -->
					<div class="absolute top-4 right-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex gap-2">
						<!-- Favorite toggle -->
						<div
							class="bg-black/50 hover:bg-black/70 p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors flex items-center"
						>
							<FavoriteButton
								active={activeGeneration.is_favorite}
								tone="onMedia"
								size="lg"
								onToggle={handleFavoriteToggle}
							/>
						</div>

						<!-- Tag icon button with dropdown -->
						<TagSelector
							{selectedTagIds}
							on:change={handleTagsChange}
							placeholder="Add tags..."
							allowCreate={true}
							compact={true}
							iconOnly={true}
						/>

						<!-- Reuse button -->
						{#if activeGeneration.preset_id}
							<button
								on:click={() => dispatch('reuse', activeGeneration)}
								class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors"
								title="Reuse this generation's settings"
							>
								<Icon name="refresh" className="h-6 w-6" />
							</button>
						{/if}

						<!-- Export bundle: a portable zip carrying files + reusable settings,
						     distinct from the plain media "Download" button above -->
						<button
							on:click={handleExportBundle}
							disabled={exportingBundle}
							class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors disabled:opacity-50"
							title="Export this generation as a portable bundle"
						>
							{#if exportingBundle}
								<Spinner size="sm" />
							{:else}
								<Icon name="box" className="h-6 w-6" />
							{/if}
						</button>

						<button
							on:click={() => (showPublishModal = true)}
							class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors"
							title="Publish to Inspirations"
						>
							<Icon name="lightbulb" className="h-6 w-6" />
						</button>

						<!-- Download button -->
						<button
							on:click={downloadCurrentFile}
							class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors"
							title="Download"
						>
							<Icon name="download" className="h-6 w-6" />
						</button>

						<!-- Copy this file into the private library (a copy - the generation stays) -->
						{#if canCopyToLibrary}
							<button
								on:click={handleCopyToLibrary}
								disabled={copyingToLibrary}
								class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors disabled:opacity-50"
								title="Copy this file to your library (the generation stays in history)"
							>
								<Icon name="photo" className="h-6 w-6" />
							</button>
						{/if}

						<!-- Open in new tab button -->
						<button
							on:click={openCurrentFileInTab}
							class="bg-black/50 hover:bg-black/70 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors"
							title="Display in new tab"
						>
							<Icon name="external-link" className="h-6 w-6" />
						</button>

						<!-- Plugin actions slot -->
						<PluginSlot
							hookName="image.actions"
							position="top-right"
							context={{
								generationId: activeGenerationId,
								fileIndex: currentFileIndex,
								filename: currentFile.file_path.split('/').pop() || currentFile.file_path,
								fileUrl: getImageUrl(currentFile),
								fileType: currentFile.file_type
							}}
						/>
					</div>

					<!-- Top-left metadata overlay - shown on hover -->
					<div class="absolute top-4 left-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
						<div class="flex flex-col gap-2">
							<div class="flex items-center gap-2">
								<!-- File counter -->
								{#if mediaFiles.length > 1}
									<div class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded text-sm font-medium shadow-lg">
										<span class="font-mono tabular-nums">
											{currentFileIndex + 1} / {mediaFiles.length}
										</span>
									</div>
								{/if}

								<!-- Resolution -->
								{#if currentFile.width && currentFile.height}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
										{currentFile.width} × {currentFile.height}
									</span>
								{/if}

								<!-- File size -->
								{#if currentFile.file_size}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
										{formatBytes(currentFile.file_size)}
									</span>
								{/if}

								<!-- File format -->
								{#if currentFile.file_type}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg uppercase">
										{isVideoFile(currentFile) ? 'MP4' : isMeshFile(currentFile) ? resolveMeshFormat(currentFile).toUpperCase() : isAudioFile(currentFile) ? resolveAudioFormat(currentFile).toUpperCase() : 'PNG'}
									</span>
								{/if}
							</div>

							<!-- Selected tags display - shown on hover -->
							{#if selectedTags.length > 0}
								<div class="flex flex-wrap gap-1">
									{#each selectedTags as tag}
										<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg">
											{tag.name}
										</span>
									{/each}
								</div>
							{/if}
						</div>
					</div>

					<!-- Media Display -->
					<!-- Absolutely filled so the media can never dictate the pane's size -->
					{#if isVideoFile(currentFile)}
						<video
							src={getImageUrl(currentFile)}
							class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {detailBlur ? 'blur-3xl' : ''}"
							controls={!detailBlur}
							playsinline
						>
							<track kind="captions" />
						</video>
					{:else if isMeshFile(currentFile)}
						<div class="absolute inset-0 h-full w-full p-3 md:p-6">
							<MeshPreview file={{ url: getImageUrl(currentFile), originalUrl: getImageUrl(currentFile) }} />
						</div>
					{:else if isAudioFile(currentFile)}
						<div class="absolute inset-0 h-full w-full flex items-center justify-center p-3 md:p-6">
							<div class="w-full max-w-2xl">
								<AudioPlayer tracks={currentAudioTracks(currentFile)} showWaveform={true} showDownload={false} />
							</div>
						</div>
					{:else}
						<img
							src={getImageUrl(currentFile)}
							alt={`Output ${currentFileIndex + 1}`}
							class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {detailBlur ? 'blur-3xl' : ''}"
						/>
					{/if}
					{#if detailBlur}
						<button
							type="button"
							class="absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-canvas/40 text-fg cursor-pointer"
							aria-label="Sensitive content, click to reveal"
							on:click|stopPropagation={() => nsfwRevealStore.reveal(currentRevealKey)}
						>
							<Icon name="eyes" className="w-8 h-8" strokeWidth={1.5} />
							<span class="text-sm font-medium">Sensitive content</span>
							<span class="text-xs text-fg-muted">Click to reveal</span>
						</button>
					{/if}
				{/if}
			</div>

			<!-- Right Side - Information -->
			<div class="w-full md:w-[400px] shrink-0 max-h-[45vh] md:max-h-none border-t md:border-t-0 md:border-l border-line bg-surface-1 overflow-y-auto min-h-0">
				<div class="p-4 space-y-3">
					<!-- Information Section -->
					<div class="bg-surface-2 rounded-lg overflow-hidden">
						<div class="flex items-center justify-between px-3 py-2.5 border-b border-line">
							<div class="flex items-center gap-2">
								<Icon name="information-circle" className="w-4 h-4 text-info" />
								<h3 class="text-sm font-semibold text-fg">Information</h3>
							</div>
							<StarRating value={activeGeneration.rating} onChange={handleRatingChange} />
						</div>
						<div class="divide-y divide-line">
							<div class="flex items-center justify-between px-3 py-2">
								<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">ID</span>
								<div class="flex items-center gap-1">
									<code class="font-mono text-xs text-fg">{shortGenerationId}...</code>
									<CopyButton text={activeGenerationId} ariaLabel="Copy ID" size="xs" />
								</div>
							</div>
							{#if activeGeneration.preset_id}
								<div class="flex items-center justify-between px-3 py-2">
									<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Preset</span>
									<div class="flex items-center gap-1">
										<span class="font-mono text-xs text-fg truncate max-w-[220px]" title={activeGeneration.preset_id}>{activeGeneration.preset_id}</span>
										<CopyButton
											text={activeGeneration.preset_id ?? ''}
											ariaLabel="Copy preset"
											size="xs"
											class="flex-shrink-0"
										/>
									</div>
								</div>
							{/if}
							{#if activeGeneration.preset_version}
								<div class="flex items-center justify-between px-3 py-2">
									<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Version</span>
									<span class="font-mono text-xs text-fg">{activeGeneration.preset_version}</span>
								</div>
							{/if}
							<div class="flex items-center justify-between px-3 py-2">
								<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Created</span>
								<span class="font-mono text-xs tabular-nums text-fg">{formatDate(activeGeneration.created_at)}</span>
							</div>
							{#if activeGeneration.completed_at}
								<div class="flex items-center justify-between px-3 py-2">
									<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">Completed</span>
									<span class="font-mono text-xs tabular-nums text-fg">{formatDate(activeGeneration.completed_at)}</span>
								</div>
							{/if}
						</div>
					</div>

					<!-- Segments Section -->
					{#if segments.length > 0}
						<div class="bg-surface-2 rounded-lg overflow-hidden">
							<button
								type="button"
								class="flex w-full items-center justify-between px-3 py-2.5 {segmentsExpanded ? 'border-b border-line' : ''} transition-colors hover:bg-surface-3"
								on:click={() => (segmentsExpanded = !segmentsExpanded)}
								aria-expanded={segmentsExpanded}
							>
								<div class="flex items-center gap-2">
									<Icon name="document" className="w-4 h-4 text-signal" />
									<h3 class="text-sm font-semibold text-fg">Segments</h3>
								</div>
								<div class="flex items-center gap-2">
									<Badge variant="neutral" size="sm">{segments.length}</Badge>
									<Icon
										name="chevron-down"
										className="w-3.5 h-3.5 text-fg-subtle transition-transform {segmentsExpanded ? 'rotate-180' : ''}"
									/>
								</div>
							</button>

							{#if segmentsExpanded}
							<div class="p-3 space-y-2">
								{#each [...positiveSegments, ...negativeSegments] as seg (seg.prompt_index + '-' + seg.segment_index + '-' + seg.channel)}
									<div class="bg-surface-3 rounded-lg p-2.5 {seg.is_disabled ? 'opacity-50' : ''}">
										<div class="flex items-center gap-1.5 mb-1.5">
											<span
												class="w-1.5 h-1.5 rounded-full flex-shrink-0 {seg.channel === 'negative' ? 'bg-danger' : 'bg-success'}"
											></span>
											<span class="font-mono text-2xs uppercase tracking-wider text-fg-subtle">
												{seg.channel === 'negative' ? 'Negative' : 'Positive'}
											</span>
											<CopyButton
												text={seg.text || ''}
												ariaLabel="Copy segment"
												size="xs"
												class="ml-auto"
											/>
										</div>

										{#if seg.name || seg.color}
											<div class="flex items-center gap-1.5 mb-1">
												{#if seg.color}
													<span
														class="w-2 h-2 rounded-full flex-shrink-0"
														style="background-color: {seg.color}"
													></span>
												{/if}
												{#if seg.name}
													<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
														{seg.name}
													</p>
												{/if}
											</div>
										{/if}

										{#if seg.segment_type === 'break'}
											<span class="inline-flex items-center px-2 py-0.5 rounded bg-surface-1 border border-line font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">
												BREAK
											</span>
										{:else if seg.text}
											<div class="bg-surface-1 border border-line rounded p-2.5 text-xs text-fg leading-relaxed break-words">
												{seg.text}
											</div>
										{:else}
											<div class="bg-surface-1 border border-dashed border-line-strong rounded p-2.5 text-xs italic text-fg-disabled">
												(empty)
											</div>
										{/if}

										{#if seg.description}
											<p class="mt-1.5 text-2xs text-fg-muted leading-relaxed break-words">
												{seg.description}
											</p>
										{/if}

										<!-- `text` above is the resolved prompt; these chips are the
										     phrasebook categories it was resolved from. -->
										{#if seg.phrasebooks && seg.phrasebooks.length > 0}
											<div class="flex flex-wrap gap-1 mt-1.5">
												{#each seg.phrasebooks as ac}
													<Badge variant="info" size="sm">
														<span title={ac.category_path ? `${ac.category_path} → ${ac.value}` : ac.value}>
															{#if ac.category_path}
																<span class="text-fg-subtle">{ac.category_path}</span>
																<span class="text-fg-subtle mx-0.5">·</span>
															{/if}{ac.value}
														</span>
													</Badge>
												{/each}
											</div>
										{/if}
									</div>
								{/each}
							</div>
							{/if}
						</div>
					{/if}

					<!-- Video Director Section -->
					{#if directorSegments.length > 0}
						<div class="bg-surface-2 rounded-lg overflow-hidden">
							<div class="flex items-center justify-between px-3 py-2.5 border-b border-line">
								<div class="flex items-center gap-2">
									<Icon name="document" className="w-4 h-4 text-signal" />
									<h3 class="text-sm font-semibold text-fg">Director shots</h3>
								</div>
								<Badge variant="neutral" size="sm">{directorSegments.length}</Badge>
							</div>

							<div class="p-3 space-y-2">
								{#if directorKeyframeCount > 0 || directorAudioCount > 0}
									<div class="flex flex-wrap gap-1">
										{#if directorKeyframeCount > 0}
											<Badge variant="neutral" size="sm">{directorKeyframeCount} keyframe{directorKeyframeCount === 1 ? '' : 's'}</Badge>
										{/if}
										{#if directorAudioCount > 0}
											<Badge variant="neutral" size="sm">{directorAudioCount} audio track{directorAudioCount === 1 ? '' : 's'}</Badge>
										{/if}
									</div>
								{/if}

								{#each directorSegments as seg, i (seg.id)}
									<div class="bg-surface-3 rounded-lg p-2.5">
										<div class="flex items-center gap-1.5 mb-1.5 flex-wrap">
											<Badge variant="signal" size="sm">SHOT {i + 1}</Badge>
											{#if seg.sub_type}
												<Badge variant="signal" size="sm">{seg.sub_type}</Badge>
											{/if}
											<CopyButton
												text={seg.prompt || ''}
												ariaLabel="Copy shot description"
												size="xs"
												class="ml-auto"
											/>
										</div>

										{#if seg.prompt}
											<div class="relative">
												<div
													class="bg-surface-1 border border-line rounded p-2.5 text-xs text-fg leading-relaxed break-words {expandedShots[seg.id] ? '' : 'max-h-[150px] overflow-hidden'}"
													use:overflowCheck={seg.id}
												>
													{#each describeLines(seg.prompt) as line}
														{#if line.label}<span class="text-fg-subtle">{line.label}:</span>{/if}{line.text}<br />
													{/each}
												</div>
												{#if !expandedShots[seg.id] && overflowingShots[seg.id]}
													<div class="absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-surface-1 to-transparent pointer-events-none rounded-b"></div>
												{/if}
											</div>
											{#if overflowingShots[seg.id]}
												<button
													type="button"
													class="w-full text-center text-2xs text-signal/80 hover:text-signal mt-1 transition-colors"
													on:click={() => toggleShotExpanded(seg.id)}
												>
													{expandedShots[seg.id] ? 'Show less ▴' : 'Show full description ▾'}
												</button>
											{/if}
										{:else}
											<div class="bg-surface-1 border border-dashed border-line-strong rounded p-2.5 text-xs italic text-fg-disabled">
												(empty)
											</div>
										{/if}

										{#if seg.negative_prompt}
											<p class="mt-1.5 text-2xs text-danger leading-relaxed break-words">
												{seg.negative_prompt}
											</p>
										{/if}
									</div>
								{/each}
							</div>
						</div>
					{/if}

					<!-- System tags (auto-tagger output, distinct from user tags) -->
					{#if systemTags.length > 0}
						<div class="bg-surface-2 rounded-lg overflow-hidden">
							<div class="flex items-center justify-between px-3 py-2.5 border-b border-line">
								<div class="flex items-center gap-2">
									<Icon name="sparkles" className="w-4 h-4 text-fg-subtle" />
									<h3 class="text-sm font-semibold text-fg">Auto tags</h3>
								</div>
								<Badge variant="neutral" size="sm">{systemTags.length}</Badge>
							</div>
							<div class="p-3 flex flex-wrap gap-1">
								{#each systemTags as st (st.tag)}
									<button
										type="button"
										class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-dashed border-line hover:border-line-hover bg-surface-3 text-2xs text-fg-muted hover:text-fg transition-colors"
										title="Filter history by “{st.tag}”"
										on:click={() => handleSystemTagClick(st.tag)}
									>
										<span class="truncate max-w-[10rem]">{st.tag.replace(/_/g, ' ')}</span>
										<span class="font-mono tabular-nums text-fg-subtle">{Math.round(st.confidence * 100)}%</span>
									</button>
								{/each}
							</div>
						</div>
					{/if}

					<!-- Parameters Section -->
					<div class="bg-surface-2 rounded-lg overflow-hidden">
						<div class="flex items-center justify-between px-3 py-2.5 border-b border-line">
							<div class="flex items-center gap-2">
								<Icon name="sliders" className="w-4 h-4 text-success" />
								<h3 class="text-sm font-semibold text-fg">Parameters</h3>
							</div>
							{#if currentFile}
								<Badge variant="neutral" size="sm" class="font-mono">#{currentFileIndex + 1}</Badge>
							{/if}
						</div>

						{#if paramsLoading}
							<div class="flex items-center justify-center py-4">
								<Spinner size="sm" />
							</div>
						{:else if Object.keys(currentParams).length > 0}
							<div class="grid grid-cols-2 gap-px bg-line">
								{#each Object.entries(currentParams) as [key, value]}
									<div class="bg-surface-2 hover:bg-surface-3 p-2.5 transition-colors group">
										<div class="flex items-center justify-between mb-1">
											<span class="font-mono text-2xs uppercase tracking-wider text-fg-disabled">
												{key.replace(/_/g, ' ')}
											</span>
											<CopyButton
												text={String(value)}
												ariaLabel="Copy {key}"
												size="xs"
												class="opacity-0 group-hover:opacity-100"
											/>
										</div>
										<div
											class="font-mono tabular-nums text-xs font-semibold text-fg truncate"
											title={typeof value === 'object' ? JSON.stringify(value) : String(value)}
										>
											{typeof value === 'object' ? JSON.stringify(value) : String(value)}
										</div>
									</div>
								{/each}
							</div>
						{:else}
							<p class="text-xs text-fg-subtle p-3">No parameters</p>
						{/if}
					</div>

					<!-- Models Section -->
					<div class="bg-surface-2 rounded-lg overflow-hidden">
						<div class="flex items-center justify-between px-3 py-2.5 border-b border-line">
							<div class="flex items-center gap-2">
								<Icon name="model" className="w-4 h-4 text-warning" />
								<h3 class="text-sm font-semibold text-fg">Models</h3>
							</div>
							{#if currentModels.length > 0}
								<Badge variant="warning" size="sm">{currentModels.length}</Badge>
							{/if}
						</div>

						{#if paramsLoading}
							<div class="flex items-center justify-center py-4">
								<Spinner size="sm" />
							</div>
						{:else if currentModels.length > 0}
							<div class="p-3 space-y-2">
								{#each currentModels as model}
									<button
										type="button"
										class="w-full bg-surface-3 rounded-lg p-2 hover:border-line-hover border border-transparent transition-colors text-left"
										on:click={() => openModelInTab(model)}
										disabled={!model.id}
									>
										<div class="flex gap-2 items-center">
											<!-- Model Thumbnail -->
											{#if modelThumbnailUrl(model)}
												<div class="w-11 h-11 flex-shrink-0 rounded overflow-hidden">
													<img
														src={modelThumbnailUrl(model)}
														alt={modelDisplayName(model)}
														class="w-full h-full object-cover"
													/>
												</div>
											{:else}
												<div class="w-11 h-11 flex-shrink-0 bg-surface-1 rounded flex items-center justify-center">
													<Icon name="model" className="w-6 h-6 text-fg-subtle" />
												</div>
											{/if}

											<!-- Model Info -->
											<div class="flex-1 min-w-0">
												<h4 class="font-semibold text-xs text-fg truncate">
													{modelDisplayName(model)}
												</h4>
												<div class="flex items-center gap-1.5 mt-0.5">
													<Badge variant="signal" size="sm">{model.model_type}</Badge>
												</div>
											</div>

											{#if model.id}
												<Icon name="external-link" className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" />
											{/if}
										</div>
									</button>
								{/each}
							</div>
						{:else}
							<p class="text-xs text-fg-subtle p-3">No models</p>
						{/if}
					</div>
				</div>
			</div>
		</div>
	{/if}
</BaseModal>
</div>

{#if showPublishModal && activeGeneration}
	<PublishToInspirationsModal
		generation={activeGeneration}
		onClose={() => (showPublishModal = false)}
	/>
{/if}
