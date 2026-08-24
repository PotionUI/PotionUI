<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { filesWithPreview, mediaFileThumbnailUrl } from '$lib/utils/modelPreview';
	import { isAvailabilityKnown } from '$lib/utils/modelAvailability';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import {
		modelFilenameStem,
		modelSummaryParts,
		modelTypePresentation
	} from '$lib/utils/modelPresentation';
	import Icon from './Icon.svelte';
	import FavoriteButton from './FavoriteButton.svelte';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	export let model: any;
	/** Show operational data (file size, backend availability).
	 *
	 * Gated on the *context*, not on the viewer's role: an admin browsing the model
	 * library or picking a model to generate with is in a user context and should see a
	 * user's view. Only the admin Models tab opts in. */
	export let showTechnical: boolean = false;
	/** Multi-select mode: clicking the media area selects instead of opening details. */
	export let selectable: boolean = false;
	export let selected: boolean = false;
	/** Always-available selection checkbox (independent of selection mode), mirrors GenerationCard. */
	export let showCheckbox: boolean = false;
	export let onSelect: ((model: any) => void) | null = null;
	/** From the `/api/models` list response's top-level `availability_indexed` - whether
	 * ANY backend has ever been indexed. `model.backend_ids` is only meaningful once this
	 * is true; an empty array before that means "unknown", not "unavailable". */
	export let availabilityIndexed: boolean = false;
	/** backend_id -> display name, for the availability badge tooltip. */
	export let backendNames: Record<string, string> = {};
	/** Admin context (Admin -> Models): the icon row is remove-only. */
	export let showManagementActions: boolean = false;
	/** No direct or group assignment exists - only admins can currently see this model. */
	export let unassigned: boolean = false;

	const dispatch = createEventDispatcher();

	let currentMediaIndex = 0;

	// Media counter shifts right to clear the checkbox badge, exactly like GenerationCard.
	$: counterOffsetClass = showCheckbox ? 'left-9' : 'left-2';

	// Favorite heart is optimistic and local to the card - the parent's grid
	// data isn't refetched just to reflect a toggle.
	let isFavorite = !!model.is_favorite;
	$: isFavorite = !!model.is_favorite;

	async function toggleFavorite() {
		const next = !isFavorite;
		isFavorite = next;
		try {
			const response = await api.setModelFavorite(model.id, next);
			if (!response.success) throw new Error('Favorite update failed');
		} catch (error) {
			isFavorite = !next;
			logger.error('Failed to toggle model favorite:', getErrorMessage(error));
		}
	}

	const formatFileSize = (bytes?: number) => {
		if (!bytes) return 'Unknown';
		const gb = bytes / (1024 * 1024 * 1024);
		const mb = bytes / (1024 * 1024);

		if (gb >= 1) {
			return `${gb.toFixed(2)} GB`;
		} else {
			return `${mb.toFixed(1)} MB`;
		}
	};

	// Include images, thumbnails, and videos (for their thumbnails). An admin-set
	// preview is folded in ahead of provider files.
	const mediaFiles = filesWithPreview(model).filter((f: any) => f.file_type === 'image' || f.file_type === 'thumbnail' || f.file_type === 'video');

	$: currentMedia = mediaFiles[currentMediaIndex];
	// File size is operational. It appears only where the context asks for it, so an
	// admin browsing the library sees the same card a user does.
	$: displayName = modelDisplayName(model);
	$: filenameStem = modelFilenameStem(model);
	$: typePresentation = modelTypePresentation(model.model_type);
	$: summaryParts = modelSummaryParts(model);

	// See docs/models.md "Indexing is per backend": an empty `backend_ids` only means
	// "unavailable" once indexing has happened at all - otherwise it means "unknown".
	$: backendIds = (model.backend_ids || []) as string[];
	$: backendNamesList = backendIds.map((id) => backendNames[id] || id);
	$: hasKnownAvailability = isAvailabilityKnown(backendIds, availabilityIndexed);

	function handleViewClick() {
		dispatch('view', model);
	}

	function handleCardClick() {
		if (selectable && onSelect) {
			onSelect(model);
		} else {
			handleViewClick();
		}
	}

	function handleDeleteClick(event: Event) {
		event.stopPropagation();
		event.preventDefault();
		dispatch('delete', model);
	}

	function goToPreviousMedia(event: Event) {
		event.stopPropagation();
		event.preventDefault();
		if (currentMediaIndex > 0) {
			currentMediaIndex--;
		}
	}

	function goToNextMedia(event: Event) {
		event.stopPropagation();
		event.preventDefault();
		if (currentMediaIndex < mediaFiles.length - 1) {
			currentMediaIndex++;
		}
	}

</script>

<div class="group">
	<div
		class="relative rounded-lg overflow-hidden bg-surface-1 border transition-colors duration-100 {selected
			? 'border-line-hover'
			: 'border-line-strong hover:border-line-hover'}"
	>
		<!-- Selection wash when selected -->
		{#if selected}
			<div class="absolute inset-0 bg-signal/15 z-40 pointer-events-none"></div>
		{/if}

		<!-- Always-available selection checkbox (top-left) -->
		{#if showCheckbox}
			<button
				class="absolute top-2 left-2 z-40 w-5 h-5 rounded flex items-center justify-center transition-colors duration-100 {selected
					? 'bg-accent text-accent-contrast'
					: 'bg-black/45 backdrop-blur-sm ring-1 ring-white/60 text-transparent hover:bg-black/70'}"
				on:click|stopPropagation={() => onSelect?.(model)}
				aria-label={selected ? 'Deselect model' : 'Select model'}
				aria-pressed={selected}
			>
				{#if selected}
					<Icon name="check" className="w-3.5 h-3.5" strokeWidth={3} />
				{/if}
			</button>
		{/if}

		<!-- Image Container with Fixed Aspect Ratio -->
		<div
			class="media-zoom relative aspect-[3/4] overflow-hidden bg-surface-2 cursor-pointer"
			on:click={handleCardClick}
			on:keydown={(e) => e.key === 'Enter' && handleCardClick()}
			role="button"
			tabindex="0"
		>
			{#if currentMedia}
				<img
					src={mediaFileThumbnailUrl(currentMedia)}
					alt={displayName}
					class="w-full h-full object-cover"
					loading="lazy"
					decoding="async"
				/>

				<!-- Navigation arrows for multiple files -->
				{#if mediaFiles.length > 1}
					{#if currentMediaIndex > 0}
						<button
							class="absolute left-1.5 top-1/2 -translate-y-1/2 z-40 bg-black/60 hover:bg-black/80 text-white p-1.5 rounded opacity-0 group-hover:opacity-100 transition-opacity duration-100"
							on:click={goToPreviousMedia}
							aria-label="Previous media"
						>
							<Icon name="chevron-left" className="w-3.5 h-3.5" />
						</button>
					{/if}

					{#if currentMediaIndex < mediaFiles.length - 1}
						<button
							class="absolute right-1.5 top-1/2 -translate-y-1/2 z-40 bg-black/60 hover:bg-black/80 text-white p-1.5 rounded opacity-0 group-hover:opacity-100 transition-opacity duration-100"
							on:click={goToNextMedia}
							aria-label="Next media"
						>
							<Icon name="chevron-right" className="w-3.5 h-3.5" />
						</button>
					{/if}

					<!-- Media counter (offset right of the checkbox/favorite badges) -->
					<div
						class="absolute top-2 {counterOffsetClass} z-30 bg-black/70 backdrop-blur-sm text-white rounded px-1.5 py-0.5 font-mono tabular-nums text-2xs"
					>
						{currentMediaIndex + 1}/{mediaFiles.length}
					</div>
				{/if}
			{:else}
				<div class="w-full h-full flex items-center justify-center" style={placeholderTint(displayName)}>
					<div class="flex flex-col items-center gap-2">
						<Icon name="model" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
						<span class="text-xs font-medium text-fg-subtle">{typePresentation.label}</span>
						<span class="max-w-[85%] text-center text-2xs text-fg-disabled">{typePresentation.purpose}</span>
					</div>
				</div>
			{/if}


			<!-- Actions - top right on hover (favorite persists when set). Hidden while selecting,
			     exactly like GenerationCard's `{#if showActions && !selectable}`. Removing a model
			     belongs to the admin management context (showManagementActions); the library gets
			     favorite/view only. -->
			{#if !selectable}
				<div class="absolute top-2 right-2 z-40 flex items-center gap-1">
					{#if showManagementActions}
						<button
							class="bg-black/60 hover:bg-danger-solid text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100"
							on:click={handleDeleteClick}
							aria-label="Remove model"
						>
							<Icon name="trash" className="h-3.5 w-3.5" />
						</button>
					{:else}
						<div
							class="bg-black/60 hover:bg-black/80 rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 flex items-center {isFavorite
								? 'opacity-100'
								: 'opacity-0 group-hover:opacity-100'}"
						>
							<FavoriteButton active={isFavorite} tone="onMedia" onToggle={toggleFavorite} />
						</div>
						<button
							class="bg-black/60 hover:bg-black/80 text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100"
							on:click={handleViewClick}
							aria-label="View model details"
						>
							<Icon name="eyes" className="h-3.5 w-3.5" />
						</button>
					{/if}
				</div>
			{/if}

			<!-- Name + type/size overlay - Bottom (always visible) -->
			<div
				class="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/85 via-black/55 to-transparent pt-10 pb-2 px-2.5 z-20"
			>
				<h4 class="text-sm font-semibold text-white truncate" title={displayName}>
					{displayName}
				</h4>
				{#if filenameStem}
					<p class="truncate font-mono text-2xs text-white/65" title={model.filename}>
						File · {filenameStem}
					</p>
				{/if}
				<div class="mt-1 flex min-w-0 items-center gap-1 overflow-hidden">
					{#if unassigned}
						<span
							class="flex-shrink-0 rounded bg-warning/25 px-1.5 py-0.5 text-2xs font-medium text-warning backdrop-blur-sm"
							title="Only admins can see this — assign users or groups"
						>Unassigned</span>
					{/if}
					{#each summaryParts as part, index}
						<span
							class="max-w-[7rem] flex-shrink truncate rounded bg-black/35 px-1.5 py-0.5 text-2xs text-white/80 backdrop-blur-sm"
							title={index === 0 ? typePresentation.purpose : part}
						>{part}</span>
					{/each}
					{#if showTechnical && model.file_size}
						<span class="flex-shrink-0 rounded bg-black/35 px-1.5 py-0.5 font-mono text-2xs text-white/65">
							{formatFileSize(model.file_size)}
						</span>
					{/if}
				</div>
				{#if showTechnical && hasKnownAvailability}
					{#if backendIds.length > 0}
						<span
							class="inline-flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded text-2xs font-mono text-white/80 bg-white/10"
							title={`Available on: ${backendNamesList.join(', ')}`}
						>
							<Icon name="database" className="w-3 h-3" />
							{backendIds.length} backend{backendIds.length === 1 ? '' : 's'}
						</span>
					{:else}
						<span class="inline-flex items-center gap-1 mt-1 text-2xs font-mono text-white/50" title="No indexed backend can currently load this model">
							No backend
						</span>
					{/if}
				{/if}
			</div>
		</div>
	</div>
</div>

<style>
	/* Hover zoom — scale the media inside the fixed, clipped frame so the tile
	   itself stays put and the image gently pushes toward the viewer. */
	.media-zoom :global(img) {
		transition: transform 450ms cubic-bezier(0.22, 1, 0.36, 1);
		will-change: transform;
	}
	.group:hover .media-zoom :global(img) {
		transform: scale(1.06);
	}
	@media (prefers-reduced-motion: reduce) {
		.media-zoom :global(img) {
			transition: none;
		}
		.group:hover .media-zoom :global(img) {
			transform: none;
		}
	}
</style>
