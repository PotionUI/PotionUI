<script lang="ts">
	import { onDestroy } from 'svelte';
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
	import { parseTemplateMarkers } from '$lib/utils/templateProcessor';
	import { formatElapsedClock } from './studioProgressRing';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import ImagePreview from '$lib/components/workbench/renderers/ImagePreview.svelte';

	export let tab: Tab;

	$: generation = tab.generation;
	// Workbench.svelte's own `currentGeneration` prop is typed `any` for the
	// same reason: the WebSocket envelope carries fields (`current_image`,
	// `current_video`, ...) the shared `ActiveGeneration` type doesn't declare.
	$: currentGeneration = generation.currentGeneration as any;
	$: isGenerating = generation.isGenerating;
	$: currentProgress = generation.currentProgress;

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

	$: displayImage =
		isGalleryMode && currentGalleryItem ? galleryItemUrl(currentGalleryItem) : currentGeneration?.current_image;
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

	// Generating-state status pill.
	$: stepText = parseTemplateMarkers(currentProgress?.current_step || '').plain;
	$: progressPercent = currentProgress?.progress != null ? Math.round(currentProgress.progress * 100) : null;
	let elapsedNow = Date.now();
	let elapsedTimer: ReturnType<typeof setInterval> | null = null;
	$: if (isGenerating && generation.startedAt) {
		if (!elapsedTimer) {
			elapsedNow = Date.now();
			elapsedTimer = setInterval(() => {
				elapsedNow = Date.now();
			}, 1000);
		}
	} else if (elapsedTimer) {
		clearInterval(elapsedTimer);
		elapsedTimer = null;
	}
	$: elapsedClock = formatElapsedClock(generation.startedAt ? elapsedNow - generation.startedAt : null);

	onDestroy(() => {
		if (elapsedTimer) clearInterval(elapsedTimer);
	});

	// Result-state chips.
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
	{#if isGenerating}
		<div class="absolute inset-0 flex items-center justify-center bg-canvas">
			{#if currentGeneration?.current_image}
				<img
					src={currentGeneration.current_image}
					alt="Generation preview"
					class="h-full w-full object-contain"
				/>
			{/if}
		</div>
		<div class="pointer-events-none absolute inset-x-0 top-0 flex justify-center pt-3">
			<span
				class="inline-flex items-center gap-2 rounded-full border border-line-strong bg-surface-1/90 px-3 py-1.5 font-mono text-2xs uppercase tracking-wide text-fg backdrop-blur-sm"
			>
				<span class="h-1.5 w-1.5 flex-shrink-0 animate-pulse rounded-full bg-fg"></span>
				{#if stepText}{stepText} · {/if}{elapsedClock}
			</span>
		</div>
	{:else if hasDisplayMedia}
		<div class="absolute inset-0 flex items-center justify-center bg-canvas">
			{#if displayFileType === 'audio' && audioTracks.length > 0}
				<AudioPlayer tracks={audioTracks} showWaveform={true} showDownload={false} />
			{:else if isFallbackFileType && resolvedFallbackRenderer}
				<svelte:component this={resolvedFallbackRenderer} file={currentGalleryItem ?? currentGeneration} />
			{:else if displayFileType === 'video' && displayVideo}
				<!-- svelte-ignore a11y-media-has-caption -->
				<video src={displayVideo} controls playsinline class="h-full w-full object-contain"></video>
			{:else if displayImage}
				<img src={displayImage} alt="Generation result" class="h-full w-full object-contain" />
			{/if}
		</div>
		{#if currentGenerationTotal > 0}
			<div class="pointer-events-none absolute left-3 top-3 flex items-center gap-1.5">
				<span
					class="rounded-full bg-black/55 px-2 py-0.5 font-mono text-2xs tabular-nums text-white"
				>
					{(generation.workbenchIndex ?? 0) + 1} / {currentGenerationTotal}
				</span>
				{#if resultDimensions}
					<span class="rounded-full bg-black/55 px-2 py-0.5 font-mono text-2xs tabular-nums text-white">
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
</div>

<style>
	.studio-canvas-dots {
		background-image: radial-gradient(rgb(var(--fg) / 0.05) 1px, transparent 1px);
		background-size: 16px 16px;
	}
</style>
