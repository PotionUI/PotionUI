<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import { formatSeconds } from '$lib/utils/format';
	import type { LibraryItem } from '$lib/services/api/library';
	import {
		bucketForCardWidth,
		formatBarResolution,
		resolveCardResolution
	} from '$lib/components/generationCardChrome';
	import {
		libraryActionsForCount,
		libraryItemDisplayName,
		libraryItemGridSrc,
		libraryItemIcon,
		libraryItemMetaParts
	} from '$lib/library/libraryItemMeta';

	// The library's counterpart to GenerationCard's justified tile: the same
	// width-bucketed chrome (generationCardChrome.ts), minus the affordances an
	// upload has no state for, plus the filename an upload is actually known by.
	export let item: LibraryItem;
	export let tile: { width: number; height: number };
	export let selected = false;
	export let selectable = false;
	export let showCheckbox = true;
	export let showActions = true;
	export let onSelect: ((item: LibraryItem) => void) | null = null;

	const dispatch = createEventDispatcher<{ open: LibraryItem; delete: LibraryItem }>();

	$: bucket = bucketForCardWidth(tile.width);
	$: actions = showActions ? libraryActionsForCount(bucket.actionCount) : [];
	$: displayName = libraryItemDisplayName(item);
	$: gridSrc = libraryItemGridSrc(item);
	$: metaParts = libraryItemMetaParts(item);
	$: mediaIcon = libraryItemIcon(item.media_type);
	$: isVideo = (item.media_type ?? '').toLowerCase() === 'video';
	$: isAudio = (item.media_type ?? '').toLowerCase() === 'audio';
	$: resolution = resolveCardResolution(item, null);
	$: barResolutionText = resolution
		? formatBarResolution(resolution.width, resolution.height, bucket.resolutionShort)
		: null;
	$: fullResolutionTitle = resolution ? `${resolution.width}×${resolution.height}` : undefined;
	// The bottom-left chip already carries a video's duration, so the hover strip
	// drops it there rather than showing the same value twice.
	$: hoverMetaParts = isVideo ? metaParts.slice(1) : metaParts;
	$: durationText =
		typeof item.duration_seconds === 'number' ? formatSeconds(item.duration_seconds) : '';

	function handleCardClick() {
		if (selectable && onSelect) {
			onSelect(item);
		} else {
			dispatch('open', item);
		}
	}

	function handleView(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		dispatch('open', item);
	}

	function handleDelete(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		dispatch('delete', item);
	}

	function handleDownload(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		const link = document.createElement('a');
		link.href = item.url;
		link.download = displayName;
		link.click();
	}
</script>

<div class="group" style="width: {tile.width}px">
	<div
		class="relative cursor-pointer rounded-lg overflow-hidden transition-colors duration-100 ease-out border bg-black {selected
			? 'border-line-hover'
			: 'border-line-strong hover:border-line-hover'}"
	>
		{#if selected}
			<div class="absolute inset-0 bg-signal/15 z-40 pointer-events-none"></div>
		{/if}

		{#if showCheckbox}
			<button
				class="absolute top-2 left-2 z-40 rounded flex items-center justify-center transition-colors duration-100 {selected
					? 'bg-accent text-accent-contrast'
					: 'bg-black/45 backdrop-blur-sm ring-1 ring-white/60 text-transparent hover:bg-black/70'}"
				style="width: {bucket.checkboxSize}px; height: {bucket.checkboxSize}px"
				on:click|stopPropagation={() => onSelect?.(item)}
				aria-label={selected ? 'Deselect item' : 'Select item'}
				aria-pressed={selected}
			>
				{#if selected}
					<Icon name="check" className="w-3.5 h-3.5" strokeWidth={3} />
				{/if}
			</button>
		{/if}

		<!-- Media -->
		<div
			class="media-zoom relative overflow-hidden"
			style="height: {tile.height}px"
			role="button"
			tabindex="0"
			on:click={handleCardClick}
			on:keydown={(e) => {
				if (e.key === 'Enter' || e.key === ' ') {
					e.preventDefault();
					handleCardClick();
				}
			}}
		>
			{#if isAudio}
				<div
					class="w-full h-full flex flex-col items-center justify-center gap-2"
					style={placeholderTint(displayName)}
				>
					<Icon name="audio" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
					{#if durationText}
						<span class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-subtle">
							{durationText}
						</span>
					{/if}
				</div>
			{:else if isVideo && item.thumbnail_medium}
				<!-- A generated thumbnail is a static image, far cheaper for the grid
				     to render than the video itself - the media-kind chip below still
				     marks it as a video. -->
				<img src={gridSrc} alt={displayName} class="w-full h-full object-contain" loading="lazy" />
			{:else if isVideo}
				<!-- No thumbnail yet (row predates thumbnail generation) - fall back
				     to the video itself, same as before. -->
				<video
					src={gridSrc}
					class="w-full h-full object-contain"
					muted
					playsinline
					preload="metadata"
				>
					<track kind="captions" />
				</video>
			{:else}
				<img src={gridSrc} alt={displayName} class="w-full h-full object-contain" loading="lazy" />
			{/if}

			<!-- Actions - top right on hover -->
			{#if actions.length > 0 && !selectable}
				<div
					class="absolute top-2 right-2 z-30 flex items-center gap-0.5 bg-black/70 rounded-md p-1 backdrop-blur-sm ring-1 ring-inset ring-white/10 opacity-0 group-hover:opacity-100 transition-opacity duration-100"
				>
					{#each actions as action (action)}
						{#if action === 'view'}
							<button
								class="text-white hover:bg-white/10 rounded p-1 transition-colors duration-100"
								on:click={handleView}
								aria-label="Open library item"
							>
								<Icon name="eyes" className="h-3.5 w-3.5" />
							</button>
						{:else if action === 'download'}
							<button
								class="text-white hover:bg-white/10 rounded p-1 transition-colors duration-100"
								on:click={handleDownload}
								aria-label="Download"
							>
								<Icon name="download" className="h-3.5 w-3.5" />
							</button>
						{:else}
							<button
								class="text-white hover:bg-danger-solid rounded p-1 transition-colors duration-100"
								on:click={handleDelete}
								aria-label="Delete library item"
							>
								<Icon name="trash" className="h-3.5 w-3.5" />
							</button>
						{/if}
					{/each}
				</div>
			{/if}

			<!-- Media kind (+ a video's duration), bottom-left -->
			{#if bucket.showMediaChip}
				<div
					class="absolute bottom-1.5 left-1.5 z-20 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg font-mono tabular-nums text-2xs tracking-[0.07em] whitespace-nowrap"
				>
					<Icon name={mediaIcon} className="h-2.5 w-2.5" />
					{#if isVideo && durationText}<span>{durationText}</span>{/if}
				</div>
			{/if}

			<!-- Duration/fps/size, bottom-right, on hover only -->
			{#if bucket.showHoverMeta && hoverMetaParts.length > 0}
				<div
					class="absolute bottom-1.5 right-1.5 z-20 max-w-[60%] px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg-muted font-mono tabular-nums text-2xs tracking-[0.07em] truncate opacity-0 group-hover:opacity-100 transition-opacity duration-100"
					title={hoverMetaParts.join(' · ')}
				>
					{hoverMetaParts.join(' · ')}
				</div>
			{/if}
		</div>

		<!-- Bottom info bar: the name an upload is known by, then its resolution. -->
		<div
			class="flex items-center justify-between gap-2 px-2 bg-surface-1 border-t border-line overflow-hidden"
			style="height: {bucket.barHeight}px"
		>
			<span class="text-2xs text-fg-muted truncate min-w-0" title={displayName}>{displayName}</span>
			<div
				class="flex items-center gap-1.5 ml-auto shrink-0 font-mono tabular-nums text-2xs tracking-[0.07em]"
			>
				{#if barResolutionText}
					<span class="text-fg-muted whitespace-nowrap" title={fullResolutionTitle}>
						{barResolutionText}
					</span>
				{/if}
				{#if bucket.showBarTime && item.created_at}
					<span class="text-line-hover">·</span>
					<span class="text-fg-subtle whitespace-nowrap">{timeAgo(item.created_at)}</span>
				{/if}
			</div>
		</div>
	</div>
</div>

<style>
	/* Hover zoom — same treatment as the generation card: the media scales
	   inside the clipped frame while the tile itself stays put. */
	.media-zoom :global(img),
	.media-zoom :global(video) {
		transition: transform 450ms cubic-bezier(0.22, 1, 0.36, 1);
		will-change: transform;
	}
	.group:hover .media-zoom :global(img),
	.group:hover .media-zoom :global(video) {
		transform: scale(1.06);
	}
	@media (prefers-reduced-motion: reduce) {
		.media-zoom :global(img),
		.media-zoom :global(video) {
			transition: none;
		}
		.group:hover .media-zoom :global(img),
		.group:hover .media-zoom :global(video) {
			transform: none;
		}
	}
</style>
