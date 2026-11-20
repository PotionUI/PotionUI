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
	import { formatHumanDuration, formatRunLine, formatShortDay, generationQueueLabel } from '$lib/utils/generationTime';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { api } from '$lib/services/api/index';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import { plainLetter, windowOwnsKeys } from '$lib/utils/shortcutGuard';
	import { canReturnToDetails, rememberDetailsScroll, takeDetailsScroll } from '$lib/tools/detailsReturn';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import StarRating from '$lib/components/StarRating.svelte';
	import { historyStore } from '$lib/stores/history';
	import { requestAddToKit } from '$lib/stores/kitAddRequests';
	import { libraryStore } from '$lib/stores/library';
	import { summarizeCopyOutcome } from '$lib/library/copyToLibrary';
	import { toasts } from '$lib/stores/toast';
	import { nsfwFilterStore, visibleMediaFiles, shouldBlurFile, isUnratedFile } from '$lib/stores/nsfwFilter';
	import { nsfwRevealStore, revealKey } from '$lib/stores/nsfwReveal';
	import { pickActiveGeneration, needsDetailFetch } from '$lib/utils/generationDetail';
	import { Badge, Button, CopyButton, Spinner, IconButton, Switch } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
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
	import MediaDetailsShell from '$lib/components/media-details/MediaDetailsShell.svelte';
	import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';
	import MediaDetailsHeader from '$lib/components/media-details/MediaDetailsHeader.svelte';
	import { libraryItemIcon } from '$lib/library/libraryItemMeta';
	import MediaStage from '$lib/components/media-details/MediaStage.svelte';
	import { audioTracksFor, isAudioFile, isMeshFile, isVideoFile, resolveAudioFormat } from '$lib/components/media-details/mediaFile';
	import ParametersView from '$lib/components/generation/ParametersView.svelte';
	import GenerationOriginChip from '$lib/components/GenerationOriginChip.svelte';
	import { describeOrigin, offersOpenInTab } from '$lib/generation/origin';
	import { openGenerationInNewTab } from '$lib/generation/attach/openInTabDefault';
	import ParameterTextBlock from '$lib/components/generation/ParameterTextBlock.svelte';
	import DetailsSection from '$lib/components/generation/DetailsSection.svelte';
	import GenerationFailureAlert from '$lib/components/generation/GenerationFailureAlert.svelte';
	import { failureNotice } from '$lib/components/generation/failureNotice';
	import { authStore } from '$lib/stores/auth';
	import { middleEllipsis } from '$lib/components/generation/idDisplay';
	import FoldedList from '$lib/components/generation/FoldedList.svelte';
	import { countParameters } from '$lib/components/generation/parametersModel';
	import { loadDetailsPrefs } from '$lib/components/generation/detailsPrefs';
	import type { GenerationParameterMeta } from '$lib/types/generation';
	import LiveStage from '$lib/components/generation/LiveStage.svelte';
	import LiveActionBar from '$lib/components/generation/LiveActionBar.svelte';
	import { liveWatcher, liveClock } from '$lib/generation/live/liveDefault';
	import { isTerminalLive } from '$lib/generation/live/liveState';
	import {
		batchSlots,
		batchTotal,
		elapsedSeconds,
		formatElapsed,
		isLiveStatus,
		liveStepLine,
		percentOf
	} from '$lib/generation/live/liveView';
	import { resolveOriginAction, type OriginAction } from '$lib/generation/live/originAction';
	import { openOriginEdit } from '$lib/tool-tabs/editOrigin';
	import { tabsStore } from '$lib/stores/tabs';
	import { goto } from '$app/navigation';

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
	$: hostTitle = title !== 'Generation Details';

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
	$: originInfo = activeGeneration ? describeOrigin(activeGeneration.origin) : null;
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
	$: statusVariant = getStatusVariant(activeGeneration?.status ?? '');
	$: statusIconClass = STATUS_ICON_CLASS[statusVariant];
	$: statusBoxClass = STATUS_BOX_CLASS[statusVariant];
	$: statusName = activeGeneration?.status ? activeGeneration.status.charAt(0).toUpperCase() + activeGeneration.status.slice(1) : '';
	$: headerIcon = headerIconName !== 'image' || !currentFile ? headerIconName : libraryItemIcon(currentFile.file_type);
	$: headerKindLabel = headerIconName === 'grid' ? 'Compare grid' : currentFile?.file_type ? currentFile.file_type.charAt(0).toUpperCase() + currentFile.file_type.slice(1) : 'Generation';
	$: runLine = activeGeneration ? formatRunLine(activeGeneration) : null;
	$: phoneWhen = activeGeneration ? formatShortDay(activeGeneration.completed_at ?? activeGeneration.created_at) : '';
	$: runDetail = (runLine?.detail ?? '').replace(/^took /, '');
	$: phoneDetail = (runLine?.detail?.split(' · ')[0] ?? '').replace(/^took /, '').replace(/^(failed|cancelled) after /, '$1 ');
	$: runTime =
		activeGeneration && activeGeneration.status === 'completed' && activeGeneration.completed_at
			? formatHumanDuration(activeGeneration.run_seconds)
			: null;
	$: queuedTime =
		activeGeneration && generationQueueLabel(activeGeneration)
			? formatHumanDuration(activeGeneration.queued_seconds)
			: null;
	$: exactTimes = [
		`Created ${activeGeneration ? formatDate(activeGeneration.created_at) : ''}`,
		activeGeneration?.completed_at ? `Completed ${formatDate(activeGeneration.completed_at)}` : '',
		queuedTime ? `Queued ${queuedTime}` : '',
		runTime ? `Run time ${runTime}` : ''
	]
		.filter(Boolean)
		.join(' · ');
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
		activeGeneration?.files?.length || recordLive || sawLive
			? `${activeGenerationId}:${currentFileIndex}:${recordLive ? (liveNow?.pipeId ?? '') : ''}`
			: '';
	$: if (paramsKey && paramsKey !== loadedParamsKey) {
		const silent = loadedParamsKey.startsWith(`${activeGenerationId}:`);
		loadedParamsKey = paramsKey;
		loadParametersForCurrentFile(silent);
	}


	const liveStates = liveWatcher.states;
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: liveNow = activeGenerationId ? ($liveStates[activeGenerationId] ?? null) : null;
	$: recordLive = !!activeGeneration && isLiveStatus(activeGeneration.status);
	$: queuedNow = (liveNow?.status ?? activeGeneration?.status) === 'pending';

	let releaseLive: (() => void) | null = null;
	let watchedLiveId = '';
	$: syncLiveWatch(isOpen && recordLive, activeGenerationId);

	function syncLiveWatch(open: boolean, id: string) {
		if (releaseLive && (!open || watchedLiveId !== id)) {
			releaseLive();
			releaseLive = null;
			watchedLiveId = '';
		}
		if (open && id && !releaseLive) {
			releaseLive = liveWatcher.watch(id, 'detail', activeGeneration?.status === 'pending' ? 'pending' : 'running');
			watchedLiveId = id;
		}
	}

	onDestroy(() => {
		releaseLive?.();
		releaseLive = null;
		clearTimeout(settleTimer);
	});

	let liveSeenId = '';
	$: if (recordLive) liveSeenId = activeGenerationId;
	$: sawLive = !!activeGenerationId && liveSeenId === activeGenerationId;
	$: endedInPlace = sawLive && (activeGeneration?.status === 'failed' || activeGeneration?.status === 'cancelled');
	$: liveStage = recordLive || endedInPlace;
	$: justFinished = sawLive && activeGeneration?.status === 'completed';

	let slotChoice: number | null = null;
	let lastItemIndex = 0;
	$: if (liveNow?.itemIndex != null) lastItemIndex = liveNow.itemIndex;
	let finishAppliedFor = '';
	$: if (justFinished && finishAppliedFor !== activeGenerationId && mediaFiles.length > 0) {
		finishAppliedFor = activeGenerationId;
		currentFileIndex = Math.min(slotChoice ?? lastItemIndex, mediaFiles.length - 1);
	}

	let settleTimer: ReturnType<typeof setTimeout> | undefined;
	let settleKey = '';
	$: liveSettled = sawLive && !!liveNow && isTerminalLive(liveNow.status);
	$: if (liveSettled && settleKey !== `${activeGenerationId}:${liveNow?.status}`) {
		settleKey = `${activeGenerationId}:${liveNow?.status}`;
		void settleDetail(3);
	}
	$: listSettled =
		!!generation &&
		!!loadedGeneration &&
		generation.id === loadedGeneration.id &&
		isLiveStatus(loadedGeneration.status) &&
		!isLiveStatus(generation.status);
	$: if (listSettled && settleKey !== `${activeGenerationId}:list:${generation?.status}`) {
		settleKey = `${activeGenerationId}:list:${generation?.status}`;
		void settleDetail(3);
	}

	async function refreshDetail(): Promise<GenerationHistoryItem | null> {
		const id = activeGenerationId;
		if (!id) return null;
		try {
			const response = await api.getGenerationById(id, true, true);
			if (response.success && response.data && id === activeGenerationId) {
				loadedGeneration = response.data;
				return response.data;
			}
		} catch (error) {
			logger.error('Failed to refresh generation:', error);
		}
		return null;
	}

	async function settleDetail(retries: number) {
		const detail = await refreshDetail();
		if ((!detail || isLiveStatus(detail.status)) && retries > 0) {
			clearTimeout(settleTimer);
			settleTimer = setTimeout(() => void settleDetail(retries - 1), 1000);
		}
	}

	let galleryCount = 0;
	$: if (recordLive && liveNow && liveNow.galleryUpdates !== galleryCount) {
		galleryCount = liveNow.galleryUpdates;
		if (galleryCount > 0) void refreshDetail();
	}

	$: paramCount = countParameters({ params: shownParams, meta: shownParamMeta, order: currentParamOrder, models: currentModels });
	$: liveElapsed = activeGeneration && recordLive ? elapsedSeconds(activeGeneration, $liveClock) : null;
	$: liveFinished = mediaFiles.length;
	$: liveTotal = Math.max(batchTotal(activeGeneration?.form_data, liveNow?.itemCount ?? null), liveFinished);
	$: liveSlots = batchSlots(
		liveTotal,
		liveFinished,
		endedInPlace ? (activeGeneration?.status as 'failed' | 'cancelled') : null
	);
	$: liveThumbs = mediaFiles.map((file) => mediaFileThumbnailUrl(file) || getImageUrl(file));
	$: liveFulls = mediaFiles.map((file) => getImageUrl(file));
	$: liveLine = liveNow
		? liveStepLine(liveNow, {
				isAdmin,
				noun: liveNow.clip ? 'video' : 'image',
				queued: queuedNow && !liveNow.currentStep
			})
		: {
				text: queuedNow ? 'Waiting in the queue' : 'Starting…',
				stepCount: null,
				percent: percentOf(activeGeneration?.progress)
			};
	$: livePercent = liveLine.percent ?? percentOf(activeGeneration?.progress);
	$: liveDimensions = (() => {
		const w = Number(activeGeneration?.form_data?.width);
		const h = Number(activeGeneration?.form_data?.height);
		return w > 0 && h > 0 ? `${w} × ${h}` : null;
	})();
	$: if (activeGenerationId !== renderedGenerationId) slotChoice = null;

	$: originAction = activeGeneration ? resolveOriginAction(activeGeneration, $tabsStore.tabs) : null;
	let cancelling = false;

	async function handleCancelLive() {
		if (!activeGenerationId || cancelling) return;
		cancelling = true;
		try {
			const response = await api.cancelGeneration(activeGenerationId);
			if (!response.success) toasts.error('Could not cancel this generation. Try again.');
		} catch (error) {
			logger.error('Cancel failed:', error);
			toasts.error('Could not cancel this generation. Try again.');
		} finally {
			cancelling = false;
		}
	}

	async function runOriginAction(action: OriginAction) {
		if (action.kind === 'open-in-tab') {
			await handleOpenInTab();
			return;
		}
		if (action.kind === 'go-to-tab') {
			tabsStore.setActiveTab(action.tabId);
			if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/generate')) await goto('/generate');
			handleClose();
			return;
		}
		const result = await openOriginEdit(action.origin);
		if (result.ok) handleClose();
		else toasts.error(result.message);
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

	async function loadParametersForCurrentFile(silent = false) {
		if (!activeGenerationId) return;

		if (!silent) paramsLoading = true;
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

	function formatDate(dateString?: string) {
		if (!dateString) return 'N/A';
		return parseServerDate(dateString)?.toLocaleString() ?? 'N/A';
	}

	type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

	const STATUS_ICON_CLASS: Record<BadgeVariant, string> = {
		success: 'text-success',
		danger: 'text-danger',
		warning: 'text-warning',
		signal: 'text-signal',
		info: 'text-info',
		neutral: 'text-fg-muted'
	};
	const STATUS_BOX_CLASS: Record<BadgeVariant, string> = {
		success: 'border-success/30 bg-success/10',
		danger: 'border-danger/30 bg-danger/10',
		warning: 'border-warning/30 bg-warning/10',
		signal: 'border-signal/30 bg-signal/10',
		info: 'border-info/30 bg-info/10',
		neutral: 'border-line bg-surface-2'
	};

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

	async function handleOpenInTab() {
		if (!activeGeneration) return;
		const result = await openGenerationInNewTab(activeGeneration.id, { navigate: true });
		if (result.kind === 'opened' || result.kind === 'focused') handleClose();
	}

	function buildGenerationActions(withReuse: boolean, withCopy: boolean, copying: boolean, withOpenInTab: boolean): BarAction[] {
		const actions: BarAction[] = [];
		if (withOpenInTab) {
			actions.push({
				id: 'open-in-tab',
				label: 'Open in a tab',
				icon: 'external-link',
				tooltip: 'Open this generation in a new tab, following it while it runs',
				onclick: handleOpenInTab
			});
		}
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
		actions.push({
			id: 'auto-file',
			label: 'Auto-file…',
			icon: 'sparkles',
			tooltip: 'Create a rule that files items like this one',
			onclick: () => activeGeneration && startRuleFrom(generationItemRule(activeGeneration))
		});
		return actions;
	}

	$: generationActions = buildGenerationActions(
		!!activeGeneration?.preset_id,
		canCopyToLibrary,
		copyingToLibrary,
		!!activeGeneration && offersOpenInTab(activeGeneration)
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

<MediaDetailsShell
	{isOpen}
	{title}
	{headerIconName}
	onClose={handleClose}
	onKeyDown={handleKeyDown}
	customHeader
	{loading}
	ready={!!activeGeneration}
	scrollRestore={() => takeDetailsScroll(activeGenerationId)}
	bind:root={detailsRoot}
	bind:column={infoPanel}
>
	{#snippet iconBox()}
		<button
			type="button"
			class="flex h-[30px] w-[30px] flex-shrink-0 cursor-default items-center justify-center rounded border {statusBoxClass}"
			aria-label={statusName ? `${headerKindLabel}, ${statusName.toLowerCase()}` : headerKindLabel}
			data-details-status={activeGeneration?.status ?? ''}
			use:tooltip={statusName}
		>
			<Icon name={headerIcon} className="h-4 w-4 {statusIconClass}" />
		</button>
	{/snippet}

	{#snippet headerRight()}
		{#if activeGeneration}
			<MediaDetailsHeader title={hostTitle ? title : presetLabel}>
				{#snippet version()}
					{#if !hostTitle && activeGeneration.preset_version}<span class="font-normal text-fg-muted">{' v'}{activeGeneration.preset_version}</span>{/if}
				{/snippet}
				{#snippet rating()}
					{#if !recordLive}<StarRating value={activeGeneration.rating} onChange={handleRatingChange} />{/if}
				{/snippet}
				{#snippet tools()}
					{#if currentFile}
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
						<div class="flex flex-shrink-0 items-center gap-1">
							<Tooltip text="Previous generation" kbd="Shift+←" position="bottom">
								<IconButton
									icon="chevron-left"
									label="Previous generation"
									disabled={!hasPrevious || navigating}
									onclick={() => navigate(-1)}
								/>
							</Tooltip>
							{#if position}
								<span class="font-mono tabular-nums text-xs text-fg-subtle max-sm:hidden" data-generation-position>
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
				{/snippet}
				{#snippet meta()}
					{#if recordLive}
							<Badge variant="signal" size="sm" class="uppercase tracking-wide">{queuedNow ? 'Queued' : 'Running'}</Badge>
						{:else if endedInPlace}
							<Badge variant="danger" size="sm" class="uppercase tracking-wide">{activeGeneration.status === 'cancelled' ? 'Cancelled' : 'Failed'}</Badge>
						{:else if justFinished}
							<Badge variant="success" size="sm" class="uppercase tracking-wide">Completed</Badge>
						{/if}
					{#if hostTitle && presetLabel}
						<span class="max-w-[40%] shrink-0 truncate font-medium text-fg max-sm:hidden">{presetLabel}</span>
					{/if}
					<button
						type="button"
						class="inline-flex min-w-0 cursor-default items-center gap-1 rounded text-left"
						data-testid="detail-run-line"
						use:tooltip={{ text: exactTimes, focusSelf: true, wrap: true }}
					>
						<span aria-hidden="true" class="flex-shrink-0 text-fg-subtle"><Icon name="calendar" className="h-3.5 w-3.5" /></span>
						<span class="whitespace-nowrap max-sm:hidden">{runLine?.when}</span><span class="whitespace-nowrap sm:hidden">{phoneWhen}</span>
					</button>
					{#if runLine?.detail}
						<span class="inline-flex min-w-0 items-center gap-1">
							<span aria-hidden="true" class="flex-shrink-0 text-fg-subtle"><Icon name="clock" className="h-3.5 w-3.5" /></span>
							<span class="whitespace-nowrap max-sm:hidden" data-testid="detail-generation-time">{runDetail}</span>
							<span class="whitespace-nowrap sm:hidden">{phoneDetail}</span>
						</span>
					{/if}
					<span class="inline-flex flex-shrink-0 items-center gap-1">
						<span aria-hidden="true" class="text-fg-subtle max-sm:hidden"><Icon name="hash" className="h-3.5 w-3.5" /></span>
						<code class="hidden font-mono tabular-nums sm:inline">{middleEllipsis(activeGenerationId, 17)}</code>
						<CopyButton text={activeGenerationId} ariaLabel="Copy ID" size="xs" class="flex-shrink-0" />
					</span>
						{#if originInfo}
							<span class="inline-flex min-w-0 flex-shrink items-center max-sm:hidden" data-details-origin>
								<GenerationOriginChip generation={activeGeneration} onopened={handleClose} />
							</span>
							<span class="inline-flex flex-shrink-0 items-center sm:hidden">
								<GenerationOriginChip generation={activeGeneration} iconOnly onopened={handleClose} />
							</span>
						{/if}
				{/snippet}
			</MediaDetailsHeader>
		{/if}
	{/snippet}

	{#snippet toolbar()}
		{#if activeGeneration && liveStage}
			<LiveActionBar
				live={recordLive}
				{cancelling}
				onCancel={handleCancelLive}
				origin={recordLive ? originAction : null}
				onOrigin={runOriginAction}
				canReuse={!!activeGeneration.preset_id}
				reuseLabel={recordLive ? 'Reuse settings' : 'Try again in a tab'}
				onReuse={() => dispatch('reuse', activeGeneration)}
			/>
		{:else if activeGeneration && currentFile}
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
	{/snippet}

	{#snippet stage()}
		{#if liveStage && activeGeneration}
			<LiveStage
				mode={recordLive ? 'live' : 'ended'}
				preview={liveNow?.preview ?? null}
				clip={liveNow?.clip ?? null}
				suppressed={liveNow?.previewSuppressed ?? false}
				stepText={liveLine.text}
				percent={livePercent}
				elapsed={liveElapsed}
				queued={queuedNow}
				dimensions={liveDimensions}
				slots={liveSlots}
				thumbs={liveThumbs}
				fulls={liveFulls}
				selectedSlot={slotChoice}
				onSelectSlot={(index) => (slotChoice = index)}
				{failure}
				{isAdmin}
			/>
		{:else if currentFile}
			<MediaStage
				file={currentFile}
				src={getImageUrl(currentFile)}
				alt={`Output ${currentFileIndex + 1}`}
				fileCount={mediaFiles.length}
				fileIndex={currentFileIndex}
				canPrev={canGoPrev}
				canNext={canGoNext}
				onPrev={goToPreviousFile}
				onNext={goToNextFile}
				blur={detailBlur}
				onReveal={() => nsfwRevealStore.reveal(currentRevealKey)}
				alphaCheckerboard={currentFileAlphaCheckerboard}
				imageLoading={stageImageLoading}
				bind:imageEl={detailImg}
				onImageSettled={() => (loadedStageSrc = stageImageSrc ?? undefined)}
				audioTracks={audioTracksFor(currentFile, getImageUrl(currentFile))}
				tagNames={selectedTags.map((tag) => tag.name)}
			/>
		{:else}
			<slot name="stageEmpty" />
		{/if}
	{/snippet}

	{#snippet columnContent()}
		{#if activeGeneration}
				<div class="pb-4">
					{#if failure && !endedInPlace}
						<div class="px-4 pb-4 pt-4">
							<GenerationFailureAlert notice={failure} />
						</div>
					{/if}

					{#if $$slots.afterInformation}
						<div class="px-4 pb-4 pt-4 empty:hidden">
							<slot name="afterInformation" />
						</div>
					{/if}

					<div data-details-panel>
						<DetailsSection id="parameters" strip title="Parameters" note={mediaFiles.length > 1 ? `image ${currentFileIndex + 1}` : undefined} count={paramsLoading ? undefined : paramCount} icon="sliders" collapsible={false}>
							<ParametersView
								params={shownParams}
								meta={shownParamMeta}
								order={currentParamOrder}
								models={currentModels}
								{axisTags}
								formData={activeGeneration.form_data ?? null}
								loading={paramsLoading}
							/>
						</DetailsSection>

						{#if segments.length > 0}
							<DetailsSection id="segments" title="Segments" icon="document" count={segments.length} defaultOpen={false}>
								<FoldedList id="segments" items={[...positiveSegments, ...negativeSegments]} above={5} show={3}>
									{#snippet item(seg)}
										<div class={seg.is_disabled ? 'opacity-50' : ''}>
											<ParameterTextBlock
												formData={activeGeneration.form_data ?? null}
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
										<ParameterTextBlock formData={activeGeneration.form_data ?? null} label="Shot {i + 1}" text={seg.prompt || '(empty)'} raw={seg.prompt || ''}>
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
											<span class="min-w-0 flex-1 text-base text-fg [overflow-wrap:anywhere]">{modelDisplayName(model)}</span>
											<Badge variant="signal" size="sm">{model.model_type}</Badge>
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
								<DetailsSection id="x-{section.id}" title={section.title} icon={section.icon ?? 'plug'}>
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
					<div class="mx-4 mt-4 empty:hidden"><ProvenanceLine itemType="generation" itemId={activeGenerationId} /></div>
				</div>
		{/if}
	{/snippet}
</MediaDetailsShell>

{#if showPublishModal && activeGeneration}
	<PublishToInspirationsModal
		generation={activeGeneration}
		onClose={() => (showPublishModal = false)}
	/>
{/if}
