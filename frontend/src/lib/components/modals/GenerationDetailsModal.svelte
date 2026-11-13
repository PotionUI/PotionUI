<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { logger } from '$lib/utils/logger';
	import { parseServerDate } from '$lib/utils/relativeTime';
	import { formatBytes } from '$lib/utils/format';
	import { tagChips, saveGenerationTags } from '$lib/workbench/generationTags';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { createEventDispatcher, onDestroy, onMount } from 'svelte';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import { resolveReusePresetLabel } from '$lib/utils/historyReuse';
	import { formatHumanDuration, formatRunLine, generationQueueLabel } from '$lib/utils/generationTime';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { api } from '$lib/services/api/index';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import portal from '$lib/actions/portal';
	import { plainLetter, windowOwnsKeys } from '$lib/utils/shortcutGuard';
	import { canReturnToDetails, rememberDetailsScroll, takeDetailsScroll } from '$lib/tools/detailsReturn';
	import { restoreScroll } from '$lib/utils/restoreScroll';
	import BaseModal from './BaseModal.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MeshPreview from '$lib/components/workbench/renderers/MeshPreview.svelte';
	import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';
	import AudioPlayer from '$lib/components/AudioPlayer.svelte';
	import type { AudioTrack, AudioTrackType } from '$lib/types/audio';
	import StarRating from '$lib/components/StarRating.svelte';
	import { historyStore } from '$lib/stores/history';
	import { requestAddToKit } from '$lib/stores/kitAddRequests';
	import { libraryStore } from '$lib/stores/library';
	import { summarizeCopyOutcome } from '$lib/library/copyToLibrary';
	import { toasts } from '$lib/stores/toast';
	import { nsfwFilterStore, visibleMediaFiles, shouldBlurFile, isUnratedFile } from '$lib/stores/nsfwFilter';
	import { nsfwRevealStore, revealKey } from '$lib/stores/nsfwReveal';
	import { pickActiveGeneration, needsDetailFetch } from '$lib/utils/generationDetail';
	import { Badge, Button, Spinner, IconButton, Switch } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LoopToggle from '$lib/components/media/LoopToggle.svelte';
	import { loopVideos } from '$lib/stores/loopVideos';
	import PublishToInspirationsModal from './PublishToInspirationsModal.svelte';
	import ProvenanceLine from '$lib/components/organize/ProvenanceLine.svelte';
	import { startRuleFrom } from '$lib/organize/handoff';
	import { generationItemRule } from '$lib/organize/itemPrefill';
	import DetailsActionBar, { type BarAction } from '$lib/media-tools/DetailsActionBar.svelte';
	import { buildHistoryEntryContext } from '$lib/tools/entryMenu';
	import { filesWithPreview, mediaFileThumbnailUrl } from '$lib/utils/modelPreview';
	import { showAlphaCheckerboard } from '$lib/utils/imageAlpha';
	import type { DetailsExtraSection, DetailsFileChange } from './generationDetailsExtras';
	import { withAxisParams } from '$lib/generation/compare/view/gridModel';
	import ParametersView from '$lib/components/generation/ParametersView.svelte';
	import ParameterTextBlock from '$lib/components/generation/ParameterTextBlock.svelte';
	import DetailsSection from '$lib/components/generation/DetailsSection.svelte';
	import DetailRow from '$lib/components/generation/DetailRow.svelte';
	import GenerationFailureAlert from '$lib/components/generation/GenerationFailureAlert.svelte';
	import { failureNotice } from '$lib/components/generation/failureNotice';
	import { authStore } from '$lib/stores/auth';
	import { middleEllipsis } from '$lib/components/generation/idDisplay';
	import FoldButton from '$lib/components/generation/FoldButton.svelte';
	import FoldedList from '$lib/components/generation/FoldedList.svelte';
	import { detailsPrefs, loadDetailsPrefs, setDetailsPref } from '$lib/components/generation/detailsPrefs';
	import type { GenerationParameterMeta } from '$lib/types/generation';

	// Support both ways of passing generation data
	export let generation: GenerationHistoryItem | null = null;
	export let generationId: string | undefined = undefined;
	export let isOpen: boolean = false;
	/** Which file to open on. Negative means the generation's last file. */
	export let initialFileIndex: number = 0;

	/** Unset = no list to walk, no navigation rendered. */
	export let onNavigate: ((direction: -1 | 1) => Promise<boolean> | boolean) | undefined =
		undefined;
	export let hasPrevious: boolean = false;
	export let hasNext: boolean = false;
	export let position: { index: number; total: number } | null = null;
	export let onClose: (() => void) | undefined = undefined;
	export let extraSections: DetailsExtraSection[] = [];
	export let onFileChange: ((change: DetailsFileChange) => void) | undefined = undefined;
	export let title: string = 'Generation Details';
	export let headerIconName: string = 'image';
	export let onArrowKey:
		| ((key: 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown', shift: boolean) => boolean)
		| undefined = undefined;
	export let axisTags: Record<string, string> = {};
	export let axisValues: Record<string, string> = {};
	export let axisParamMeta: Record<string, GenerationParameterMeta> = {};

	const dispatch = createEventDispatcher();

	let presetNamesCache: Record<string, string> = {};
	onMount(() => {
		loadPresetNameMap()
			.then((map) => (presetNamesCache = map))
			.catch((err) => logger.error('Failed to load preset names:', err));
	});
	let detailImg: HTMLImageElement | undefined;
	let trackedImg: HTMLImageElement | undefined;
	let loadedStageSrc: string | undefined;
	$: stageImageSrc =
		currentFile && !isVideoFile(currentFile) && !isMeshFile(currentFile) && !isAudioFile(currentFile)
			? getImageUrl(currentFile)
			: null;
	$: if (detailImg !== trackedImg) {
		trackedImg = detailImg;
		loadedStageSrc = undefined;
	}
	$: if (detailImg && stageImageSrc && loadedStageSrc !== stageImageSrc && detailImg.complete && detailImg.naturalWidth > 0) {
		loadedStageSrc = stageImageSrc;
	}
	$: stageImageLoading = !!stageImageSrc && loadedStageSrc !== stageImageSrc;

	$: presetLabel = activeGeneration
		? resolveReusePresetLabel(activeGeneration, (id) => presetNamesCache[id] ?? null)
		: '';

	$: canNavigate = typeof onNavigate === 'function';
	let navigating = false;

	async function navigate(direction: -1 | 1) {
		if (!onNavigate || navigating) return;
		if (direction === -1 ? !hasPrevious : !hasNext) return;
		navigating = true;
		try {
			await onNavigate(direction);
		} finally {
			navigating = false;
		}
	}

	// Generation data (will be loaded if only ID is provided)
	let loadedGeneration: GenerationHistoryItem | null = null;
	let currentFileIndex = initialFileIndex;
	let loading = true;
	// Id whose detail fetch has been kicked off, so the reactive fetch below
	// doesn't re-fire while the request is in flight.
	let requestedDetailId: string | undefined = undefined;

	// Prefer the loaded detail (it carries `segments`) over the list item.
	$: activeGenerationId = generation?.id || generationId || '';
	$: activeGeneration = pickActiveGeneration(generation, loadedGeneration, activeGenerationId);
	$: shortGenerationId = activeGenerationId ? activeGenerationId.slice(0, 8) : '';
	$: if (!activeGenerationId) {
		logger.error('GenerationDetailsModal: No generation or generationId provided');
	}

	// Track if tags were initialized to prevent overwriting user changes
	let tagsInitialized = false;

	// A generation handed to us by the parent is already renderable, so the spinner ends
	// here. It used to end inside the tags block below, which meant any caller passing a
	// generation without a `tags` array left the modal loading forever.
	$: if (generation) {
		loading = false;
	}

	// Initialize tags from generation if provided (only once per generation)
	$: if (generation?.tags && !tagsInitialized) {
		selectedTags = tagChips(generation.tags);
		selectedTagIds = selectedTags.map(tag => tag.id);
		tagsInitialized = true;
	}

	// Reset tags initialization flag when generation changes
	$: if (generation?.id) {
		if (previousGenerationId && previousGenerationId !== generation.id) {
			tagsInitialized = false;
			selectedTags = [];
			selectedTagIds = [];
		}
		previousGenerationId = generation.id;
	}

	let previousGenerationId: string | undefined = undefined;

	// Parameters and models for current image
	let currentParams: Record<string, any> = {};
	$: shownParams = withAxisParams(currentParams, axisTags, axisValues);
	let currentModels: any[] = [];
	let currentParamMeta: Record<string, GenerationParameterMeta> | null = null;
	let currentParamOrder: string[] | null = null;
	$: shownParamMeta = mergeParamMeta(axisParamMeta, currentParamMeta);
	let paramsLoading = false;
	let loadedParamsKey = '';

	// Tags
	let selectedTagIds: string[] = [];
	let selectedTags: Array<{ id: string; name: string }> = [];
	let isSavingTags: boolean = false;

	$: failure = activeGeneration ? failureNotice(activeGeneration, $authStore.user?.account_type === 'ADMIN') : null;
	$: runLine = activeGeneration ? formatRunLine(activeGeneration) : null;
	$: runTime =
		activeGeneration && activeGeneration.status === 'completed' && activeGeneration.completed_at
			? formatHumanDuration(activeGeneration.run_seconds)
			: null;
	$: queuedTime =
		activeGeneration && generationQueueLabel(activeGeneration)
			? formatHumanDuration(activeGeneration.queued_seconds)
			: null;
	$: timesOpen = $detailsPrefs['info:times'] ?? false;
	// Structured prompt segments recorded with this generation (may be absent on
	// old rows). Split by channel for display.
	$: segments = activeGeneration?.segments ?? [];
	$: positiveSegments = segments
		.filter((s) => s.channel === 'positive')
		.sort((a, b) => a.prompt_index - b.prompt_index || a.segment_index - b.segment_index);
	$: negativeSegments = segments
		.filter((s) => s.channel === 'negative')
		.sort((a, b) => a.prompt_index - b.prompt_index || a.segment_index - b.segment_index);

	// The Video Director wire document, stored verbatim on form_data.video_director
	// (src/features/video_director/normalize.py's contract) -- a generic prompt
	// `segments` (above) is empty for a Director generation, since its shots never
	// go through the segmented-prompt path.
	$: videoDirectorDoc = (activeGeneration?.form_data?.video_director ?? null) as {
		mode?: string;
		segments?: Array<{ id: string; prompt?: string; negative_prompt?: string; sub_type?: string }>;
		media?: Array<{ role?: string }>;
		audio?: Array<unknown>;
	} | null;
	$: directorSegments = videoDirectorDoc?.segments ?? [];
	$: directorKeyframeCount = (videoDirectorDoc?.media ?? []).filter((m) => m.role === 'keyframe').length;
	$: directorAudioCount = videoDirectorDoc?.audio?.length ?? 0;

	loadDetailsPrefs();

	// Reactive computed values; hide mode drops nsfw files from navigation.
	$: mediaFiles = visibleMediaFiles(activeGeneration?.files || [], $nsfwFilterStore.mode).filter(
		(file) => !isUnratedFile(file)
	);
	// Re-derived on every generation swap; reading `mediaFiles` orders this after it.
	let renderedGenerationId = '';
	$: if (activeGenerationId !== renderedGenerationId) {
		renderedGenerationId = activeGenerationId;
		currentFileIndex =
			initialFileIndex < 0 ? Math.max(0, mediaFiles.length - 1) : initialFileIndex;
		showPublishModal = false;
	}

	$: currentFile = mediaFiles[currentFileIndex];
	$: fileChangeKey = currentFile ? `${activeGenerationId}:${currentFileIndex}:${currentFile.id ?? ''}` : '';
	let reportedFileKey = '';
	$: if (fileChangeKey && fileChangeKey !== reportedFileKey) {
		reportedFileKey = fileChangeKey;
		onFileChange?.({
			generationId: activeGenerationId,
			fileIndex: currentFileIndex,
			fileId: typeof currentFile?.id === 'number' ? currentFile.id : null
		});
	}
	$: canGoPrev = currentFileIndex > 0;
	$: canGoNext = currentFileIndex < mediaFiles.length - 1;
	$: currentFileAlphaCheckerboard = currentFile ? showAlphaCheckerboard(currentFile) : false;

	// Auto-tagger output for the file on screen.
	$: systemTags = currentFile?.system_tags ?? [];

	// NSFW blur for the detail pane; reveal state is shared per file via nsfwRevealStore.
	nsfwFilterStore.init();
	$: currentRevealKey = currentFile ? revealKey(activeGenerationId, currentFile) : '';
	$: detailBlur =
		!!currentFile &&
		shouldBlurFile(currentFile, $nsfwFilterStore.mode, $nsfwRevealStore.has(currentRevealKey));

	function handleSystemTagClick(tag: string) {
		historyStore.setSystemTagFilter(tag);
		historyStore.loadGenerations();
		handleClose();
	}

	// The history list endpoint never carries `segments`; the detail one does. Fetch
	// whenever what we're holding lacks them, not merely when we were handed a bare id.
	$: if (needsDetailFetch(isOpen, activeGenerationId, activeGeneration, requestedDetailId)) {
		tagsInitialized = false; // Reset flag when loading new generation
		loadGenerationData();
	}

	// Keyed on what the request actually depends on. Svelte marks an object prop
	// dirty on every assignment even when the reference is unchanged, so a parent
	// re-render - history polls every 6s - would otherwise refetch on a loop and
	// blink this pane. Keying also picks up a file change, which reading
	// currentFileIndex inside the function did not.
	$: paramsKey =
		activeGeneration?.files?.length ? `${activeGenerationId}:${currentFileIndex}` : '';
	$: if (paramsKey && paramsKey !== loadedParamsKey) {
		loadedParamsKey = paramsKey;
		loadParametersForCurrentFile();
	}


	async function loadGenerationData() {
		if (!activeGenerationId) return;

		// Claim the id before awaiting, or the reactive statement re-fires.
		requestedDetailId = activeGenerationId;
		// Only block on the spinner when we have nothing to paint yet. When the
		// parent handed us a list item, the Segments card just appears on resolve —
		// the same way the per-file Parameters card already behaves.
		if (!generation) loading = true;
		try {
			const response = await api.getGenerationById(activeGenerationId, true);
			if (response.success && response.data) {
				loadedGeneration = response.data;
				selectedTags = tagChips(response.data.tags);
				selectedTagIds = selectedTags.map(tag => tag.id);
				tagsInitialized = true;
			}
		} catch (error) {
			logger.error('Failed to load generation:', error);
		} finally {
			loading = false;
		}
	}

	function mergeParamMeta(
		axis: Record<string, GenerationParameterMeta>,
		recorded: Record<string, GenerationParameterMeta> | null
	): Record<string, GenerationParameterMeta> | null {
		const fields = Object.keys(axis);
		if (fields.length === 0) return recorded;
		const merged: Record<string, GenerationParameterMeta> = { ...recorded };
		for (const field of fields) merged[field] = { ...axis[field], ...recorded?.[field] };
		return merged;
	}

	async function loadParametersForCurrentFile() {
		if (!activeGenerationId) return;

		paramsLoading = true;
		try {
			const response = await api.getGenerationParams(activeGenerationId, currentFileIndex);
			if (response.success && response.data) {
				currentParams = response.data.parameters || {};
				currentModels = response.data.models || [];
				currentParamMeta = response.data.parameter_meta ?? null;
				currentParamOrder = response.data.parameter_order ?? null;
			}
		} catch (error) {
			logger.error('Failed to load parameters:', error);
			currentParams = {};
			currentModels = [];
			currentParamMeta = null;
			currentParamOrder = null;
		} finally {
			paramsLoading = false;
		}
	}

	async function handleTagsChange(event: CustomEvent<string[]>) {
		await applyTagIds(event.detail);
	}

	async function applyTagIds(newTagIds: string[]) {
		if (!activeGenerationId) return;

		isSavingTags = true;

		try {
			const tags = await saveGenerationTags(activeGenerationId, newTagIds);
			if (tags) {
				selectedTagIds = newTagIds;
				selectedTags = tagChips(tags);
				if (loadedGeneration) {
					loadedGeneration.tags = tags;
				}
			}
		} catch (error) {
			logger.error('Failed to update tags:', error);
		} finally {
			isSavingTags = false;
		}
	}

	function goToPreviousFile() {
		if (canGoPrev) {
			currentFileIndex--;
		}
	}

	function goToNextFile() {
		if (canGoNext) {
			currentFileIndex++;
		}
	}

	function getImageUrl(file: any) {
		const filename = file.file_path.split('/').pop() || file.file_path;
		return `/api/media/generations/${activeGenerationId}/${filename}`;
	}

	function isVideoFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'video';
	}

	function isMeshFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'mesh';
	}

	function isAudioFile(file: any) {
		return file?.file_type?.toLowerCase?.() === 'audio';
	}

	// No format column is persisted server-side for history rows at all (see
	// GenerationFile's track_type/sample_rate/channels comments - format never
	// made that list either) - derived from the filename exactly like
	// mapGenerationFiles' mesh_format fallback, so a container format still
	// shows without one.
	function resolveAudioFormat(file: any): string {
		const basename = typeof file?.file_path === 'string' ? file.file_path.split('/').pop() : null;
		const ext = basename && basename.includes('.') ? basename.split('.').pop() : null;
		return (ext || 'wav').toLowerCase();
	}

	function currentAudioTracks(file: any): AudioTrack[] {
		if (!file) return [];
		const url = getImageUrl(file);
		return [
			{
				type: (file.track_type as AudioTrackType) || 'mixed',
				url,
				originalUrl: url,
				duration: file.duration_seconds,
				sample_rate: file.sample_rate,
				channels: file.channels,
				format: resolveAudioFormat(file)
			}
		];
	}

	function formatDate(dateString?: string) {
		if (!dateString) return 'N/A';
		return parseServerDate(dateString)?.toLocaleString() ?? 'N/A';
	}

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	function getStatusVariant(status: string): BadgeVariant {
		switch (status) {
			case 'completed':
				return 'success';
			case 'failed':
				return 'danger';
			case 'running':
				return 'signal';
			case 'pending':
				return 'warning';
			default:
				return 'neutral';
		}
	}

	// Arrows walk files and roll over into the neighbouring generation; Shift jumps. Escape: BaseModal.
	function handleLaunch() {
		if (!canReturnToDetails()) return;
		rememberDetailsScroll(activeGenerationId, infoPanel?.scrollTop ?? 0);
		handleClose();
	}

	let infoPanel: HTMLElement | undefined;

	let detailsRoot: HTMLElement | undefined;
	let actionBar: DetailsActionBar | undefined;

	function handleKeyDown(event: KeyboardEvent) {
		if (!isOpen) return;
		const letter = plainLetter(event);
		if (letter && windowOwnsKeys(event, detailsRoot) && actionBar?.triggerShortcut(letter)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if ((event.target as HTMLElement | null)?.closest?.('[data-details-action-bar], [data-bar-menu]')) return;
		if (
			onArrowKey &&
			['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) &&
			!isTypingInField(event)
		) {
			const claimed = onArrowKey(
				event.key as 'ArrowLeft' | 'ArrowRight' | 'ArrowUp' | 'ArrowDown',
				event.shiftKey
			);
			if (claimed) {
				event.preventDefault();
				return;
			}
		}
		if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
		if (isTypingInField(event)) return;

		const direction: -1 | 1 = event.key === 'ArrowRight' ? 1 : -1;

		if (event.shiftKey) {
			if (!canNavigate) return;
			event.preventDefault();
			navigate(direction);
			return;
		}

		if (direction === 1 ? canGoNext : canGoPrev) {
			if (direction === 1) goToNextFile();
			else goToPreviousFile();
			return;
		}

		if (!canNavigate) return;
		event.preventDefault();
		navigate(direction);
	}

	function handleClose() {
		dispatch('close');
		onClose?.();
	}

	function handleRatingChange(rating: number) {
		if (!activeGenerationId) return;
		if (loadedGeneration && loadedGeneration.id === activeGenerationId) {
			loadedGeneration = { ...loadedGeneration, rating };
		}
		historyStore.setRating(activeGenerationId, rating);
	}

	function handleFavoriteToggle() {
		if (!activeGeneration || !activeGenerationId) return;
		if (loadedGeneration && loadedGeneration.id === activeGenerationId) {
			loadedGeneration = { ...loadedGeneration, is_favorite: !loadedGeneration.is_favorite };
		}
		historyStore.toggleFavorite(activeGenerationId);
	}

	// The library holds images, video and audio - a mesh has nowhere to go there.
	let copyingToLibrary = false;
	$: canCopyToLibrary =
		!!currentFile && ['image', 'video', 'audio'].includes(currentFile.file_type?.toLowerCase?.());

	// Copy, not move: this generation and its files stay exactly as they are.
	async function handleCopyToLibrary() {
		if (!currentFile || copyingToLibrary) return;
		copyingToLibrary = true;
		try {
			const { copied, failed } = await libraryStore.copyFromGenerations([{ files: [currentFile] }]);
			const message = summarizeCopyOutcome(copied, failed);
			if (copied > 0) toasts.success(message);
			else toasts.error(message);
		} finally {
			copyingToLibrary = false;
		}
	}

	let showPublishModal = false;

	$: rowContext =
		activeGeneration && currentFile ? buildHistoryEntryContext(activeGeneration, currentFile, null) : null;
	$: rowItems = rowContext?.items ?? [];

	let exportingBundle = false;

	async function handleExportBundle() {
		if (!activeGenerationId || exportingBundle) return;
		exportingBundle = true;
		try {
			await api.exportGenerationBundle(activeGenerationId);
		} catch (e) {
			logger.error('Export bundle failed:', e);
			toasts.error('Export failed. Please try again.');
		} finally {
			exportingBundle = false;
		}
	}

	function downloadCurrentFile() {
		if (!currentFile) return;
		const link = document.createElement('a');
		link.href = getImageUrl(currentFile);
		const ext = isVideoFile(currentFile)
			? 'mp4'
			: isMeshFile(currentFile)
				? resolveMeshFormat(currentFile)
				: isAudioFile(currentFile)
					? resolveAudioFormat(currentFile)
					: 'png';
		link.download = `generation-${activeGenerationId}-${currentFileIndex + 1}.${ext}`;
		link.click();
	}

	function buildGenerationActions(withReuse: boolean, withCopy: boolean, copying: boolean): BarAction[] {
		const actions: BarAction[] = [];
		if (withReuse) {
			actions.push({
				id: 'reuse',
				label: 'Reuse settings',
				icon: 'refresh',
				tooltip: "Reuse this generation's settings",
				onclick: () => dispatch('reuse', activeGeneration)
			});
		}
		if (withCopy) {
			actions.push({
				id: 'copy-to-library',
				label: 'Copy to Library',
				icon: 'photo',
				tooltip: 'Copy this file to your library (the generation stays in history)',
				loading: copying,
				onclick: handleCopyToLibrary
			});
			actions.push({
				id: 'add-to-kit',
				label: 'Add to kit',
				icon: 'layers',
				tooltip: 'Add this file to a kit',
				onclick: () => currentFile && requestAddToKit([currentFile])
			});
		}
		return actions;
	}

	$: generationActions = buildGenerationActions(
		!!activeGeneration?.preset_id,
		canCopyToLibrary,
		copyingToLibrary
	);

	$: downloadActions = [
		{
			id: 'download-file',
			label: 'Download file',
			icon: 'download',
			shortcut: 'D',
			tooltip: 'Download this file',
			description: currentFile
				? [currentFile.file_path.split('/').pop(), currentFile.file_size ? formatBytes(currentFile.file_size) : '']
						.filter(Boolean)
						.join(' · ')
				: undefined,
			onclick: downloadCurrentFile
		},
		{
			id: 'export-bundle',
			label: 'Export bundle',
			icon: 'box',
			description: 'Files and settings in one bundle',
			tooltip: 'Export this generation as a portable bundle',
			loading: exportingBundle,
			onclick: handleExportBundle
		}
	] satisfies BarAction[];

	$: moreActions = [
		{
			id: 'publish',
			label: 'Publish to Inspirations',
			icon: 'lightbulb',
			onclick: () => (showPublishModal = true)
		},
		{ id: 'open-new-tab', label: 'Open in new tab', icon: 'external-link', onclick: openCurrentFileInTab }
	] satisfies BarAction[];

	$: pluginActionContext =
		activeGeneration && currentFile
			? {
					generationId: activeGenerationId,
					fileIndex: currentFileIndex,
					filename: currentFile.file_path.split('/').pop() || currentFile.file_path,
					fileUrl: getImageUrl(currentFile),
					fileType: currentFile.file_type
				}
			: null;

	function handleBarTagsChange(ids: string[]) {
		void applyTagIds(ids);
	}

	function openCurrentFileInTab() {
		if (!currentFile) return;
		window.open(getImageUrl(currentFile), '_blank');
	}

	function openModelInTab(model: any) {
		if (!model?.id) return;
		window.open(`/models/${model.id}`, '_blank');
	}

	// Same precedence as ModelCard: an admin-set preview_media (folded in by
	// filesWithPreview) wins over the provider-supplied thumbnail file.
	function modelThumbnailUrl(model: any): string | null {
		const file = filesWithPreview(model).find(
			(f: any) => f.file_type === 'image' || f.file_type === 'thumbnail' || f.file_type === 'video'
		);
		return file ? mediaFileThumbnailUrl(file) || null : null;
	}
</script>

<svelte:window on:keydown|capture={handleKeyDown} />

<!-- Callers may mount this from inside a slide-out drawer or another modal;
     an ancestor with a `transform` (the drawer's slide animation) becomes the
     containing block for `position: fixed`, so the dialog must portal to
     <body> to stay viewport-sized regardless of where it's mounted from. -->
<div use:portal>
<BaseModal
	{isOpen}
	{title}
	sizeClass="md:w-[85vw] md:h-[85vh]"
	on:close={handleClose}
>
	<svelte:fragment slot="headerIcon">
		<div class="w-[30px] h-[30px] rounded bg-surface-2 border border-line flex items-center justify-center flex-shrink-0">
			<Icon name={headerIconName} className="w-4 h-4 text-fg-muted" />
		</div>
	</svelte:fragment>
	<svelte:fragment slot="header">
		{#if activeGeneration}
			<code class="font-mono text-2xs text-fg-disabled flex-shrink-0 hidden sm:inline">{shortGenerationId}</code>
		{/if}
		<div class="flex-1"></div>
		{#if activeGeneration}
			<Badge variant={getStatusVariant(activeGeneration.status)} dot class="flex-shrink-0 uppercase tracking-wide">
				{activeGeneration.status}
			</Badge>
		{/if}
		{#if activeGeneration && currentFile}
			<div class="flex-shrink-0">
				<PluginSlot
					hookName="generation.detail.actions"
					tooltips
					context={{
						generationId: activeGenerationId,
						presetId: activeGeneration.preset_id ?? null,
						mode: activeGeneration.mode,
						fileIndex: currentFileIndex,
						filename: currentFile.file_path.split('/').pop() || currentFile.file_path,
						fileUrl: getImageUrl(currentFile),
						fileType: currentFile.file_type
					}}
				/>
			</div>
		{/if}
		{#if canNavigate}
			<div class="flex items-center gap-1 flex-shrink-0">
				<Tooltip text="Previous generation" kbd="Shift+←" position="bottom">
					<IconButton
						icon="chevron-left"
						label="Previous generation"
						disabled={!hasPrevious || navigating}
						onclick={() => navigate(-1)}
					/>
				</Tooltip>
				{#if position}
					<span class="font-mono tabular-nums text-xs text-fg-subtle" data-generation-position>
						{position.index} / {position.total}
					</span>
				{/if}
				<Tooltip text="Next generation" kbd="Shift+→" position="bottom">
					<IconButton
						icon="chevron-right"
						label="Next generation"
						disabled={!hasNext || navigating}
						onclick={() => navigate(1)}
					/>
				</Tooltip>
			</div>
		{/if}
	</svelte:fragment>

	<!-- Body (height must fill the modal) -->
	{#if loading}
		<div class="flex-1 flex items-center justify-center h-full">
			<div class="flex flex-col items-center gap-3">
				<Spinner size="lg" />
				<p class="text-sm text-fg-muted">Loading...</p>
			</div>
		</div>
	{:else if activeGeneration}
		<div class="flex flex-col md:flex-row h-full min-h-0" bind:this={detailsRoot} data-details-window>
			<!-- Left Side - Media -->
			<div class="group relative flex min-h-0 min-w-0 flex-1 flex-col">
				{#if currentFile}
					<DetailsActionBar
						bind:this={actionBar}
						items={rowItems}
						context={rowContext}
						scope="history"
						favorite={{ active: !!activeGeneration.is_favorite, onToggle: handleFavoriteToggle }}
						tags={{ selectedIds: selectedTagIds, onChange: handleBarTagsChange }}
						pageActions={generationActions}
						{downloadActions}
						{moreActions}
						pluginContext={pluginActionContext}
						onLaunch={handleLaunch}
						onOpenedInTab={handleClose}
					/>
				{/if}
			<div data-details-stage class="relative min-h-0 flex-1 overflow-hidden bg-black">
				{#if currentFile}
					<!-- Navigation Arrows -->
					{#if canGoPrev}
						<IconButton icon="chevron-left" label="Previous file" variant="overlay" onclick={goToPreviousFile} class="absolute left-4 top-1/2 -translate-y-1/2 z-10" />
					{/if}

					{#if canGoNext}
						<IconButton icon="chevron-right" label="Next file" variant="overlay" onclick={goToNextFile} class="absolute right-4 top-1/2 -translate-y-1/2 z-10" />
					{/if}

					<div class="absolute top-4 left-4 z-20 max-w-[calc(100%-2rem)] opacity-0 group-hover:opacity-100 transition-opacity duration-200">
						<div class="flex flex-col gap-2">
							<div data-stage-meta class="flex flex-wrap items-center gap-2">
								{#if mediaFiles.length > 1}
									<div data-file-counter class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded text-sm font-medium shadow-lg">
										<span class="font-mono tabular-nums">
											{currentFileIndex + 1} / {mediaFiles.length}
										</span>
									</div>
								{/if}

								{#if currentFile.width && currentFile.height}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
										{currentFile.width} × {currentFile.height}
									</span>
								{/if}

								{#if currentFile.file_size}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
										{formatBytes(currentFile.file_size)}
									</span>
								{/if}

								{#if currentFile.file_type}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg uppercase">
										{isVideoFile(currentFile) ? 'MP4' : isMeshFile(currentFile) ? resolveMeshFormat(currentFile).toUpperCase() : isAudioFile(currentFile) ? resolveAudioFormat(currentFile).toUpperCase() : 'PNG'}
									</span>
								{/if}
							</div>

							{#if selectedTags.length > 0}
								<div class="flex flex-wrap gap-1">
									{#each selectedTags as tag}
										<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg">
											{tag.name}
										</span>
									{/each}
								</div>
							{/if}
						</div>
					</div>

					{#if isVideoFile(currentFile)}
						<video
							src={getImageUrl(currentFile)}
							class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {detailBlur ? 'blur-3xl' : ''}"
							controls={!detailBlur}
							loop={$loopVideos}
							playsinline
						>
							<track kind="captions" />
						</video>
						{#if !detailBlur}
							<LoopToggle class="absolute top-4 right-4 z-20" />
						{/if}
					{:else if isMeshFile(currentFile)}
						<div class="absolute inset-0 h-full w-full p-3 md:p-6">
							<MeshPreview file={{ url: getImageUrl(currentFile), originalUrl: getImageUrl(currentFile) }} />
						</div>
					{:else if isAudioFile(currentFile)}
						<div class="absolute inset-0 h-full w-full flex items-center justify-center p-3 md:p-6">
							<div class="w-full max-w-2xl">
								<AudioPlayer tracks={currentAudioTracks(currentFile)} showWaveform={true} showDownload={false} />
							</div>
						</div>
					{:else}
						{#if stageImageLoading}
							<div class="absolute inset-0 z-[1] bg-surface-2 animate-pulse" data-stage-skeleton></div>
						{/if}
						<img
							bind:this={detailImg}
							on:load={() => (loadedStageSrc = stageImageSrc ?? undefined)}
							on:error={() => (loadedStageSrc = stageImageSrc ?? undefined)}
							src={getImageUrl(currentFile)}
							alt={`Output ${currentFileIndex + 1}`}
							class="absolute inset-0 h-full w-full object-contain p-3 md:p-6 {detailBlur ? 'blur-3xl' : ''} {currentFileAlphaCheckerboard ? 'alpha-checkerboard' : ''}"
						/>
					{/if}
					{#if detailBlur}
						<button
							type="button"
							class="absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-canvas/40 text-fg cursor-pointer"
							aria-label="Sensitive content, click to reveal"
							on:click|stopPropagation={() => nsfwRevealStore.reveal(currentRevealKey)}
						>
							<Icon name="eyes" className="w-8 h-8" strokeWidth={1.5} />
							<span class="text-sm font-medium">Sensitive content</span>
							<span class="text-xs text-fg-muted">Click to reveal</span>
						</button>
					{/if}
				{:else}
					<slot name="stageEmpty" />
				{/if}
			</div>
			</div>

			<!-- Right Side - Information -->
			<div bind:this={infoPanel} use:restoreScroll={takeDetailsScroll(activeGenerationId)} data-details-info class="w-full md:w-[400px] shrink-0 max-h-[45vh] md:max-h-none border-t md:border-t-0 md:border-l border-line bg-surface-1 overflow-y-auto min-h-0">
				<div class="py-4">
					{#if failure}
						<div class="px-4 pb-4">
							<GenerationFailureAlert notice={failure} />
						</div>
					{/if}

					{#if $$slots.afterInformation}
						<div class="px-4 pb-4">
							<slot name="afterInformation" />
						</div>
					{/if}

					<div data-details-panel>
						<DetailsSection id="info" title="Information" icon="information-circle" collapsible={false}>
							{#snippet headerRight()}
								<StarRating value={activeGeneration.rating} onChange={handleRatingChange} />
							{/snippet}
							<dl class="m-0 px-3 pb-1">
								{#if activeGeneration.preset_id}
									<DetailRow label="Preset" copy={activeGeneration.preset_id} copyLabel="Copy preset" mono={false}>
										{presetLabel}{#if activeGeneration.preset_version}<span class="whitespace-nowrap text-fg-muted">{' · v'}{activeGeneration.preset_version}</span>{/if}
									</DetailRow>
								{/if}
								<DetailRow label="ID" copy={activeGenerationId} copyLabel="Copy ID" copyAlways>{middleEllipsis(activeGenerationId)}</DetailRow>
								<DetailRow label="When" mono={false}>
									<span class="whitespace-nowrap">{runLine?.when}</span>{#if runLine?.detail}{' · '}<span data-testid="detail-generation-time">{#each runLine.detail.split(' · ') as part, i}{i > 0 ? ' · ' : ''}<span class="whitespace-nowrap">{part}</span>{/each}</span>{/if}
								</DetailRow>
							</dl>
							<div class="px-3">
								<FoldButton
									label={timesOpen ? 'Hide exact times' : 'Show exact times'}
									expanded={timesOpen}
									controls="details-exact-times"
									onToggle={() => setDetailsPref('info:times', !timesOpen)}
								/>
							</div>
							{#if timesOpen}
								<dl id="details-exact-times" class="m-0 px-3 pb-1">
									<DetailRow label="Created" mono={false}>{formatDate(activeGeneration.created_at)}</DetailRow>
									{#if activeGeneration.completed_at}
										<DetailRow label="Completed" mono={false}>{formatDate(activeGeneration.completed_at)}</DetailRow>
									{/if}
									{#if queuedTime}
										<DetailRow label="Queued" mono={false}>{queuedTime}</DetailRow>
									{/if}
									{#if runTime}
										<DetailRow label="Run time" mono={false}>{runTime}</DetailRow>
									{/if}
								</dl>
							{/if}
							<div class="space-y-1 px-3 pt-1">
								<ProvenanceLine itemType="generation" itemId={activeGenerationId} />
								<button
									type="button"
									class="flex min-h-6 items-center gap-1.5 text-sm text-fg-muted hover:text-fg"
									on:click={() => startRuleFrom(generationItemRule(activeGeneration))}
								>
									<Icon name="sparkles" className="h-3.5 w-3.5" />
									Auto-file items like this…
								</button>
							</div>
						</DetailsSection>

						<DetailsSection id="parameters" title="Parameters" icon="sliders" collapsible={false}>
							{#snippet headerRight()}
								{#if currentFile}
									<Badge variant="neutral" size="sm" class="font-mono">#{currentFileIndex + 1}</Badge>
								{/if}
							{/snippet}
							<ParametersView
								params={shownParams}
								meta={shownParamMeta}
								order={currentParamOrder}
								models={currentModels}
								{axisTags}
								loading={paramsLoading}
							/>
						</DetailsSection>

						{#if segments.length > 0}
							<DetailsSection id="segments" title="Segments" icon="document" count={segments.length} defaultOpen={false}>
								<FoldedList id="segments" items={[...positiveSegments, ...negativeSegments]} above={5} show={3}>
									{#snippet item(seg)}
										<div class={seg.is_disabled ? 'opacity-50' : ''}>
											<ParameterTextBlock
												compact
												label={seg.name || (seg.channel === 'negative' ? 'Negative' : 'Positive')}
												text={seg.text || '(empty)'}
												raw={seg.text || ''}
											>
												{#snippet prefix()}
													<span class="h-1.5 w-1.5 shrink-0 rounded-full {seg.channel === 'negative' ? 'bg-danger' : 'bg-success'}"></span>
													{#if seg.color}
														<span class="h-2 w-2 shrink-0 rounded-full" style="background-color: {seg.color}"></span>
													{/if}
												{/snippet}
												{#snippet suffix()}
													{#if seg.name}
														<span class="font-normal text-fg-subtle">{seg.channel === 'negative' ? 'Negative' : 'Positive'}</span>
													{/if}
												{/snippet}
												{#snippet footer()}
													{#if seg.description}
														<p class="mt-1.5 break-words text-sm leading-relaxed text-fg-muted">{seg.description}</p>
													{/if}
													{#if seg.phrasebooks && seg.phrasebooks.length > 0}
														<div class="mt-1.5 flex flex-wrap gap-1">
															{#each seg.phrasebooks as ac}
																<Badge variant="info" size="sm">
																	<span use:tooltip={ac.category_path ? `${ac.category_path} → ${ac.value}` : ac.value}>
																		{#if ac.category_path}
																			<span class="text-fg-subtle">{ac.category_path}</span>
																			<span class="mx-0.5 text-fg-subtle">·</span>
																		{/if}{ac.value}
																	</span>
																</Badge>
															{/each}
														</div>
													{/if}
												{/snippet}
											</ParameterTextBlock>
										</div>
									{/snippet}
								</FoldedList>
							</DetailsSection>
						{/if}

						{#if directorSegments.length > 0}
							<DetailsSection id="director" title="Director shots" icon="document" count={directorSegments.length}>
								{#if directorKeyframeCount > 0 || directorAudioCount > 0}
									<div class="flex flex-wrap gap-1 px-3 pb-2">
										{#if directorKeyframeCount > 0}
											<Badge variant="neutral" size="sm">{directorKeyframeCount} keyframe{directorKeyframeCount === 1 ? '' : 's'}</Badge>
										{/if}
										{#if directorAudioCount > 0}
											<Badge variant="neutral" size="sm">{directorAudioCount} audio track{directorAudioCount === 1 ? '' : 's'}</Badge>
										{/if}
									</div>
								{/if}
								<FoldedList id="director" items={directorSegments} above={5} show={3}>
									{#snippet item(seg, i)}
										<ParameterTextBlock compact label="Shot {i + 1}" text={seg.prompt || '(empty)'} raw={seg.prompt || ''}>
											{#snippet suffix()}
												{#if seg.sub_type}
													<Badge variant="signal" size="sm">{seg.sub_type}</Badge>
												{/if}
											{/snippet}
											{#snippet footer()}
												{#if seg.negative_prompt}
													<p class="mt-1.5 break-words text-sm leading-relaxed text-danger">{seg.negative_prompt}</p>
												{/if}
											{/snippet}
										</ParameterTextBlock>
									{/snippet}
								</FoldedList>
							</DetailsSection>
						{/if}

						<DetailsSection id="models" title="Models" icon="model" count={currentModels.length > 0 ? currentModels.length : undefined}>
							{#if paramsLoading}
								<div class="flex items-center justify-center py-4">
									<Spinner size="sm" />
								</div>
							{:else if currentModels.length > 0}
								<FoldedList id="models" items={currentModels} above={5} show={3} class="grid gap-1 px-3">
									{#snippet item(model)}
										<button
											type="button"
											class="flex w-full items-center gap-2.5 rounded px-2 py-1 text-left enabled:hover:bg-surface-2"
											on:click={() => openModelInTab(model)}
											disabled={!model.id}
										>
											{#if modelThumbnailUrl(model)}
												<div class="h-8 w-8 flex-shrink-0 overflow-hidden rounded">
													<img src={modelThumbnailUrl(model)} alt={modelDisplayName(model)} class="h-full w-full object-cover" />
												</div>
											{:else}
												<div class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded border border-line bg-surface-1">
													<Icon name="model" className="h-4 w-4 text-fg-subtle" />
												</div>
											{/if}
											<span class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
												<span class="text-base font-semibold text-fg [overflow-wrap:anywhere]">{modelDisplayName(model)}</span>
												<Badge variant="signal" size="sm">{model.model_type}</Badge>
											</span>
											{#if model.id}
												<Icon name="external-link" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
											{/if}
										</button>
									{/snippet}
								</FoldedList>
							{:else}
								<p class="px-3 text-sm text-fg-subtle">No models</p>
							{/if}
						</DetailsSection>

						{#each extraSections as section (section.id)}
							<div data-extra-section={section.id}>
								<DetailsSection id="x-{section.id}" title={section.title} icon={section.icon}>
									{#if section.rows?.length}
										<ul>
											{#each section.rows as row (row.id)}
												<li>
													<label class="flex min-h-[2.25rem] cursor-pointer items-center gap-2.5 px-3 py-1.5 text-md text-fg {row.disabled ? 'opacity-60' : ''}">
														<Switch
															size="sm"
															checked={row.checked}
															disabled={row.disabled}
															label={row.label}
															onchange={(checked) => row.onToggle(checked)}
														/>
														<span class="min-w-0 flex-1 truncate">{row.label}</span>
													</label>
												</li>
											{/each}
										</ul>
									{/if}
									{#if section.actions?.length}
										<div class="flex flex-wrap items-center gap-2 px-3 py-1">
											{#each section.actions as action (action.id)}
												<Button variant="secondary" size="sm" icon={action.icon} onclick={action.onClick}>{action.label}</Button>
											{/each}
										</div>
									{/if}
								</DetailsSection>
							</div>
						{/each}

						{#if systemTags.length > 0}
							<DetailsSection id="autotags" title="Auto tags" icon="sparkles" count={systemTags.length} defaultOpen={false}>
								<FoldedList id="autotags" items={systemTags} above={8} show={6} class="flex flex-wrap gap-1 px-3">
									{#snippet item(st)}
										<button
											type="button"
											class="inline-flex min-h-6 items-center gap-1.5 rounded border border-dashed border-line-strong bg-surface-3 px-2 py-0.5 text-sm text-fg-muted transition-colors hover:text-fg"
											use:tooltip={`Filter history by “${st.tag}”`}
											on:click={() => handleSystemTagClick(st.tag)}
										>
											<span class="max-w-[10rem] truncate">{st.tag.replace(/_/g, ' ')}</span>
											<span class="font-mono tabular-nums text-fg-subtle">{Math.round(st.confidence * 100)}%</span>
										</button>
									{/snippet}
								</FoldedList>
							</DetailsSection>
						{/if}
					</div>
				</div>
			</div>
		</div>
	{/if}
</BaseModal>
</div>

{#if showPublishModal && activeGeneration}
	<PublishToInspirationsModal
		generation={activeGeneration}
		onClose={() => (showPublishModal = false)}
	/>
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
