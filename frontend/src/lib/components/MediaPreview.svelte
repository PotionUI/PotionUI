<script lang="ts">
	import type { GenerationFile } from '$lib/types/history';
	import { api } from '$lib/services/api/index';
	import { nsfwFilterStore } from '$lib/stores/nsfwFilter';
	import { nsfwRevealStore, revealKey } from '$lib/stores/nsfwReveal';

	export let file: GenerationFile;
	export let generationId: string;
	export let thumbnailSize: 'small' | 'medium' | 'large' = 'medium';
	export let loadFullOnClick: boolean = true;
	export let className: string = '';
	export let showVideoControls: boolean = false;

	let error = false;
	let fullLoaded = false;
	let isHovering = false;
	let activeFilePath = file.file_path;

	nsfwFilterStore.init();

	$: fileRevealKey = revealKey(generationId, file);
	$: isHiddenNsfw = !!file.nsfw && $nsfwFilterStore.mode === 'hide';
	$: shouldBlur =
		!!file.nsfw && $nsfwFilterStore.mode === 'blur' && !$nsfwRevealStore.has(fileRevealKey);

	function handleReveal(event: MouseEvent | KeyboardEvent) {
		event.stopPropagation();
		nsfwRevealStore.reveal(fileRevealKey);
	}

	$: isVideo = file.file_type.toLowerCase() === 'video';
	$: filename = file.file_path.split('/').pop() || file.file_path;

	function isBrowserUrl(value: string | undefined): value is string {
		if (!value) return false;
		return /^(https?:|data:|blob:)/i.test(value) || value.startsWith('/api/') || value.startsWith('/frontend-kit/');
	}

	$: thumbnailCandidate =
		thumbnailSize === 'small'
			? file.thumbnail_small
			: thumbnailSize === 'large'
				? file.thumbnail_large
				: file.thumbnail_medium;
	$: directThumbnail = isBrowserUrl(thumbnailCandidate) ? thumbnailCandidate : undefined;
	$: if (file.file_path !== activeFilePath) {
		activeFilePath = file.file_path;
		error = false;
		fullLoaded = false;
		isHovering = false;
	}

	function handleImageError() {
		error = true;
	}

	function handleClick() {
		if (loadFullOnClick && !isVideo && !fullLoaded) {
			fullLoaded = true;
		}
	}

	function handleMouseEnter() {
		if (isVideo && !showVideoControls) {
			isHovering = true;
		}
	}

	function handleMouseLeave() {
		if (isVideo && !showVideoControls) {
			isHovering = false;
		}
	}

	// Get the appropriate URL based on state
	$: imageUrl = (() => {
		if (!fullLoaded && directThumbnail) return directThumbnail;
		if (isVideo && !showVideoControls) {
			// For videos, always use animated thumbnail (static when not hovering, animated when hovering)
			return api.getGenerationThumbnailURL(generationId, filename, thumbnailSize, isHovering);
		} else if (isVideo && showVideoControls) {
			// For videos with controls enabled, use actual video URL
			return api.getGenerationImageURL(generationId, filename);
		} else {
			// For images, use full size if loaded, otherwise thumbnail
			if (fullLoaded) {
				return api.getGenerationImageURL(generationId, filename);
			} else {
				return api.getGenerationThumbnailURL(generationId, filename, thumbnailSize);
			}
		}
	})();
</script>

{#if isHiddenNsfw}
	<div
		class="flex flex-col items-center justify-center w-full h-full bg-surface-2 text-fg-subtle {className}"
	>
		<svg class="w-8 h-8 mb-1.5" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
		</svg>
		<span class="text-xs font-medium">Hidden</span>
	</div>
{:else if error}
	<div
		class="flex flex-col items-center justify-center w-full h-full bg-surface-2 text-fg-subtle {className}"
	>
		<svg
			class="w-12 h-12 mb-2"
			fill="none"
			stroke="currentColor"
			viewBox="0 0 24 24"
			xmlns="http://www.w3.org/2000/svg"
		>
			<path
				stroke-linecap="round"
				stroke-linejoin="round"
				stroke-width="2"
				d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
			></path>
		</svg>
		<span class="text-sm">Failed to load</span>
	</div>
{:else if isVideo && !showVideoControls}
	<!-- Video with animated thumbnail preview -->
	<div
		class="relative overflow-hidden flex items-center justify-center {className}"
		on:mouseenter={handleMouseEnter}
		on:mouseleave={handleMouseLeave}
	>
		<img
			src={imageUrl}
			alt="Video preview"
			class="w-full h-full object-cover {shouldBlur ? 'blur-2xl scale-110' : ''} {loadFullOnClick ? 'cursor-pointer' : ''}"
			role={loadFullOnClick ? 'button' : undefined}
			tabindex={loadFullOnClick && !shouldBlur ? 0 : undefined}
			on:error={handleImageError}
			on:click={handleClick}
			on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(); } }}
			loading="lazy"
			decoding="async"
		/>
		{#if shouldBlur}
			<button
				type="button"
				class="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-canvas/40 text-fg cursor-pointer"
				aria-label="Sensitive content, click to reveal"
				on:click={handleReveal}
			>
				<svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
				</svg>
				<span class="text-xs font-medium">Sensitive</span>
				<span class="text-[10px] text-fg-muted">Click to reveal</span>
			</button>
		{:else if loadFullOnClick}
			<div
				class="absolute bottom-2 right-2 bg-black bg-opacity-60 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
			>
				<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
					<path
						d="M6.3 2.841A1.5 1.5 0 004 4.11V15.89a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z"
					/>
				</svg>
				Click for video
			</div>
		{/if}
	</div>
{:else if isVideo && showVideoControls}
	<!-- Full video player -->
	<div class="relative overflow-hidden w-full h-full flex items-center justify-center bg-black {className}">
		<video
			src={imageUrl}
			class="max-w-full max-h-full object-contain {shouldBlur ? 'blur-2xl scale-110' : ''}"
			controls={showVideoControls && !shouldBlur}
			autoplay={false}
			muted
			playsinline
			on:error={handleImageError}
		>
			<track kind="captions" />
			Your browser does not support the video tag.
		</video>
		{#if shouldBlur}
			<button
				type="button"
				class="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-canvas/40 text-fg cursor-pointer"
				aria-label="Sensitive content, click to reveal"
				on:click={handleReveal}
			>
				<svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
				</svg>
				<span class="text-xs font-medium">Sensitive</span>
				<span class="text-[10px] text-fg-muted">Click to reveal</span>
			</button>
		{/if}
	</div>
{:else if imageUrl}
	<!-- Image with thumbnail -->
	<div class="relative overflow-hidden flex items-center justify-center {className}">
		<img
			src={imageUrl}
			alt="Generated content"
			class="w-full h-full object-cover {shouldBlur ? 'blur-2xl scale-110' : ''} {loadFullOnClick && !fullLoaded ? 'cursor-pointer' : ''}"
			role={loadFullOnClick && !fullLoaded ? 'button' : undefined}
			tabindex={loadFullOnClick && !fullLoaded && !shouldBlur ? 0 : undefined}
			on:error={handleImageError}
			on:click={handleClick}
			on:keydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleClick(); } }}
			loading="lazy"
			decoding="async"
		/>
		{#if shouldBlur}
			<button
				type="button"
				class="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-canvas/40 text-fg cursor-pointer"
				aria-label="Sensitive content, click to reveal"
				on:click={handleReveal}
			>
				<svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
				</svg>
				<span class="text-xs font-medium">Sensitive</span>
				<span class="text-[10px] text-fg-muted">Click to reveal</span>
			</button>
		{:else if !fullLoaded && loadFullOnClick}
			<div
				class="absolute bottom-2 right-2 bg-black bg-opacity-60 text-white text-xs px-2 py-1 rounded"
			>
				Click for full size
			</div>
		{/if}
	</div>
{/if}
