<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { ImageData, VideoData, MeshData } from '$lib/types/tabs';
	import type { AudioData } from '$lib/types/audio';
	import { Badge, SegmentedControl } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import { occurrenceKeys, tileThumbUrl } from './tileThumb';
	import { groupByPrompt, isPromptBatch, type PromptEntry } from './promptGroups';

	export let batchImages: ImageData[] = [];
	export let batchVideos: VideoData[] = [];
	export let batchAudios: AudioData[] = [];
	export let batchMeshes: MeshData[] = [];
	export let selectedIndex: number = 0;
	$: imageKeys = occurrenceKeys(batchImages.map((image) => image.originalUrl || image.url));

	const dispatch = createEventDispatcher<{
		select: { item: any; index: number };
	}>();

	function handleSelect(item: any, index: number) {
		dispatch('select', { item, index });
	}

	let view: 'prompt' | 'all' = 'prompt';

	$: promptEntries = [
		...batchImages.map((item, index): PromptEntry<ImageData | VideoData> => ({ item, index, kind: 'image' })),
		...batchVideos.map((item, index): PromptEntry<ImageData | VideoData> => ({
			item,
			index: batchImages.length + index,
			kind: 'video'
		}))
	];
	$: batched = isPromptBatch(promptEntries.map((entry) => entry.item));
	$: grouped = batched && view === 'prompt';
	$: groups = grouped ? groupByPrompt(promptEntries) : [];
	$: hasOtherMedia = batchAudios.length > 0 || batchMeshes.length > 0;
</script>

{#snippet groupTile(tile: (typeof groups)[number]['tiles'][number], group: (typeof groups)[number])}
	<div
		class="relative cursor-pointer"
		class:tile-selected={selectedIndex === tile.index}
		on:click={() => handleSelect({ url: tile.item.url, originalUrl: tile.item.originalUrl || tile.item.url, file_type: tile.kind }, tile.index)}
		on:keydown={(e) => e.key === 'Enter' && handleSelect({ url: tile.item.url, originalUrl: tile.item.originalUrl || tile.item.url, file_type: tile.kind }, tile.index)}
		role="button"
		tabindex="0"
		aria-label="Prompt {group.promptIndex + 1}, image {tile.imageNumber}"
	>
		<div class="h-24 w-24 overflow-hidden rounded-lg border bg-surface-2 transition-colors {selectedIndex === tile.index ? 'border-line-hover' : 'border-line hover:border-line-hover'}">
			{#if tile.kind === 'video'}
				<video src={tile.item.url} class="h-full w-full object-cover" muted preload="metadata"></video>
			{:else}
				<img src={tileThumbUrl(tile.item.url)} alt="Prompt {group.promptIndex + 1}, image {tile.imageNumber}" class="h-full w-full object-cover" loading="lazy" decoding="async" />
			{/if}
		</div>
		<div class="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-0.5 font-mono text-sm font-medium tabular-nums text-white" data-output-label>
			{group.promptIndex + 1}.{tile.imageNumber}
		</div>
	</div>
{/snippet}

<div class="flex-shrink-0 border-t border-line-strong bg-canvas">
	<div class="p-3">
		{#if batched}
			<div class="mb-2 flex items-center justify-between gap-3">
				<span class="font-mono text-sm tabular-nums text-fg-subtle">
					{promptEntries.length} {promptEntries.length === 1 ? 'image' : 'images'} · {new Set(promptEntries.map((entry) => entry.item.promptIndex)).size} prompts
				</span>
				<SegmentedControl
					variant="toggle"
					ariaLabel="Group images"
					selected={view}
					items={[
						{ id: 'prompt', label: 'By prompt' },
						{ id: 'all', label: 'All' }
					]}
					onSelect={(id) => (view = id === 'all' ? 'all' : 'prompt')}
				/>
			</div>
		{/if}
		{#if grouped}
			<div class="mb-2 max-h-72 overflow-y-auto rounded-lg border border-line">
				{#each groups as group (group.promptIndex)}
					{@const current = group.tiles.some((tile) => tile.index === selectedIndex)}
					<div class="grid grid-cols-[10rem_minmax(0,1fr)] items-start gap-3 border-t border-line px-3 py-2.5 first:border-t-0 {current ? 'border-l-2 border-l-signal bg-signal/5' : ''}">
						<button
							type="button"
							class="min-w-0 rounded text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
							on:click={() => handleSelect({ url: group.tiles[0].item.url, originalUrl: group.tiles[0].item.originalUrl || group.tiles[0].item.url, file_type: group.tiles[0].kind }, group.tiles[0].index)}
						>
							<span class="mb-1 block font-mono text-sm font-medium tabular-nums text-fg-subtle">{group.promptIndex + 1}</span>
							<span class="line-clamp-3 text-sm {current ? 'text-fg' : 'text-fg-muted'}">{group.promptText}</span>
						</button>
						<div class="flex flex-wrap gap-2">
							{#each group.tiles as tile (tile.index)}
								{@render groupTile(tile, group)}
							{/each}
						</div>
					</div>
				{/each}
			</div>
		{/if}
		<div class="flex gap-3 overflow-x-auto pb-2" class:hidden={grouped && !hasOtherMedia}>
			{#if !grouped}
			<!-- Batch Images -->
			{#each batchImages as imageData, index (imageKeys[index])}
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
							src={tileThumbUrl(imageData.url)}
							alt="Generated image {index + 1}"
							class="w-full h-full object-cover hover:scale-105 transition-transform duration-200"
							loading="lazy"
							decoding="async"
						/>
					</div>

					<!-- Image number badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium">
						#{index + 1}
					</div>

					{#if imageData.label}
						<div class="absolute bottom-1 left-1 right-1 flex" data-output-label>
							<Tooltip text={imageData.label} position="top" wrapperClass="inline-flex items-center max-w-full min-w-0">
								<Badge size="lg" class="max-w-full font-mono uppercase tracking-[0.07em] bg-black/70 text-white border-white/20">
									<span class="truncate">{imageData.label}</span>
								</Badge>
							</Tooltip>
						</div>
					{/if}
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
							preload="metadata"
						></video>
					</div>

					<!-- Video badge -->
					<div class="absolute top-1 left-1 bg-black/70 text-white text-xs font-mono tabular-nums px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1">
						<svg class="w-2 h-2" fill="currentColor" viewBox="0 0 24 24">
							<path d="M4 4h4v4H4V4zm6 0h4v4h-4V4zm6 0h4v4h-4V4zM4 10h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4zM4 16h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4z" />
						</svg>
						#{globalIndex + 1}
					</div>

					{#if videoData.promptIndex !== undefined}
						<div class="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-0.5 font-mono text-sm font-medium tabular-nums text-white" data-output-label>
							{videoData.promptIndex + 1}.{videoData.promptImage}
						</div>
					{/if}
				</div>
			{/each}
			{/if}

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
