<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import LoopToggle from '$lib/components/media/LoopToggle.svelte';
	import MeshPreview from '$lib/components/workbench/renderers/MeshPreview.svelte';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import { loopVideos } from '$lib/stores/loopVideos';
	import { formatBytes } from '$lib/utils/format';
	import type { AudioTrack } from '$lib/types/audio';
	import { isAudioFile, isMeshFile, isVideoFile, stageFormatLabel, type StageFile } from './mediaFile';

	let {
		file,
		src,
		alt,
		fileCount = 1,
		fileIndex = 0,
		canPrev = false,
		canNext = false,
		onPrev,
		onNext,
		blur = false,
		onReveal,
		alphaCheckerboard = false,
		imageLoading = false,
		imageEl = $bindable(),
		onImageSettled,
		audioTracks = [],
		tagNames = [],
		formatLabel
	}: {
		file: StageFile;
		src: string;
		alt: string;
		fileCount?: number;
		fileIndex?: number;
		canPrev?: boolean;
		canNext?: boolean;
		onPrev?: () => void;
		onNext?: () => void;
		blur?: boolean;
		onReveal?: () => void;
		alphaCheckerboard?: boolean;
		imageLoading?: boolean;
		imageEl?: HTMLImageElement | undefined;
		onImageSettled?: () => void;
		audioTracks?: AudioTrack[];
		tagNames?: string[];
		formatLabel?: string;
	} = $props();
</script>

{#if canPrev}
	<IconButton icon="chevron-left" label="Previous file" variant="overlay" onclick={onPrev} class="absolute left-4 top-1/2 -translate-y-1/2 z-10" />
{/if}

{#if canNext}
	<IconButton icon="chevron-right" label="Next file" variant="overlay" onclick={onNext} class="absolute right-4 top-1/2 -translate-y-1/2 z-10" />
{/if}

<div class="absolute top-4 left-4 z-20 max-w-[calc(100%-2rem)] opacity-0 group-hover:opacity-100 transition-opacity duration-200">
	<div class="flex flex-col gap-2">
		<div data-stage-meta class="flex flex-wrap items-center gap-2">
			{#if fileCount > 1}
				<div data-file-counter class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded text-sm font-medium shadow-lg">
					<span class="font-mono tabular-nums">
						{fileIndex + 1} / {fileCount}
					</span>
				</div>
			{/if}

			{#if file.width && file.height}
				<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
					{file.width} × {file.height}
				</span>
			{/if}

			{#if file.file_size}
				<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
					{formatBytes(file.file_size)}
				</span>
			{/if}

			{#if file.file_type}
				<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg uppercase">
					{formatLabel ?? stageFormatLabel(file)}
				</span>
			{/if}
		</div>

		{#if tagNames.length > 0}
			<div class="flex flex-wrap gap-1">
				{#each tagNames as name}
					<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg">
						{name}
					</span>
				{/each}
			</div>
		{/if}
	</div>
</div>

{#if isVideoFile(file)}
	<video
		{src}
		class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {blur ? 'blur-3xl' : ''}"
		controls={!blur}
		loop={$loopVideos}
		playsinline
	>
		<track kind="captions" />
	</video>
	{#if !blur}
		<LoopToggle class="absolute top-4 right-4 z-20" />
	{/if}
{:else if isMeshFile(file)}
	<div class="absolute inset-0 h-full w-full p-3 md:p-6">
		<MeshPreview file={{ url: src, originalUrl: src }} />
	</div>
{:else if isAudioFile(file)}
	<div class="absolute inset-0 h-full w-full flex items-center justify-center p-3 md:p-6">
		<div class="w-full max-w-2xl">
			<AudioPlayer tracks={audioTracks} showWaveform={true} showDownload={false} />
		</div>
	</div>
{:else}
	{#if imageLoading}
		<div class="absolute inset-0 z-[1] bg-surface-2 animate-pulse" data-stage-skeleton></div>
	{/if}
	<img
		bind:this={imageEl}
		onload={onImageSettled}
		onerror={onImageSettled}
		{src}
		{alt}
		class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {blur ? 'blur-3xl' : ''} {alphaCheckerboard ? 'alpha-checkerboard' : ''}"
	/>
{/if}
{#if blur}
	<button
		type="button"
		class="absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-canvas/40 text-fg cursor-pointer"
		aria-label="Sensitive content, click to reveal"
		onclick={(event) => {
			event.stopPropagation();
			onReveal?.();
		}}
	>
		<Icon name="eyes" className="w-8 h-8" strokeWidth={1.5} />
		<span class="text-sm font-medium">Sensitive content</span>
		<span class="text-xs text-fg-muted">Click to reveal</span>
	</button>
{/if}

<style>
	.alpha-checkerboard {
		background-image:
			linear-gradient(45deg, rgb(var(--surface-3)) 25%, transparent 25%),
			linear-gradient(-45deg, rgb(var(--surface-3)) 25%, transparent 25%),
			linear-gradient(45deg, transparent 75%, rgb(var(--surface-3)) 75%),
			linear-gradient(-45deg, transparent 75%, rgb(var(--surface-3)) 75%);
		background-size: 16px 16px;
		background-position: 0 0, 0 8px, 8px -8px, -8px 0px;
		background-color: rgb(var(--surface-2));
	}
</style>
