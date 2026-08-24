<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { ImageData, VideoData, MeshData } from '$lib/types/tabs';
	import type { AudioData } from '$lib/types/audio';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let batchImages: ImageData[] = [];
	export let batchVideos: VideoData[] = [];
	export let batchAudios: AudioData[] = [];
	export let batchMeshes: MeshData[] = [];
	export let selectedIndex: number = 0;

	const dispatch = createEventDispatcher<{
		select: { item: any; index: number };
	}>();

	function handleSelect(item: any, index: number) {
		dispatch('select', { item, index });
	}
</script>

<div class="flex-shrink-0 border-t border-line-strong bg-canvas">
	<div class="p-3">
		<div class="flex gap-3 overflow-x-auto pb-2">
			<!-- Batch Images -->
			{#each batchImages as imageData, index}
				<div
					class="relative group cursor-pointer flex-shrink-0"
					class:tile-selected={selectedIndex === index}
					on:click={() => handleSelect({ url: imageData.url, originalUrl: imageData.originalUrl || imageData.url, file_type: 'image' }, index)}
					on:keydown={(e) => e.key === 'Enter' && handleSelect({ url: imageData.url, originalUrl: imageData.originalUrl || imageData.url, file_type: 'image' }, index)}
					role="button"
					tabindex="0"
				>
					<div class="w-32 h-32 bg-surface-2 rounded-lg overflow-hidden border {selectedIndex === index ? 'border-line-hover' : 'border-line hover:border-line-hover'} transition-colors">
						<img
							src={imageData.url}
							alt="Generated image {index + 1}"
							class="w-full h-full object-cover hover:scale-105 transition-transform duration-200"
							loading="lazy"
						/>
					</div>

					<!-- Image number badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium">
						#{index + 1}
					</div>
				</div>
			{/each}

			<!-- Batch Videos -->
			{#each batchVideos as videoData, index}
				{@const globalIndex = batchImages.length + index}
				<div
					class="relative group cursor-pointer flex-shrink-0"
					class:tile-selected={selectedIndex === globalIndex}
					on:click={() => handleSelect({ url: videoData.url, originalUrl: videoData.originalUrl || videoData.url, file_type: 'video' }, globalIndex)}
					on:keydown={(e) => e.key === 'Enter' && handleSelect({ url: videoData.url, originalUrl: videoData.originalUrl || videoData.url, file_type: 'video' }, globalIndex)}
					role="button"
					tabindex="0"
				>
					<div class="w-32 h-32 bg-canvas rounded-lg overflow-hidden border {selectedIndex === globalIndex ? 'border-line-hover' : 'border-line hover:border-line-hover'} transition-colors">
						<video
							src={videoData.url}
							class="w-full h-full object-cover"
							muted
						></video>
					</div>

					<!-- Video badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1">
						<svg class="w-2 h-2" fill="currentColor" viewBox="0 0 24 24">
							<path d="M4 4h4v4H4V4zm6 0h4v4h-4V4zm6 0h4v4h-4V4zM4 10h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4zM4 16h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4z" />
						</svg>
						#{globalIndex + 1}
					</div>
				</div>
			{/each}

			<!-- Batch Audio -->
			{#each batchAudios as audioData, index}
				{@const globalIndex = batchImages.length + batchVideos.length + index}
				<div
					class="relative group cursor-pointer flex-shrink-0"
					class:tile-selected={selectedIndex === globalIndex}
					on:click={() => handleSelect({ url: audioData.url, originalUrl: audioData.originalUrl || audioData.url, file_type: 'audio' }, globalIndex)}
					on:keydown={(e) => e.key === 'Enter' && handleSelect({ url: audioData.url, originalUrl: audioData.originalUrl || audioData.url, file_type: 'audio' }, globalIndex)}
					role="button"
					tabindex="0"
				>
					<div
						class="w-32 h-32 rounded-lg overflow-hidden border {selectedIndex === globalIndex ? 'border-line-hover' : 'border-line-strong hover:border-line-hover'} transition-colors flex items-center justify-center"
						style={placeholderTint(audioData.url)}
					>
						<!-- Audio icon -->
						<svg class="w-12 h-12 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
						</svg>
					</div>

					<!-- Audio badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1">
						<svg class="w-2 h-2" fill="currentColor" viewBox="0 0 24 24">
							<path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" />
						</svg>
						#{globalIndex + 1}
					</div>

					<!-- Track type indicator if available -->
					{#if audioData.track_type}
						<div class="absolute bottom-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded-full font-medium">
							{audioData.track_type}
						</div>
					{/if}
				</div>
			{/each}

			<!-- Batch Meshes. Last bucket in the chain (images, videos, audios,
			     meshes) - the same order Workbench's index walk uses. -->
			{#each batchMeshes as meshData, index}
				{@const globalIndex = batchImages.length + batchVideos.length + batchAudios.length + index}
				<div
					class="relative group cursor-pointer flex-shrink-0"
					class:tile-selected={selectedIndex === globalIndex}
					on:click={() => handleSelect({ url: meshData.url, originalUrl: meshData.originalUrl || meshData.url, file_type: 'mesh' }, globalIndex)}
					on:keydown={(e) => e.key === 'Enter' && handleSelect({ url: meshData.url, originalUrl: meshData.originalUrl || meshData.url, file_type: 'mesh' }, globalIndex)}
					role="button"
					tabindex="0"
				>
					<div
						class="w-32 h-32 rounded-lg overflow-hidden border {selectedIndex === globalIndex ? 'border-line-hover' : 'border-line-strong hover:border-line-hover'} transition-colors flex items-center justify-center"
						style={placeholderTint(meshData.url)}
					>
						<!-- Mesh icon: a real thumbnail would need a render pass we do not have here -->
						<svg class="w-12 h-12 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zm0 18V12m0 0l8-4.5M12 12L4 7.5" />
						</svg>
					</div>

					<!-- Mesh badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium">
						#{globalIndex + 1}
					</div>

					{#if meshData.mesh_format}
						<div class="absolute bottom-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded-full font-medium">
							{meshData.mesh_format}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</div>
</div>

<style>
	/* Corner-bracket selection indicator for gallery thumbnails, replacing full ring/border states. */
	.tile-selected {
		position: relative;
	}
	.tile-selected::before,
	.tile-selected::after {
		content: '';
		position: absolute;
		width: 10px;
		height: 10px;
		pointer-events: none;
		z-index: 1;
	}
	.tile-selected::before {
		top: 4px;
		left: 4px;
		border-top: 1.5px solid rgb(var(--accent));
		border-left: 1.5px solid rgb(var(--accent));
	}
	.tile-selected::after {
		bottom: 4px;
		right: 4px;
		border-bottom: 1.5px solid rgb(var(--accent));
		border-right: 1.5px solid rgb(var(--accent));
	}
</style>
