<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';
	import PreviewClipPlayer from '$lib/components/workbench/PreviewClipPlayer.svelte';
	import LiveElapsed from '$lib/components/LiveElapsed.svelte';
	import { liveWatcher } from '$lib/generation/live/liveDefault';
	import { percentOf, tileStepLabel } from '$lib/generation/live/liveView';
	import { authStore } from '$lib/stores/auth';
	import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
	import MediaPreview from './MediaPreview.svelte';
	import ContentPolicyTile from '$lib/components/ContentPolicyTile.svelte';
	import Icon from './Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';
	import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';
	import StarRating from './StarRating.svelte';
	import { Badge, CopyButton, IconButton } from '$lib/components/ui';
	import { historyStore } from '$lib/stores/history';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { generationRunLabel } from '$lib/utils/generationTime';
	import { formatBytes, formatSeconds } from '$lib/utils/format';
	import { nsfwFilterStore, selectableMediaFiles, visibleMediaFiles, isUnratedFile } from '$lib/stores/nsfwFilter';
	import FailureNotice from '$lib/components/FailureNotice.svelte';
	import { isContentPolicyCode, policyShowsErrorId, firstHintLine } from '$lib/generation/failurePolicy';
	import { leadIndex } from '$lib/generation/leadFile';
	import { isStackEntry, isLooseCell, stackBadge, stackInfo, cellChip, gridIdOf } from '$lib/generation/compare/view/historyGrid';
	import { getIconPath } from '$lib/utils/IconLibrary';
	import GenerationOriginChip from '$lib/components/GenerationOriginChip.svelte';
	import { describeOrigin } from '$lib/generation/origin';
	import EntryActions from '$lib/components/tools/EntryActions.svelte';
	import ToolRunBand from '$lib/components/tools/ToolRunBand.svelte';
	import { mediaToolRuns } from '$lib/media-tools/runs';
	import { runForGeneration } from '$lib/tools/toolRuns';
	import type { EntryMenuController } from '$lib/tools/entryMenu';
	import type { MediaTool, MediaToolContext, ToolRunExtra } from '$lib/tools/tools';
	import {
		getGenerationCardDensity,
		shouldShowGenerationCardCounter,
		shouldShowGenerationCardMetadataStrip,
		getGenerationCardCounterOffsetClass
	} from './generationCardDensity';
	import {
		bucketForCardWidth,
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
	export let disabled: boolean = false;
	export let interactive: boolean = true;
	export let label: string | null = null;
	/**
	 * The second argument is the selected file ITSELF, never its index: the card's
	 * carousel walks a filtered (`is_final`, nsfw) and re-ordered
	 * (images→videos→audio→mesh) list, so a position in it does not address the
	 * same element in the caller's `files` array. A hidden row is enough to make
	 * an index resolve to the wrong file. The third argument is the originating
	 * pointer event (omitted for a keyboard activation), so a multi-select grid
	 * can read its modifier keys for shift-range / ctrl-toggle behavior.
	 */
	export let onSelect:
		| ((generation: GenerationHistoryItem, file: GenerationFile | null, event?: MouseEvent) => void)
		| null = null;
	export let entryMenu: EntryMenuController<GenerationHistoryItem, GenerationFile> | null = null;
	/**
	 * Justified-gallery mode: explicit media box in px (native aspect ratio) plus
	 * a bottom info bar whose chrome scales with tile width (`generationCardChrome.ts`).
	 * When null the card renders the legacy fixed aspect-[3/4] tile (models page,
	 * history modal).
	 */
	export let tile: { width: number; height: number } | null = null;
	export let live: boolean = false;

	const liveStates = liveWatcher.states;
	let rootEl: HTMLElement | undefined;
	let visible = false;
	let releaseTile: (() => void) | null = null;
	let watchedTileId = '';

	$: isLiveTile = live && (generation.status === 'pending' || generation.status === 'running');
	$: liveTile = isLiveTile ? ($liveStates[generation.id] ?? null) : null;
	$: syncTileWatch(isLiveTile && visible, generation.id, $liveStates);

	function syncTileWatch(want: boolean, id: string, _snapshot: unknown) {
		if (releaseTile && (!want || watchedTileId !== id)) {
			releaseTile();
			releaseTile = null;
			watchedTileId = '';
		}
		if (want && !releaseTile) {
			releaseTile = liveWatcher.watch(id, 'tile', generation.status === 'pending' ? 'pending' : 'running');
			watchedTileId = releaseTile ? id : '';
		}
	}

	onMount(() => {
		if (typeof IntersectionObserver === 'undefined' || !rootEl) {
			visible = true;
			return;
		}
		const observer = new IntersectionObserver(
			(entries) => {
				visible = entries.some((entry) => entry.isIntersecting);
			},
			{ rootMargin: '160px' }
		);
		observer.observe(rootEl);
		return () => observer.disconnect();
	});

	onDestroy(() => {
		releaseTile?.();
		releaseTile = null;
	});

	let currentImageIndex = 0;
	let previousGenerationId = generation.id;
	let leadAppliedFor: string | null = null;

	const dispatch = createEventDispatcher();

	nsfwFilterStore.init();

	$: nsfwMode = $nsfwFilterStore.mode;
	$: availableFiles = visibleMediaFiles(selectableMediaFiles(generation.files ?? []), nsfwMode);
	$: imageFiles = availableFiles.filter((file) => file.file_type?.toLowerCase() === 'image' || isUnratedFile(file));
	$: videoFiles = availableFiles.filter((file) => file.file_type?.toLowerCase() === 'video');
	// Audio and meshes carry no `is_final`/nsfw-relevant filtering of their own,
	// and `selectableMediaFiles` is image/video only, so both are pulled
	// straight from the generation's files rather than through it.
	$: audioFiles = visibleMediaFiles(
		(generation.files ?? []).filter(
			(file) => file.is_final !== false && file.file_type?.toLowerCase() === 'audio'
		),
		nsfwMode
	);
	$: meshFiles = visibleMediaFiles(
		(generation.files ?? []).filter(
			(file) => file.is_final !== false && file.file_type?.toLowerCase() === 'mesh'
		),
		nsfwMode
	);
	$: mediaFiles = [...imageFiles, ...videoFiles, ...audioFiles, ...meshFiles];
	$: liveSeen = liveTile && !liveTile.previewSuppressed ? liveTile : null;
	$: liveShowsPicture = !!liveSeen && (!!liveSeen.clip || !!liveSeen.preview) && mediaFiles.length === 0;
	$: liveProgress = liveTile?.progress ?? generation.progress;
	$: livePercent = percentOf(liveProgress);
	$: liveLabel = liveTile && generation.status === 'running' ? tileStepLabel(liveTile, $authStore.user?.account_type === 'ADMIN') : '';
	$: liveWide = !!tile && tile.width >= 220;
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
	$: toolRun = runForGeneration($mediaToolRuns, generation);
	$: originInfo = describeOrigin(generation.origin);
	$: showOriginChip = !!originInfo && (generation.status === 'completed' || originInfo.kind !== 'editor');
	$: mediaChipShown =
		!!tile &&
		!!chromeBucket?.showMediaChip &&
		generation.status === 'completed' &&
		(mediaFiles.length > 1 || isCurrentMediaVideo);
	$: menuEnabled = !!(tile && chromeBucket && entryMenu);
	$: barRunLabel = chromeBucket?.showBarDuration ? generationRunLabel(generation) : null;
	$: durationReplacesResolution = !!(barRunLabel && chromeBucket?.durationReplacesResolution);
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
	$: stackEntry = isStackEntry(generation);
	$: stackText = stackInfo(generation);
	$: looseCell = isLooseCell(generation);

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

	function handleCardClick(event?: MouseEvent) {
		if (longPressFired) {
			longPressFired = false;
			return;
		}
		// Outside selection mode, a shift/ctrl/cmd-click still starts a
		// selection (desktop-file-manager convention) rather than opening the
		// preview; a plain click there falls through to the preview as usual.
		const wantsSelection =
			selectable || !!(event && (event.shiftKey || event.ctrlKey || event.metaKey));
		if (wantsSelection && onSelect && !disabled) {
			onSelect(generation, currentMediaFile ?? null, event);
		} else if (mediaFiles.length > 0 || stackEntry || isLiveTile) {
			dispatch('imageClick', { files: generation.files, generationId: generation.id });
		}
	}

	let actions: EntryActions | undefined;
	let longPressTimer: ReturnType<typeof setTimeout> | null = null;
	let longPressOrigin = { x: 0, y: 0 };
	let longPressFired = false;

	function menuModel() {
		return entryMenu!.model(generation, currentMediaFile ?? null);
	}

	function handleMenuPick(tool: MediaTool, ctx: MediaToolContext, extra?: ToolRunExtra) {
		void entryMenu?.pick(generation, tool, ctx, extra);
	}

	function handleContextMenu(event: MouseEvent) {
		if (!menuEnabled || event.shiftKey) return;
		event.preventDefault();
		event.stopPropagation();
		if (longPressFired) return;
		if (event.clientX === 0 && event.clientY === 0) {
			const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
			actions?.openAt(rect.left + 16, rect.top + 16);
			return;
		}
		actions?.openAt(event.clientX, event.clientY);
	}

	function clearLongPress() {
		if (longPressTimer) clearTimeout(longPressTimer);
		longPressTimer = null;
	}

	function handlePointerDown(event: PointerEvent) {
		longPressFired = false;
		if (!menuEnabled || event.pointerType !== 'touch') return;
		longPressOrigin = { x: event.clientX, y: event.clientY };
		clearLongPress();
		longPressTimer = setTimeout(() => {
			longPressTimer = null;
			longPressFired = true;
			actions?.openSheet();
		}, 500);
	}

	function handlePointerMove(event: PointerEvent) {
		if (!longPressTimer) return;
		if (Math.hypot(event.clientX - longPressOrigin.x, event.clientY - longPressOrigin.y) > 10) {
			clearLongPress();
		}
	}

	function handleFavoriteToggle() {
		historyStore.toggleFavorite(generation.id);
	}

	function handleRatingChange(rating: number) {
		historyStore.setRating(generation.id, rating);
	}

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	$: policyOutcome = generation.status === 'failed' && isContentPolicyCode(generation.error_code);
	$: statusLabel = policyOutcome ? 'Blocked' : generation.status;
	$: statusVariant = policyOutcome ? 'warning' : getStatusVariant(generation.status);
	$: statusDotClass = policyOutcome ? 'bg-warning' : getStatusDotClass(generation.status);

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

<div
	class="group relative"
	class:opacity-50={disabled}
	style={tile ? `width: ${tile.width}px` : undefined}
	data-generation-card
	data-generation-id={generation.id}
	bind:this={rootEl}
	role="presentation"
	on:contextmenu={handleContextMenu}
>
	{#if stackEntry}
		<div
			aria-hidden="true"
			data-testid="grid-stack-layers"
			class="pointer-events-none absolute -top-1 left-1.5 right-1.5 h-2 rounded-t-lg border border-b-0 border-line bg-surface-2"
		></div>
		<div
			aria-hidden="true"
			class="pointer-events-none absolute -top-2 left-3 right-3 h-2 rounded-t-lg border border-b-0 border-line bg-surface-1"
		></div>
	{/if}
	<div
		class="tile-frame relative cursor-pointer rounded-lg overflow-hidden transition-colors duration-100 ease-out border {tile
			? 'bg-black'
			: 'bg-surface-1'} {selected
			? 'tile-selected border-line-hover'
			: 'border-line-strong group-hover:border-line-hover'}"
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
				on:click|stopPropagation={(e) => onSelect?.(generation, currentMediaFile ?? null, e)}
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
			aria-label={label ? `Preview ${label}` : undefined}
			on:click={handleCardClick}
			on:pointerdown={handlePointerDown}
			on:pointermove={handlePointerMove}
			on:pointerup={clearLongPress}
			on:pointercancel={clearLongPress}
			on:keydown={(e) => {
				if (e.key === 'Enter' || e.key === ' ') {
					e.preventDefault();
					handleCardClick();
				}
			}}
		>
			{#if mediaFiles.length > 0 && currentMediaFile}
				{#if isUnratedFile(currentMediaFile)}
					<ContentPolicyTile kind="checking" />
				{:else if isCurrentMediaMesh}
					<!-- Grid tiles stay lightweight: no <model-viewer> here. A
					     server-rendered thumbnail (media-index queue) is preferred
					     once one exists; otherwise fall back to the format marker. -->
					{#if currentMediaFile.thumbnail_small || currentMediaFile.thumbnail_medium || currentMediaFile.thumbnail_large}
						{#key currentMediaFile.file_path}
							<MediaPreview
								file={currentMediaFile}
								generationId={generation.id}
								className="w-full h-full {tile ? 'object-contain' : 'object-cover'}"
								{thumbnailSize}
								loadFullOnClick={false}
							/>
						{/key}
					{:else}
						<div
							class="w-full h-full flex flex-col items-center justify-center gap-2"
							style={placeholderTint(currentMediaFile.file_path)}
						>
							<Icon name="cube" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
							<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{currentMeshFormat}</span>
						</div>
					{/if}
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
							skeleton={!!tile}
						/>
					{/key}
				{/if}
			{:else}
				<div
					class="w-full h-full min-h-[8rem] flex items-center justify-center bg-surface-2"
					style={generation.status === 'completed' ? placeholderTint(generation.id) : undefined}
				>
					{#if liveShowsPicture && liveSeen}
						{#if liveSeen.clip}
							<PreviewClipPlayer clip={liveSeen.clip} progress={liveProgress ?? 0} chip={false} controls={false} ruler={false} />
						{:else if liveSeen.preview}
							<img src={liveSeen.preview} alt="" class="absolute inset-0 h-full w-full object-contain" data-live-tile-preview />
						{/if}
					{:else if generation.status === 'pending' || generation.status === 'running'}
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
					<Badge variant={statusVariant} size="sm" class="uppercase tracking-wide">
						{statusLabel}
					</Badge>
				</div>
			{/if}

			{#if stackEntry || looseCell}
				<div
					data-testid={stackEntry ? 'grid-stack-chip' : 'grid-cell-chip'}
					data-grid-id={gridIdOf(generation)}
					class="absolute top-2 z-30 flex items-center gap-1 rounded bg-black/70 px-1.5 py-0.5 font-mono text-xs tabular-nums tracking-[0.07em] text-fg backdrop-blur-sm {showCheckbox
						? 'left-9'
						: 'left-2'}"
				>
					<Icon name="grid" className="h-2.5 w-2.5" />
					<span>{stackEntry ? stackBadge(generation) : cellChip(generation)}</span>
				</div>
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

			{#if currentMediaFile?.label && !isCurrentMediaVideo && !isCurrentMediaAudio && !isCurrentMediaMesh}
				<div class="absolute top-2 left-1/2 -translate-x-1/2 z-20 max-w-[60%] flex" data-output-label>
					<Tooltip text={currentMediaFile.label} position="bottom" wrapperClass="inline-flex items-center max-w-full min-w-0">
						<Badge size="lg" class="max-w-full font-mono uppercase tracking-[0.07em] bg-black/70 text-white border-white/20">
							<span class="truncate">{currentMediaFile.label}</span>
						</Badge>
					</Tooltip>
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

			{#if tile && showOriginChip}
				<div class="absolute left-1.5 z-30 flex max-w-[calc(100%-12px)] {mediaChipShown ? 'bottom-8' : 'bottom-1.5'}" data-editor-origin-host>
					<GenerationOriginChip {generation} iconOnly={tile.width < 200} />
				</div>
			{/if}

			<!-- Justified tile: duration/fps/size, bottom-right, on hover only (resolution
			     already has its own lane in the bar below, so it never repeats here). -->
			{#if tile && chromeBucket?.showHoverMeta && generation.status === 'completed' && barHoverMetaParts.length > 0}
				<div
					class="absolute bottom-1.5 right-1.5 z-20 max-w-[60%] px-1.5 py-0.5 rounded bg-black/70 backdrop-blur-sm text-fg-muted font-mono tabular-nums text-2xs tracking-[0.07em] truncate opacity-0 group-hover:opacity-100 transition-opacity duration-100"
					use:tooltip={{ text: barHoverMetaParts.join(' · '), truncateOnly: true }}
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
							use:tooltip={{ text: topSystemTags.join(' · '), truncateOnly: true }}
						>
							{topSystemTags.join(' · ')}
						</span>
					{/if}
					<span
						class="font-mono tabular-nums text-xs uppercase tracking-[0.07em] text-white/90 truncate min-w-0 shrink-0"
						use:tooltip={{ text: metaParts.join(' · '), truncateOnly: true }}
					>
						{metaParts.join(' · ')}
					</span>
				</div>
			{/if}

			<!-- File navigation -->
			{#if mediaFiles.length > 1 && density.showCarouselArrows}
				<IconButton icon="chevron-left" label="Previous image" size="xs" variant="overlay" onclick={handlePrevImage} class="absolute left-1.5 top-1/2 -translate-y-1/2 z-30 opacity-0 group-hover:opacity-100" />
				<IconButton icon="chevron-right" label="Next image" size="xs" variant="overlay" onclick={handleNextImage} class="absolute right-1.5 top-1/2 -translate-y-1/2 z-30 opacity-0 group-hover:opacity-100" />
			{/if}
			{#if showCarouselCounter}
				<div
					class="absolute {counterOffsetClass} left-1/2 -translate-x-1/2 z-20 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity duration-100"
				>
					<span class="font-mono tabular-nums">{currentImageIndex + 1}/{mediaFiles.length}</span>
				</div>
			{/if}

			<!-- Tick-ruler progress for running generations with media -->
			{#if generation.status === 'running' && (liveProgress ?? 0) > 0 && (mediaFiles.length > 0 || liveShowsPicture)}
				<div class="absolute bottom-0 left-0 right-0 z-30">
					<div class="tick-track">
						<div
							class="tick-fill bg-signal-solid transition-all duration-300"
							style="width: {livePercent ?? 0}%"
						></div>
					</div>
				</div>
			{/if}
			{#if liveShowsPicture && liveTile}
				<div
					class="pointer-events-none absolute top-2 z-30 inline-flex max-w-[calc(100%-1rem)] items-center gap-1.5 rounded bg-black/70 px-1.5 py-0.5 font-mono text-xs tabular-nums text-white {showCheckbox ? 'left-9' : 'left-2'}"
					data-live-tile-chip
				>
					<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-signal-solid"></span>
					<span class="truncate">{liveWide ? liveLabel : livePercent !== null ? `${livePercent}%` : ''}</span>
				</div>
			{/if}
		</div>

		{#if toolRun}
			<ToolRunBand
				label={toolRun.label}
				progress={toolRun.progress}
				cancelling={toolRun.cancelling}
				bottom={tile && chromeBucket ? chromeBucket.barHeight : 0}
				onCancel={() => toolRun && mediaToolRuns.cancel(toolRun.key)}
			/>
		{/if}

		{#if menuEnabled && chromeBucket}
				<EntryActions
					bind:this={actions}
					scope="history"
					getModel={menuModel}
					onPick={handleMenuPick}
					favorite={showActions && !selectable && !stackEntry ? !!generation.is_favorite : null}
					onFavorite={handleFavoriteToggle}
					chipSize={chromeBucket.menuChipSize}
				/>
		{/if}

		<!-- Justified tile: persistent bottom info bar. Rating (or status, while not
		     completed) sits left; resolution (+ time, full bucket only) sits right.
		     Replaces the floating status badge and the below-frame caption that the
		     fixed-aspect card still uses. -->
		{#if tile && chromeBucket}
			<div
				class="flex items-center justify-between gap-2 px-2 bg-surface-1 border-t border-line overflow-hidden"
				style="height: {chromeBucket.barHeight}px"
			>
				{#if stackText}
					<span data-testid="grid-stack-title" class="min-w-0 truncate font-mono text-xs tracking-[0.07em] text-fg-muted">
						{stackText.title}
					</span>
					<span data-testid="grid-stack-count" class="ml-auto shrink-0 font-mono tabular-nums text-xs tracking-[0.07em] text-fg-subtle">
						{stackText.count}
					</span>
				{:else}
				<div class="flex items-center gap-1 min-w-0 overflow-hidden">
					{#if generation.status === 'completed'}
						{#if chromeBucket.showStars && interactive}
							<StarRating
								value={generation.rating}
								tone="default"
								readonly={!showActions}
								onChange={handleRatingChange}
							/>
						{:else if chromeBucket.showRatingChip || !interactive}
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
						<Badge variant={statusVariant} size="sm" class="uppercase tracking-wide">
							{statusLabel}
						</Badge>
					{:else}
						<span
							class="w-1.5 h-1.5 rounded-full shrink-0 {statusDotClass}"
							use:tooltip={generation.status}
						></span>
					{/if}
				</div>
				<div class="flex items-center gap-1.5 ml-auto shrink-0 font-mono tabular-nums text-2xs tracking-[0.07em]">
					{#if isLiveTile && generation.status === 'running'}
						<LiveElapsed {generation} class="text-fg-muted" />
					{/if}
					{#if barResolutionText && !durationReplacesResolution}
						<span class="text-fg-muted whitespace-nowrap" use:tooltip={fullResolutionTitle}>{barResolutionText}</span>
					{/if}
					{#if barRunLabel}
						{#if barResolutionText && !durationReplacesResolution}<span class="text-line-hover">·</span>{/if}
						<Tooltip text="Generation time" position="top" wrapperClass="inline-flex items-center">
							<span class="inline-flex items-center gap-0.5 text-xs text-fg-muted whitespace-nowrap" data-testid="card-run-time">
								{#if chromeBucket.durationIcon}<Icon name="clock" className="w-2.5 h-2.5" />{/if}{barRunLabel}
							</span>
						</Tooltip>
					{/if}
					{#if chromeBucket.showBarTime}
						<span class="text-line-hover">·</span>
						<span class="text-fg-subtle whitespace-nowrap">{timeAgo(generation.created_at)}</span>
					{/if}
				</div>
				{/if}
			</div>
		{/if}

		{#if !tile && showOriginChip}
			<div class="flex items-center border-t border-line bg-surface-1 px-2 py-1.5" data-editor-origin-host>
				<GenerationOriginChip {generation} />
			</div>
		{/if}

		<!-- Error Message Footer -->
		{#if generation.error_user_message || generation.error_message}
			<div class="px-2 py-2 bg-surface-1 space-y-1.5">
				{#if policyOutcome}
					<FailureNotice
						message={generation.error_message || generation.error_user_message || ''}
						hint={firstHintLine(generation.error_user_message, generation.error_message)}
						errorId={policyShowsErrorId(generation.error_code) ? generation.error_id : null}
						contactAdmin={!!generation.error_contact_admin}
					/>
				{:else}
					<div class="p-1.5 bg-danger/10 border border-danger/25 rounded text-sm text-danger whitespace-pre-line">
						{generation.error_user_message || generation.error_message}
					</div>
					{#if generation.error_id}
						<div class="flex items-center gap-1.5">
							<span class="text-sm text-fg-subtle whitespace-nowrap">Error ID</span>
							<span class="font-mono text-sm tabular-nums text-fg truncate">{generation.error_id}</span>
							<CopyButton text={generation.error_id} ariaLabel="Copy error ID" size="xs" />
						</div>
						{#if generation.error_contact_admin}
							<p class="text-sm text-fg-subtle">Give this to your admin</p>
						{/if}
					{/if}
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
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
