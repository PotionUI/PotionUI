<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { formatBytes } from './formatters';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	/** Preview/media files for the model (images, thumbnails, videos), pre-sorted by display_order. */
	export let files: any[] = [];
	export let currentIndex: number = 0;
	export let displayName: string = '';
	export let selectedTags: Array<{ id: string; name: string }> = [];
	/** Only the admin modal lets you edit model tags from the media pane. */
	export let tagsEditable: boolean = false;
	export let selectedTagIds: string[] = [];

	const dispatch = createEventDispatcher<{ prev: void; next: void; tagsChange: string[] }>();

	$: currentFile = files[currentIndex];

	function getMediaUrl(file: any): string {
		if (file.file_type === 'video') {
			return file.url || '';
		}
		if (file.url && file.url.includes('/api/media/files/')) {
			return file.url.includes('?') ? `${file.url}&size=large` : `${file.url}?size=large`;
		}
		return file.url || '';
	}

	const isVideo = (file: any) => file?.file_type === 'video';

	function handleTagsChange(event: CustomEvent<string[]>) {
		dispatch('tagsChange', event.detail);
	}
</script>

<div class="flex-1 relative min-h-0 bg-black group">
	<!-- Navigation Arrows -->
	{#if files.length > 0 && currentFile && currentIndex > 0}
		<button
			class="absolute left-4 top-1/2 transform -translate-y-1/2 z-10 bg-black/70 hover:bg-black/80 text-white p-3 rounded"
			on:click={() => dispatch('prev')}
			aria-label="Previous image"
		>
			<Icon name="chevron-left" className="w-6 h-6" />
		</button>
	{/if}

	{#if files.length > 0 && currentFile && currentIndex < files.length - 1}
		<button
			class="absolute right-4 top-1/2 transform -translate-y-1/2 z-10 bg-black/70 hover:bg-black/80 text-white p-3 rounded"
			on:click={() => dispatch('next')}
			aria-label="Next image"
		>
			<Icon name="chevron-right" className="w-6 h-6" />
		</button>
	{/if}

	{#if tagsEditable}
		<!-- Action buttons - top-right corner, shown on hover -->
		<div class="absolute top-4 right-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex gap-2">
			<TagSelector
				{selectedTagIds}
				on:change={handleTagsChange}
				placeholder="Add model tags..."
				allowCreate={true}
				compact={true}
				iconOnly={true}
				tagType="MODEL"
			/>
		</div>
	{/if}

	<!-- Top-left metadata overlay - shown on hover -->
	<div class="absolute top-4 left-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
		<div class="flex flex-col gap-2">
			<div class="flex items-center gap-2">
				<!-- File counter -->
				{#if files.length > 1}
					<div class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded text-sm font-medium shadow-lg">
						<span class="font-mono tabular-nums">
							{currentIndex + 1} / {files.length}
						</span>
					</div>
				{/if}

				<!-- File size -->
				{#if currentFile?.file_size}
					<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
						{formatBytes(currentFile.file_size)}
					</span>
				{/if}
			</div>

			<!-- Selected tags display - shown on hover -->
			{#if selectedTags.length > 0}
				<div class="flex flex-wrap gap-1">
					{#each selectedTags as tag}
						<span class="px-2 py-1 bg-signal/80 backdrop-blur-sm text-white text-xs rounded shadow-lg">
							{tag.name}
						</span>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	<!-- Media Display (Image or Video) -->
	<!-- Absolutely filled so the media can never dictate the pane's size -->
	{#if files.length > 0 && currentFile}
		{#if isVideo(currentFile)}
			<!-- Video Player -->
			<video
				src={getMediaUrl(currentFile)}
				class="absolute inset-0 h-full w-full object-contain p-3 md:p-6"
				controls
				playsinline
			>
				<track kind="captions" />
				Your browser does not support the video tag.
			</video>
		{:else}
			<!-- Image -->
			<img
				src={getMediaUrl(currentFile)}
				alt={displayName}
				class="absolute inset-0 h-full w-full object-contain p-3 md:p-6"
			/>
		{/if}
	{:else}
		<!-- No media placeholder -->
		<div
			class="absolute inset-0 flex flex-col items-center justify-center gap-4"
			style={displayName ? placeholderTint(displayName) : undefined}
		>
			<Icon name="image" className="w-24 h-24 text-fg-subtle" />
			<p class="text-fg-muted">No preview images available</p>
		</div>
	{/if}
</div>
