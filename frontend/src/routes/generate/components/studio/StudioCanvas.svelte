<script lang="ts">
	import type { Tab } from '$lib/types/tabs';
	import type { AudioTrack } from '$lib/types/audio';
	import {
		isAudioFileType,
		isVideoFileType,
		normalizeFileType
	} from '$lib/utils/fileType';
	import {
		downloadExtensionFor,
		entryFileType,
		galleryItemAt,
		galleryItemUrl,
		galleryTotal,
		type GalleryEntry,
		type WorkbenchBatches
	} from '$lib/components/workbench/workbenchGallery';
	import { resolveWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import ImagePreview from '$lib/components/workbench/renderers/ImagePreview.svelte';
	import LiveModelFileArtifacts from '$lib/components/generation/LiveModelFileArtifacts.svelte';
	import GridWorkbench from '$lib/components/compare/view/GridWorkbench.svelte';
	import { compareRevision, readWorkbenchGrid } from '$lib/generation/compare/compareStore.svelte';
	import { createImageHandoff } from '$lib/generation/imageHandoff';
	import { createMissingVideoExpiry, createPreviewKeeper, createVideoHandoff, isLiveStatus } from '$lib/generation/videoHandoff';

	export let tab: Tab;

	$: generation = tab.generation;
	$: compareGrid = readWorkbenchGrid($compareRevision, tab.id);
	$: hasModelFiles = (generation.artifacts ?? []).some(
		(artifact) => artifact.artifact_type === 'model_file' && !!artifact.artifact_data
	);
	// Workbench.svelte's own `currentGeneration` prop is typed `any` for the
	// same reason: the WebSocket envelope carries fields (`current_image`,
	// `current_video`, ...) the shared `ActiveGeneration` type doesn't declare.
	$: currentGeneration = generation.currentGeneration as any;
	$: isGenerating = generation.isGenerating;

	$: batches = {
		images: generation.batchImages,
		videos: generation.batchVideos,
		audios: generation.batchAudios,
		meshes: generation.batchMeshes
	} as WorkbenchBatches;
	$: currentGenerationTotal = galleryTotal(batches);
	$: hasGalleryItems = currentGenerationTotal > 0;
	$: isGalleryMode = !isGenerating && hasGalleryItems && currentGeneration?.status === 'completed';

	let currentGalleryEntry: GalleryEntry | null = null;
	$: currentGalleryEntry =
		isGalleryMode && generation.workbenchIndex !== undefined
			? galleryItemAt(batches, generation.workbenchIndex)
			: null;
	$: currentGalleryItem = (currentGalleryEntry?.item as any) ?? null;

	$: displayFileType = isGalleryMode
		? entryFileType(currentGalleryEntry)
		: normalizeFileType(currentGeneration?.file_type);

	let shownImage: string | null | undefined;
	const handoffImage = createImageHandoff((url) => (shownImage = url));
	$: handoffImage(
		isGalleryMode && currentGalleryItem ? galleryItemUrl(currentGalleryItem) : currentGeneration?.current_image
	);
	$: displayImage = shownImage;
	let videoPending = false;
	const keepPreview = createPreviewKeeper();
	$: heldPreview = keepPreview(
		currentGeneration?.generation_id ?? currentGeneration?.id,
		currentGeneration?.current_image
	);
	let holdExpired = false;
	const expireMissingVideo = createMissingVideoExpiry((expired) => (holdExpired = expired));
	$: expireMissingVideo(
		currentGeneration?.status === 'completed' && !isGenerating && !displayVideo,
		currentGeneration?.generation_id ?? currentGeneration?.id
	);
	const videoHandoff = createVideoHandoff((pending) => (videoPending = pending));
	$: videoHandoff.want(
		displayFileType === 'video' ? displayVideo : null,
		!!heldPreview,
		currentGeneration?.generation_id ?? currentGeneration?.id
	);
	$: videoGeneration = isVideoFileType(currentGeneration?.file_type) || displayFileType === 'video';
	$: videoReady = displayFileType === 'video' && !!displayVideo && !videoPending;
	$: previewLive = isLiveStatus(currentGeneration?.status) || isGenerating;
	$: holdingPreview = !!heldPreview && videoGeneration && previewLive && !videoReady && !holdExpired;
	$: displayVideo =
		isGalleryMode && isVideoFileType(displayFileType)
			? galleryItemUrl(currentGalleryItem)
			: currentGeneration?.current_video;
	$: displayAudioItem =
		isGalleryMode && isAudioFileType(displayFileType) ? currentGalleryItem : currentGeneration?.current_audio;

	// Same gate as Workbench.svelte: anything that isn't image/video/audio
	// (mesh, or a plugin-registered `workbench.file` type) renders through the
	// registry instead of a dedicated branch here.
	let resolvedFallbackRenderer: any = null;
	$: isFallbackFileType = !!displayFileType && !['image', 'video', 'audio'].includes(displayFileType);
	$: hasDisplayMedia = !!displayImage || !!displayVideo || !!displayAudioItem || isFallbackFileType;
	$: if (isFallbackFileType) {
		resolveWorkbenchFileRenderer(displayFileType).then((c) => {
			resolvedFallbackRenderer = c ?? ImagePreview;
		});
	}

	$: audioTracks = ((): AudioTrack[] => {
		if (!displayAudioItem) return [];
		if (Array.isArray(displayAudioItem)) return displayAudioItem;
		return [
			{
				type: displayAudioItem.track_type || 'mixed',
				url: displayAudioItem.url || displayAudioItem.originalUrl || '',
				originalUrl: displayAudioItem.originalUrl || displayAudioItem.url,
				duration: displayAudioItem.duration,
				sample_rate: displayAudioItem.sample_rate,
				channels: displayAudioItem.channels,
				file_size: displayAudioItem.file_size,
				format: displayAudioItem.format
			}
		];
	})();

	$: hasError = !isGenerating && !hasDisplayMedia && currentGeneration?.status === 'error';

	$: resultExtension = displayFileType ? downloadExtensionFor(displayFileType, currentGalleryItem).toUpperCase() : '';
	$: resultDimensions = (() => {
		const resolution = currentGalleryItem?.resolution;
		if (!Array.isArray(resolution) || resolution.length !== 2) return '';
		const [w, h] = resolution;
		if (displayFileType === 'video') {
			const parts = [`${w} × ${h}`];
			if (currentGalleryItem?.fps) parts.push(`${currentGalleryItem.fps} FPS`);
			if (currentGalleryItem?.duration) parts.push(`${currentGalleryItem.duration}S`);
			return parts.join(' · ');
		}
		return resultExtension ? `${w} × ${h} · ${resultExtension}` : `${w} × ${h}`;
	})();
</script>

<div class="studio-canvas absolute inset-0 overflow-hidden bg-canvas">
	{#if compareGrid}
		<div
			class="absolute inset-0 min-w-0 overflow-y-auto px-3 pt-16"
			style="padding-bottom: calc(var(--studio-dock-height, 7rem) + 0.75rem)"
			data-testid="studio-compare-stage"
		>
			<GridWorkbench tabId={tab.id} />
		</div>
	{:else if isGenerating}
		<div class="absolute inset-0 flex items-center justify-center bg-canvas">
			{#if currentGeneration?.current_image}
				<img
					src={currentGeneration.current_image}
					alt="Generation preview"
					class="h-full w-full object-contain"
				/>
			{/if}
		</div>
	{:else if hasDisplayMedia}
		<div class="absolute inset-0 flex items-center justify-center bg-canvas">
			{#if displayFileType === 'audio' && audioTracks.length > 0}
				<AudioPlayer tracks={audioTracks} showWaveform={true} showDownload={false} />
			{:else if isFallbackFileType && resolvedFallbackRenderer}
				<svelte:component this={resolvedFallbackRenderer} file={currentGalleryItem ?? currentGeneration} />
			{:else if displayFileType === 'video' && displayVideo}
				<!-- svelte-ignore a11y-media-has-caption -->
				<video
					src={displayVideo}
					controls
					playsinline
					preload="auto"
					class="h-full w-full object-contain"
					on:loadeddata={videoHandoff.ready}
					on:error={videoHandoff.ready}
				></video>
			{:else if displayImage}
				<img src={displayImage} alt="Generation result" class="h-full w-full object-contain" />
			{/if}
		</div>
		{#if currentGenerationTotal > 0}
			<div class="pointer-events-none absolute left-3 top-3 flex items-center gap-1.5">
				<span
					class="rounded-full bg-black/55 px-2 py-0.5 font-mono text-sm tabular-nums text-white"
				>
					{(generation.workbenchIndex ?? 0) + 1} / {currentGenerationTotal}
				</span>
				{#if resultDimensions}
					<span class="rounded-full bg-black/55 px-2 py-0.5 font-mono text-sm tabular-nums text-white">
						{resultDimensions}
					</span>
				{/if}
			</div>
		{/if}
	{:else}
		<div class="studio-canvas-dots absolute inset-0 flex items-center justify-center">
			<div class="-mt-16 px-12 text-center">
				<svg
					class="mx-auto mb-2.5 h-8 w-8 text-line-strong"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
					aria-hidden="true"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="1.5"
						d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"
					/>
				</svg>
				{#if hasError}
					<p class="text-sm font-medium text-danger">Generation failed</p>
					{#if currentGeneration?.message}
						<p class="mt-1 text-xs text-fg-subtle">{currentGeneration.message}</p>
					{/if}
				{:else}
					<p class="text-sm font-medium text-fg-subtle">Describe something below</p>
				{/if}
			</div>
		</div>
	{/if}

	{#if holdingPreview && heldPreview && !compareGrid}
		<img
			src={heldPreview}
			alt="Generation preview"
			class="pointer-events-none absolute inset-0 z-10 h-full w-full object-contain"
			data-preview-hold
		/>
	{/if}

	{#if hasModelFiles}
		<div
			class="absolute inset-0 min-w-0 overflow-y-auto bg-canvas px-3 pt-16"
			style="padding-bottom: calc(var(--studio-dock-height, 14rem) + 0.75rem)"
			data-testid="studio-model-file-stage"
		>
			<LiveModelFileArtifacts {tab} />
		</div>
	{/if}
</div>

<style>
	.studio-canvas-dots {
		background-image: radial-gradient(rgb(var(--fg) / 0.05) 1px, transparent 1px);
		background-size: 16px 16px;
	}
</style>
