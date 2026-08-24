<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
	import MediaPreview from './MediaPreview.svelte';
	import Icon from './Icon.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';
	import StarRating from './StarRating.svelte';
	import FavoriteButton from './FavoriteButton.svelte';
	import { Badge } from '$lib/components/ui';
	import { historyStore } from '$lib/stores/history';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { formatBytes, formatSeconds } from '$lib/utils/format';
	import { nsfwFilterStore, selectableMediaFiles, visibleMediaFiles } from '$lib/stores/nsfwFilter';
	import { leadIndex } from '$lib/generation/leadFile';
	import { api } from '$lib/services/api/index';
	import { getIconPath } from '$lib/utils/IconLibrary';
	import {
		getGenerationCardDensity,
		shouldShowGenerationCardCounter,
		shouldShowGenerationCardMetadataStrip,
		getGenerationCardCounterOffsetClass
	} from './generationCardDensity';
	import {
		bucketForCardWidth,
		actionsForCount,
		formatBarResolution,
		resolveCardResolution,
		mediaChipOwnsDuration
	} from './generationCardChrome';

	export let generation: GenerationHistoryItem;
	export let thumbnailSize: 'small' | 'medium' | 'large' = 'medium';
	export let showActions: boolean = true;
	export let selectable: boolean = false;
	export let selected: boolean = false;
	/** Show an always-available selection checkbox (independent of selection mode). */
	export let showCheckbox: boolean = false;
	/**
	 * The second argument is the selected file ITSELF, never its index: the card's
	 * carousel walks a filtered (`is_final`, nsfw) and re-ordered
	 * (images→videos→audio→mesh) list, so a position in it does not address the
	 * same element in the caller's `files` array. A hidden row is enough to make
	 * an index resolve to the wrong file.
	 */
	export let onSelect: ((generation: GenerationHistoryItem, file: GenerationFile | null) => void) | null = null;
	/**
	 * Justified-gallery mode: explicit media box in px (native aspect ratio) plus
	 * a bottom info bar whose chrome scales with tile width (`generationCardChrome.ts`).
	 * When null the card renders the legacy fixed aspect-[3/4] tile (models page,
	 * history modal).
	 */
	export let tile: { width: number; height: number } | null = null;

	let currentImageIndex = 0;
	let previousGenerationId = generation.id;
	let leadAppliedFor: string | null = null;

	const dispatch = createEventDispatcher();

	nsfwFilterStore.init();

	$: nsfwMode = $nsfwFilterStore.mode;
	$: availableFiles = visibleMediaFiles(selectableMediaFiles(generation.files ?? []), nsfwMode);
	$: imageFiles = availableFiles.filter((file) => file.file_type.toLowerCase() === 'image');
	$: videoFiles = availableFiles.filter((file) => file.file_type.toLowerCase() === 'video');
	// Audio and meshes carry no `is_final`/nsfw-relevant filtering of their own,
	// and `selectableMediaFiles` is image/video only, so both are pulled
	// straight from the generation's files rather than through it.
	$: audioFiles = visibleMediaFiles(
		(generation.files ?? []).filter(
			(file) => file.is_final !== false && file.file_type.toLowerCase() === 'audio'
		),
		nsfwMode
	);
	$: meshFiles = visibleMediaFiles(
		(generation.files ?? []).filter(
			(file) => file.is_final !== false && file.file_type.toLowerCase() === 'mesh'
		),
		nsfwMode
	);
	$: mediaFiles = [...imageFiles, ...videoFiles, ...audioFiles, ...meshFiles];
	$: currentMediaFile = mediaFiles[currentImageIndex];

	// Reset index if generation changes
	$: if (generation.id !== previousGenerationId) {
		currentImageIndex = 0;
		leadAppliedFor = null;
		previousGenerationId = generation.id;
	}

	// Lead with the newest derived final file (e.g. an enhance pass) when one
	// exists; the carousel itself keeps all files in stored order.
	$: if (generation.id !== leadAppliedFor && mediaFiles.length > 0) {
		currentImageIndex = leadIndex(mediaFiles);
		leadAppliedFor = generation.id;
	}

	// Ensure currentImageIndex is within bounds
	$: if (currentImageIndex >= mediaFiles.length && mediaFiles.length > 0) {
		currentImageIndex = 0;
	}

	$: isCurrentMediaVideo = currentMediaFile?.file_type?.toLowerCase() === 'video';
	$: isCurrentMediaAudio = currentMediaFile?.file_type?.toLowerCase() === 'audio';
	$: isCurrentMediaMesh = currentMediaFile?.file_type?.toLowerCase() === 'mesh';
	$: currentMeshFormat = resolveMeshFormat(currentMediaFile).toUpperCase();
	$: metaParts = [
		currentMediaFile?.width && currentMediaFile?.height
			? `${currentMediaFile.width}×${currentMediaFile.height}`
			: null,
		(isCurrentMediaVideo || isCurrentMediaAudio) && typeof currentMediaFile?.duration_seconds === 'number'
			? formatSeconds(currentMediaFile.duration_seconds)
			: null,
		isCurrentMediaVideo && currentMediaFile?.fps
			? `${Math.round(currentMediaFile.fps)}fps`
			: null,
		currentMediaFile?.file_size ? formatBytes(currentMediaFile.file_size) : null
	].filter(Boolean);

	// Top auto-tagger tags for the hover strip (quiet, capped so the tile stays calm).
	$: topSystemTags = (currentMediaFile?.system_tags ?? [])
		.slice(0, 3)
		.map((st) => st.tag.replace(/_/g, ' '));

	$: density = getGenerationCardDensity(tile);
	$: showMetadataStrip = shouldShowGenerationCardMetadataStrip(
		density,
		metaParts.length > 0 || topSystemTags.length > 0,
		generation.status
	);
	$: counterOffsetClass = getGenerationCardCounterOffsetClass(showMetadataStrip);
	$: showCarouselCounter =
		mediaFiles.length > 1 && shouldShowGenerationCardCounter(density, generation.status);

	// Justified-tile "chrome" - the bottom info bar and its affordances scale
	// with the tile's own width bucket, independent of the legacy `density`
	// table above (which still governs the media-area overlays shared with the
	// fixed-aspect card).
	$: chromeBucket = tile ? bucketForCardWidth(tile.width) : null;
	$: chromeActions = chromeBucket ? actionsForCount(chromeBucket.actionCount) : [];
	$: cardResolution = resolveCardResolution(currentMediaFile, generation.form_data);
	$: fullResolutionTitle = cardResolution ? `${cardResolution.width}×${cardResolution.height}` : undefined;
	$: barResolutionText =
		chromeBucket && cardResolution
			? formatBarResolution(cardResolution.width, cardResolution.height, chromeBucket.resolutionShort)
			: null;
	// The media chip (bottom-left) already owns duration for a single-file video,
	// so the hover strip drops it there to avoid showing it twice.
	$: hoverStripOwnsDuration = !mediaChipOwnsDuration(mediaFiles.length, isCurrentMediaVideo);
	$: barHoverMetaParts = [
		hoverStripOwnsDuration &&
		(isCurrentMediaVideo || isCurrentMediaAudio) &&
		typeof currentMediaFile?.duration_seconds === 'number'
			? formatSeconds(currentMediaFile.duration_seconds)
			: null,
		isCurrentMediaVideo && currentMediaFile?.fps ? `${Math.round(currentMediaFile.fps)}fps` : null,
		currentMediaFile?.file_size ? formatBytes(currentMediaFile.file_size) : null
	].filter(Boolean);
	$: chromeMediaChipText =
		mediaFiles.length > 1
			? `${currentImageIndex + 1}/${mediaFiles.length}`
			: isCurrentMediaVideo && typeof currentMediaFile?.duration_seconds === 'number'
				? formatSeconds(currentMediaFile.duration_seconds)
				: '';
	$: ratingStarPath = getIconPath('star') as string;

	function handlePrevImage(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		currentImageIndex = currentImageIndex > 0 ? currentImageIndex - 1 : mediaFiles.length - 1;
	}

	function handleNextImage(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		currentImageIndex = currentImageIndex < mediaFiles.length - 1 ? currentImageIndex + 1 : 0;
	}

	function handleCardClick() {
		if (selectable && onSelect) {
			onSelect(generation, currentMediaFile ?? null);
		} else if (mediaFiles.length > 0) {
			dispatch('imageClick', { files: generation.files, generationId: generation.id });
		}
	}

	function handleViewClick(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		dispatch('viewClick', generation);
	}

	function handleDeleteClick(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		dispatch('deleteClick', generation);
	}

	function handleDownloadClick(e: Event) {
		e.stopPropagation();
		e.preventDefault();
		if (!currentMediaFile) return;
		const filename = currentMediaFile.file_path.split('/').pop() || currentMediaFile.file_path;
		const link = document.createElement('a');
		link.href = api.getGenerationImageURL(generation.id, filename);
		link.download = filename;
		link.click();
	}

	function handleFavoriteToggle() {
		historyStore.toggleFavorite(generation.id);
	}

	function handleRatingChange(rating: number) {
		historyStore.setRating(generation.id, rating);
	}

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	function getStatusVariant(status: string): BadgeVariant {
		const statusVariants: Record<string, BadgeVariant> = {
			pending: 'warning',
			running: 'signal',
			completed: 'success',
			failed: 'danger',
			cancelled: 'neutral'
		};
		return statusVariants[status] || 'neutral';
	}

	function getStatusDotClass(status: string): string {
		const dotClasses: Record<BadgeVariant, string> = {
			neutral: 'bg-fg-muted',
			success: 'bg-success',
			warning: 'bg-warning',
			danger: 'bg-danger',
			info: 'bg-info',
			signal: 'bg-signal'
		};
		return dotClasses[getStatusVariant(status)];
	}
</script>

<div class="group" style={tile ? `width: ${tile.width}px` : undefined}>
	<div
		class="tile-frame relative cursor-pointer rounded-lg overflow-hidden transition-colors duration-100 ease-out border {tile
			? 'bg-black'
			: 'bg-surface-1'} {selected
			? 'tile-selected border-line-hover'
			: 'border-line-strong hover:border-line-hover'}"
	>
		<!-- Selection wash when selected -->
		{#if selected}
			<div class="absolute inset-0 bg-signal/15 z-40 pointer-events-none"></div>
		{/if}

		<!-- Always-available selection checkbox (top-left) -->
		{#if showCheckbox}
			<button
				class="absolute top-2 left-2 z-40 rounded flex items-center justify-center transition-colors duration-100 {selected
					? 'bg-accent text-accent-contrast'
					: 'bg-black/45 backdrop-blur-sm ring-1 ring-white/60 text-transparent hover:bg-black/70'}"
				style={chromeBucket ? `width: ${chromeBucket.checkboxSize}px; height: ${chromeBucket.checkboxSize}px` : undefined}
				class:w-5={!chromeBucket}
				class:h-5={!chromeBucket}
				on:click|stopPropagation={() => onSelect?.(generation, currentMediaFile ?? null)}
				aria-label={selected ? 'Deselect generation' : 'Select generation'}
				aria-pressed={selected}
			>
				{#if selected}
					<Icon name="check" className="w-3.5 h-3.5" strokeWidth={3} />
				{/if}
			</button>
		{/if}

		<!-- Media -->
		<div
			class="media-zoom relative overflow-hidden {tile ? '' : 'aspect-[3/4]'}"
			style={tile ? `height: ${tile.height}px` : undefined}
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
			{#if mediaFiles.length > 0 && currentMediaFile}
				{#if isCurrentMediaMesh}
					<!-- Grid tiles stay lightweight: no <model-viewer> here, just a marker. -->
					<div
						class="w-full h-full flex flex-col items-center justify-center gap-2"
						style={placeholderTint(currentMediaFile.file_path)}
					>
						<Icon name="cube" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{currentMeshFormat}</span>
					</div>
				{:else if isCurrentMediaAudio}
					<!-- No waveform in the grid tile - a compact affordance (icon + duration)
					     rather than embedding the full AudioPlayer, mirroring the mesh marker. -->
					<div
						class="w-full h-full flex flex-col items-center justify-center gap-2"
						style={placeholderTint(currentMediaFile.file_path)}
					>
						<Icon name="audio" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
						{#if typeof currentMediaFile.duration_seconds === 'number'}
							<span class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-subtle">
								{formatSeconds(currentMediaFile.duration_seconds)}
							</span>
						{/if}
					</div>
				{:else}
					{#key currentMediaFile.file_path}
						<MediaPreview
							file={currentMediaFile}
							generationId={generation.id}
							className="w-full h-full {tile ? 'object-contain' : 'object-cover'}"
							{thumbnailSize}
							loadFullOnClick={false}
						/>
					{/key}
				{/if}
			{:else}
				<div
					class="w-full h-full min-h-[8rem] flex items-center justify-center bg-surface-2"
					style={generation.status === 'completed' ? placeholderTint(generation.id) : undefined}
				>
					{#if generation.status === 'pending' || generation.status === 'running'}
						<div class="flex flex-col items-center gap-2 py-6">
							<span
								class="font-mono text-2xs uppercase tracking-[0.07em] {generation.status === 'running'
									? 'text-signal'
									: 'text-warning'}"
							>
								{generation.status === 'running' ? 'Generating' : 'Queued'}
							</span>
							<div class="tick-track w-24">
								<div
									class="tick-fill {generation.status === 'running' ? 'bg-signal-solid' : 'bg-warning-solid'}"
									style="width: {generation.status === 'running'
										? Math.max(4, Math.round(generation.progress * 100))
										: 4}%"
								></div>
							</div>
						</div>
					{:else}
						<div class="flex flex-col items-center gap-2 py-6">
							<Icon name="image" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
							<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">No media</span>
						</div>
					{/if}
				</div>
			{/if}

			<!-- Status badge (non-completed only, fixed-aspect card only - the justified
			     tile shows status in its bottom info bar instead). -->
			{#if !tile && generation.status !== 'completed' && density.showStatus}
				<div class="absolute bottom-2 left-2 z-30">
					<Badge variant={getStatusVariant(generation.status)} size="sm" class="uppercase tracking-wide">
						{generation.status}
					</Badge>
				</div>
			{/if}

			<!-- Actions - top right on hover (favorite persists when set) -->
			{#if showActions && !selectable}
				{#if tile && chromeBucket}
					<div
						class="absolute top-2 right-2 z-30 flex items-center gap-0.5 bg-black/70 rounded-md p-1 backdrop-blur-sm ring-1 ring-inset ring-white/10 transition-opacity duration-100 {generation.is_favorite
							? 'opacity-100'
							: 'opacity-0 group-hover:opacity-100'}"
					>
						{#each chromeActions as action (action)}
							{#if action === 'favorite'}
								<FavoriteButton
									active={generation.is_favorite}
									tone="onMedia"
									onToggle={handleFavoriteToggle}
								/>
							{:else if action === 'view'}
								<button
									class="text-white hover:bg-white/10 rounded p-1 transition-colors duration-100"
									on:click={handleViewClick}
									aria-label="View generation details"
								>
									<Icon name="eyes" className="h-3.5 w-3.5" />
								</button>
							{:else if action === 'download'}
								<button
									class="text-white hover:bg-white/10 rounded p-1 transition-colors duration-100"
									on:click={handleDownloadClick}
									aria-label="Download"
								>
									<Icon name="download" className="h-3.5 w-3.5" />
								</button>
							{:else}
								<button
									class="text-white hover:bg-danger-solid rounded p-1 transition-colors duration-100"
									on:click={handleDeleteClick}
									aria-label="Delete generation"
								>
									<Icon name="trash" className="h-3.5 w-3.5" />
								</button>
							{/if}
						{/each}
					</div>
				{:else if !tile && density.actionMode !== 'none'}
					<div class="absolute top-2 right-2 z-30 flex items-center gap-1">
						<div
							class="bg-black/60 hover:bg-black/80 rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 flex items-center {generation.is_favorite
								? 'opacity-100'
								: 'opacity-0 group-hover:opacity-100'}"
						>
							<FavoriteButton
								active={generation.is_favorite}
								tone="onMedia"
								onToggle={handleFavoriteToggle}
							/>
						</div>
						{#if density.actionMode === 'full'}
							<button
								class="bg-black/60 hover:bg-black/80 text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100"
								on:click={handleViewClick}
								aria-label="View generation details"
							>
								<Icon name="eyes" className="h-3.5 w-3.5" />
							</button>
							<button
								class="bg-black/60 hover:bg-danger-solid text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100"
								on:click={handleDeleteClick}
								aria-label="Delete generation"
							>
								<Icon name="trash" className="h-3.5 w-3.5" />
							</button>
						{/if}
					</div>
				{/if}
			{/if}

			<!-- Media type only; the carousel already communicates the file count as 1/N
			     (fixed-aspect card only - the justified tile shows this as a bottom-left chip). -->
			{#if !tile && (mediaFiles.length > 1 || videoFiles.length > 0 || audioFiles.length > 0 || meshFiles.length > 0) && density.showMediaType}
				<div
					class="absolute top-2 z-20 bg-black/70 backdrop-blur-sm text-white rounded px-1.5 py-0.5 flex items-center gap-1 {showCheckbox ? 'left-9' : 'left-2'}"
				>
					<Icon
						name={meshFiles.length > 0
							? 'cube'
							: videoFiles.length > 0
							? 'video'
							: audioFiles.length > 0
							? 'audio'
							: 'image'}
						className="h-3 w-3"
					/>
				</div>
			{/if}

			<!-- Justified tile: media-type + position/duration chip, bottom-left. -->
			{#if tile && chromeBucket?.showMediaChip && generation.status === 'completed' && (mediaFiles.length > 1 || isCurrentMediaVideo)}
				<div
					class="absolute bottom-1.5 left-1.5 z-20 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg font-mono tabular-nums text-2xs tracking-[0.07em] whitespace-nowrap"
				>
					<Icon
						name={meshFiles.length > 0
							? 'cube'
							: videoFiles.length > 0
							? 'video'
							: audioFiles.length > 0
							? 'audio'
							: 'image'}
						className="h-2.5 w-2.5"
					/>
					{#if chromeMediaChipText}<span>{chromeMediaChipText}</span>{/if}
				</div>
			{/if}

			<!-- Justified tile: duration/fps/size, bottom-right, on hover only (resolution
			     already has its own lane in the bar below, so it never repeats here). -->
			{#if tile && chromeBucket?.showHoverMeta && generation.status === 'completed' && barHoverMetaParts.length > 0}
				<div
					class="absolute bottom-1.5 right-1.5 z-20 max-w-[60%] px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg-muted font-mono tabular-nums text-2xs tracking-[0.07em] truncate opacity-0 group-hover:opacity-100 transition-opacity duration-100"
					title={barHoverMetaParts.join(' · ')}
				>
					{barHoverMetaParts.join(' · ')}
				</div>
			{/if}

			<!-- Consolidated metadata strip - bottom, on hover (fixed-aspect card only - the
			     justified tile has its own bottom-right hover chip above). Priority order
			     (resolution > duration > fps > size) is the join order itself, so truncating
			     drops the lowest-priority parts first, from the right. -->
			{#if !tile && showMetadataStrip}
				<div
					class="absolute bottom-0 left-0 right-0 z-20 px-2 py-1.5 flex items-center gap-2 bg-gradient-to-t from-black/80 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-100 pointer-events-none {density.metadataMode ===
					'full'
						? 'justify-between'
						: 'justify-end'}"
				>
					{#if density.metadataMode === 'full'}
						<span
							class="font-mono text-xs text-white/60 truncate min-w-0"
							title={topSystemTags.join(' · ')}
						>
							{topSystemTags.join(' · ')}
						</span>
					{/if}
					<span
						class="font-mono tabular-nums text-xs uppercase tracking-[0.07em] text-white/90 truncate min-w-0 shrink-0"
						title={metaParts.join(' · ')}
					>
						{metaParts.join(' · ')}
					</span>
				</div>
			{/if}

			<!-- File navigation -->
			{#if mediaFiles.length > 1 && density.showCarouselArrows}
				<button
					class="absolute left-1.5 top-1/2 -translate-y-1/2 z-30 bg-black/60 hover:bg-black/80 text-white opacity-0 group-hover:opacity-100 transition-opacity duration-100 rounded p-1.5"
					on:click={handlePrevImage}
					aria-label="Previous image"
				>
					<Icon name="chevron-left" className="h-3.5 w-3.5" />
				</button>
				<button
					class="absolute right-1.5 top-1/2 -translate-y-1/2 z-30 bg-black/60 hover:bg-black/80 text-white opacity-0 group-hover:opacity-100 transition-opacity duration-100 rounded p-1.5"
					on:click={handleNextImage}
					aria-label="Next image"
				>
					<Icon name="chevron-right" className="h-3.5 w-3.5" />
				</button>
			{/if}
			{#if showCarouselCounter}
				<div
					class="absolute {counterOffsetClass} left-1/2 -translate-x-1/2 z-20 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity duration-100"
				>
					<span class="font-mono tabular-nums">{currentImageIndex + 1}/{mediaFiles.length}</span>
				</div>
			{/if}

			<!-- Tick-ruler progress for running generations with media -->
			{#if generation.status === 'running' && generation.progress > 0 && mediaFiles.length > 0}
				<div class="absolute bottom-0 left-0 right-0 z-30">
					<div class="tick-track">
						<div
							class="tick-fill bg-signal-solid transition-all duration-300"
							style="width: {Math.round(generation.progress * 100)}%"
						></div>
					</div>
				</div>
			{/if}
		</div>

		<!-- Justified tile: persistent bottom info bar. Rating (or status, while not
		     completed) sits left; resolution (+ time, full bucket only) sits right.
		     Replaces the floating status badge and the below-frame caption that the
		     fixed-aspect card still uses. -->
		{#if tile && chromeBucket}
			<div
				class="flex items-center justify-between gap-2 px-2 bg-surface-1 border-t border-line overflow-hidden"
				style="height: {chromeBucket.barHeight}px"
			>
				<div class="flex items-center gap-1 min-w-0 overflow-hidden">
					{#if generation.status === 'completed'}
						{#if chromeBucket.showStars}
							<StarRating
								value={generation.rating}
								tone="default"
								readonly={!showActions}
								onChange={handleRatingChange}
							/>
						{:else if chromeBucket.showRatingChip}
							<div class="flex items-center gap-1 {generation.rating ? 'text-signal' : 'text-line-hover'}">
								<svg
									class="w-2.5 h-2.5"
									viewBox="0 0 24 24"
									fill={generation.rating ? 'currentColor' : 'none'}
									stroke="currentColor"
									stroke-width="1.5"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d={ratingStarPath} />
								</svg>
								<span class="font-mono tabular-nums text-2xs">{generation.rating || '–'}</span>
							</div>
						{/if}
					{:else if chromeBucket.statusStyle === 'label'}
						<Badge variant={getStatusVariant(generation.status)} size="sm" class="uppercase tracking-wide">
							{generation.status}
						</Badge>
					{:else}
						<span
							class="w-1.5 h-1.5 rounded-full shrink-0 {getStatusDotClass(generation.status)}"
							title={generation.status}
						></span>
					{/if}
				</div>
				<div class="flex items-center gap-1.5 ml-auto shrink-0 font-mono tabular-nums text-2xs tracking-[0.07em]">
					{#if barResolutionText}
						<span class="text-fg-muted whitespace-nowrap" title={fullResolutionTitle}>{barResolutionText}</span>
					{/if}
					{#if chromeBucket.showBarTime}
						<span class="text-line-hover">·</span>
						<span class="text-fg-subtle whitespace-nowrap">{timeAgo(generation.created_at)}</span>
					{/if}
				</div>
			</div>
		{/if}

		<!-- Error Message Footer -->
		{#if generation.error_message}
			<div class="px-2 py-2 bg-surface-1">
				<div class="p-1.5 bg-danger/10 border border-danger/25 rounded text-2xs text-danger truncate" title={generation.error_message}>
					{generation.error_message}
				</div>
			</div>
		{/if}
	</div>
</div>

<style>
	/* Hover zoom — scale the media inside the fixed, clipped frame so the tile
	   itself stays put and the image gently pushes toward the viewer. */
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

	/* Tick-ruler progress bar */
	.tick-track {
		position: relative;
		height: 4px;
		background: rgba(0, 0, 0, 0.35);
		overflow: hidden;
	}
	.tick-fill {
		height: 100%;
	}
	.tick-track::after {
		content: '';
		position: absolute;
		inset: 0;
		background: repeating-linear-gradient(
			90deg,
			transparent 0,
			transparent 7px,
			rgba(0, 0, 0, 0.45) 7px,
			rgba(0, 0, 0, 0.45) 8px
		);
		pointer-events: none;
	}
</style>
