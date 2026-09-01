<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { createEventDispatcher, onDestroy } from 'svelte';
	import { api } from '$lib/services/api';
	import type { ImageData, VideoData } from '$lib/types/tabs';
	import type { AudioData } from '$lib/types/audio';
	import type { AudioTrack } from '$lib/types/audio';
	import TagSelector from '$lib/components/TagSelector.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import portal from '$lib/actions/portal';
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import PluginSlot from '$lib/components/plugins/PluginSlot.svelte';
	import { pluginStore } from '$lib/stores/plugins';
	import { fade } from 'svelte/transition';
	import GalleryStrip from '$lib/components/workbench/GalleryStrip.svelte';
	import WorkbenchParametersModal from '$lib/components/workbench/WorkbenchParametersModal.svelte';
	import WorkbenchProfileModal from '$lib/components/workbench/WorkbenchProfileModal.svelte';
	import { authStore } from '$lib/stores/auth';
	import { resolveWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
	import ImagePreview from '$lib/components/workbench/renderers/ImagePreview.svelte';
	import VideoPreview from '$lib/components/workbench/renderers/VideoPreview.svelte';
	import AudioPreview from '$lib/components/workbench/renderers/AudioPreview.svelte';
	import MeshPreview from '$lib/components/workbench/renderers/MeshPreview.svelte';
	import '$lib/components/workbench/renderers/builtin'; // registers the image/video/audio core defaults
	import { IconButton, Button } from '$lib/components/ui';
	import { copyText } from '$lib/utils/clipboard';
	import {
		isAudioFileType,
		isMeshFileType,
		isVideoFileType,
		normalizeFileType
	} from '$lib/utils/fileType';
	import {
		downloadExtensionFor,
		entryFileType,
		galleryItemAt,
		galleryItemUrl,
		galleryTotal,
		workbenchActionsFor,
		type GalleryEntry
	} from '$lib/components/workbench/workbenchGallery';

	// Local-only widening of ImageData/VideoData: batchVideos items carry `file_type:
	// 'video'` at runtime (galleryUpdate.ts) though the shared type doesn't declare it.
	// The literal discriminant also prevents TS from structurally folding VideoData
	// into ImageData (every field on both is optional) when inferring a union of items.
	type GalleryImageItem = ImageData & { file_type?: 'image' };
	type GalleryVideoItem = VideoData & { file_type?: 'video' };
	type GalleryItem = GalleryImageItem | GalleryVideoItem | AudioData;

	// Props
	export let currentGeneration: any = null;
	export let isGenerating: boolean = false;
	export let workbenchIndex: number = 0;
	export let workbenchTotal: number = 0;
	export let batchImages: ImageData[] = [];
	export let batchVideos: VideoData[] = [];
	export let batchAudios: AudioData[] = [];
	export let batchMeshes: any[] = [];
	export let workbenchMaxHeight: string = '600';
	export let className: string = '';

	const dispatch = createEventDispatcher<{
		previous: void;
		next: void;
		heightChange: string;
		moveToWorkbench: { item: any; index: number };
	}>();

	// Comparison state
	let showCompareModal = false;
	let comparisonImage: string | null = null;
	let isComparing = false;
	let isComparingVideo = false;
	let comparisonVideo: string | null = null;

	// Video mute state (start muted for autoplay compatibility)
	let isVideoMuted = true;

	// Zoom mode state
	let isZoomMode = false;

	// Image preview modal state
	let showImageModal = false;

	// Generation parameters modal state
	let showParamsModal = false;

	// Admin-only resource-profile viewer. `hasProfile` comes from the generation
	// detail fetched on completion; the trigger is gated on admin + hasProfile.
	let showProfileModal = false;
	let hasProfile = false;
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';

	// Copy-the-error-detail affordance for the failed-generation empty state
	// (same idiom as NotificationItem's "Copy error" control).
	let copiedErrorDetail = false;
	async function handleCopyErrorDetail() {
		const detail = currentGeneration?.errorDetail;
		if (!detail) return;
		const ok = await copyText(detail);
		if (ok) {
			copiedErrorDetail = true;
			setTimeout(() => (copiedErrorDetail = false), 1500);
		}
	}

	// Height slider interaction state
	let isAdjustingHeight = false;

	// Lock body scroll when modal is open
	$: if (typeof document !== 'undefined') {
		if (showImageModal) {
			document.body.style.overflow = 'hidden';
		} else {
			document.body.style.overflow = '';
		}
	}

	// VideoPreview component reference (forwards VideoPlayer's exported methods)
	let videoPlayerRef: VideoPreview | null = null;
	let vpRegularPlaying = false;
	let vpRegularCurrentTime = 0;
	let vpRegularDuration = 0;

	// Resolved renderer for file types with no dedicated branch below (plugin-registered).
	// The gate must key off `displayFileType` alone - `displayImage` is just the
	// gallery item's URL and is truthy for ANY file type, so gating on it left
	// the renderer unresolved for a mesh and the template fell through to a
	// broken <img>.
	let resolvedFallbackRenderer: any = null;
	$: isFallbackFileType = !!displayFileType && !['image', 'video', 'audio'].includes(displayFileType);
	$: if (isFallbackFileType) {
		// Registry keys are always registered lowercase (see builtin.ts) and
		// `displayFileType` is normalized at its declaration, so the lookup key
		// matches whichever casing the item arrived in.
		resolveWorkbenchFileRenderer(displayFileType).then((c) => {
			resolvedFallbackRenderer = c ?? ImagePreview;
		});
	}

	// Cleanup scroll lock on component destroy
	onDestroy(() => {
		if (typeof document !== 'undefined') {
			document.body.style.overflow = '';
		}
	});

	// Reactive computed values
	$: hasImage = currentGeneration?.current_image;
	$: hasVideo = currentGeneration?.current_video;
	$: hasAudio = currentGeneration?.current_audio;
	$: hasMedia = hasImage || hasVideo || hasAudio;
	$: isVideo = isVideoFileType(currentGeneration?.file_type) || hasVideo;
	$: isAudio = isAudioFileType(currentGeneration?.file_type) || hasAudio;
	// The one ordered chain (images, videos, audios, meshes) every index below
	// addresses - see workbenchGallery.ts.
	$: batches = { images: batchImages, videos: batchVideos, audios: batchAudios, meshes: batchMeshes };
	$: currentGenerationTotal = galleryTotal(batches);
	$: effectiveTotal = currentGenerationTotal > 0 ? currentGenerationTotal : workbenchTotal;
	$: hasGalleryItems = currentGenerationTotal > 0;
	// Show gallery strip when: not generating AND generation is completed AND has gallery items
	$: showGalleryStrip = !isGenerating && currentGeneration?.status === 'completed' && hasGalleryItems;
	$: maxHeight = parseInt(workbenchMaxHeight, 10) || 600;

	// In gallery mode (generation completed and has gallery items), get the current item to display
	$: isGalleryMode = !isGenerating && hasGalleryItems && currentGeneration?.status === 'completed';

	// IMPORTANT: Inline the lookup so Svelte tracks workbenchIndex changes.
	let currentGalleryEntry: GalleryEntry | null = null;
	$: currentGalleryEntry =
		isGalleryMode && workbenchIndex !== undefined ? galleryItemAt(batches, workbenchIndex) : null;
	let currentGalleryItem: GalleryItem | null = null;
	$: currentGalleryItem = (currentGalleryEntry?.item as GalleryItem) ?? null;

	// The file type is normalized once, here, and every branch below compares
	// against the normalized value: the history/detail API serializes the
	// `files` DB row verbatim ('IMAGE'/'VIDEO'/'AUDIO'/'MESH') while the
	// WebSocket envelope is lowercase, and an item restored from history may
	// carry no `file_type` at all - in which case the bucket it came out of
	// decides (see entryFileType).
	$: displayFileType = isGalleryMode
		? entryFileType(currentGalleryEntry)
		: normalizeFileType(currentGeneration?.file_type);

	$: displayImage = isGalleryMode && currentGalleryItem ? galleryItemUrl(currentGalleryItem) : currentGeneration?.current_image;
	$: displayVideo = isGalleryMode && isVideoFileType(displayFileType) ? galleryItemUrl(currentGalleryItem) : currentGeneration?.current_video;
	$: displayAudio = isGalleryMode && isAudioFileType(displayFileType) ? currentGalleryItem : currentGeneration?.current_audio;
	// `mesh` has no dedicated display* wiring beyond this gate - it renders
	// through the generic `resolvedFallbackRenderer` branch below, which passes
	// the raw file object rather than a resolved URL.
	$: displayMesh = isGalleryMode && isMeshFileType(displayFileType) ? galleryItemUrl(currentGalleryItem) : currentGeneration?.current_mesh;
	$: hasDisplayMedia = !!displayImage || !!displayVideo || !!displayAudio || !!displayMesh;

	// Which per-output actions apply to what is on screen. A mesh gets download
	// and open-in-new-tab; compare/zoom/expand (all of which assume a raster
	// frame) are withheld rather than shown as no-ops.
	$: actions = workbenchActionsFor(displayFileType);

	// When true, the media "floats" on the page with an ambient color halo instead of
	// sitting inside the bordered panel card (audio keeps the card; so do compare/zoom).
	$: showAmbient =
		hasDisplayMedia &&
		displayFileType !== 'audio' &&
		!isFallbackFileType &&
		(!!displayImage || !!displayVideo) &&
		!isComparing &&
		!isZoomMode;

	// Responsive overlay chrome. The workbench panel is resizable independently of the
	// viewport, so viewport breakpoints (sm:/md:) don't apply — we measure the panel's own
	// width with a ResizeObserver and shrink/relocate the overlay chrome on narrow panels.
	let panelWidth = 0;
	function trackPanelWidth(node: HTMLElement) {
		const ro = new ResizeObserver((entries) => {
			panelWidth = entries[0].contentRect.width;
		});
		ro.observe(node);
		return { destroy: () => ro.disconnect() };
	}
	$: compactChrome = panelWidth > 0 && panelWidth < 560;
	$: tightChrome = panelWidth > 0 && panelWidth < 430;
	// Overlay action/tool buttons + their icons scale down on narrow panels.
	$: actionBtnClass = `bg-black/50 hover:bg-black/70 text-white rounded-lg shadow-lg backdrop-blur-sm transition-colors ${compactChrome ? 'p-1.5' : 'p-3'}`;
	$: actionIconClass = compactChrome ? 'h-5 w-5' : 'h-6 w-6';

	// Convert audio data to AudioTrack format for AudioPlayer
	$: audioTracks = (() => {
		if (!displayAudio) return [];

		if (Array.isArray(displayAudio)) {
			return displayAudio;
		}

		const track: AudioTrack = {
			type: displayAudio.track_type || 'mixed',
			url: displayAudio.url || displayAudio.originalUrl || '',
			originalUrl: displayAudio.originalUrl || displayAudio.url,
			duration: displayAudio.duration,
			sample_rate: displayAudio.sample_rate,
			channels: displayAudio.channels,
			file_size: displayAudio.file_size,
			format: displayAudio.format
		};

		return [track];
	})();

	// Reset all tools when generation starts
	$: if (isGenerating) {
		if (isComparing) {
			isComparing = false;
			comparisonImage = null;
			comparisonVideo = null;
			isComparingVideo = false;
			videoPlayerRef?.resetPlayState();
		}
		if (isZoomMode) {
			isZoomMode = false;
		}
		videoPlayerRef?.resetPlayState();
	}

	let fetchedParams: Record<string, any> = {};
	let imageMetadata: {
		width: number;
		height: number;
		fileSize: string;
		format: string;
	} | null = null;
	let generationFiles: any[] = [];

	// Tag management state
	let selectedTagIds: string[] = [];
	let selectedTags: Array<{ id: string; name: string }> = [];
	let isSavingTags: boolean = false;

	// Helper functions
	function getDisplayUrl(url: string): string {
		if (!url) return '';

		if (currentGeneration?.status === 'completed' && isGalleryMode) {
			if (url.startsWith('/api/')) {
				return url;
			}
		}

		if (currentGeneration?.status === 'completed' && !isVideo && !isGalleryMode) {
			if (currentGeneration?.path && !url.startsWith('/api/')) {
				return currentGeneration.path;
			}
		}

		if (url.startsWith('blob:')) return url;
		if (url.startsWith('data:')) return url;

		let processedUrl = url;

		if (/^https?:\/\/[^\/]+\/api\//.test(url)) {
			processedUrl = url.replace(/^https?:\/\/[^\/]+/, '');
		} else if (!url.includes('/') && !url.includes(':') && currentGeneration?.id) {
			processedUrl = `/api/media/generations/${currentGeneration.id}/${url}`;
		} else if (url.startsWith('/api/')) {
			processedUrl = url;
		} else {
			processedUrl = url;
		}

		return processedUrl;
	}

	// Both of these address the SAME chain as `currentGalleryEntry` - a mesh at
	// the tail of it used to fall off the end here (the walk stopped at audio),
	// leaving download / open-in-new-tab with no URL at all.
	function getCurrentWorkbenchItem(): GalleryItem | null {
		const entry =
			workbenchIndex !== undefined ? galleryItemAt(batches, workbenchIndex) : null;
		return ((entry ?? galleryItemAt(batches, 0))?.item as GalleryItem) ?? null;
	}

	function getCurrentOriginalUrl(): string | null {
		const url = galleryItemUrl(getCurrentWorkbenchItem());
		if (url) return url;

		if (displayVideo) return displayVideo;
		if (currentGeneration?.current_video) return currentGeneration.current_video;

		return null;
	}

	function getCurrentFileMetadata() {
		if (!generationFiles?.length || workbenchIndex === undefined) {
			return null;
		}

		const originalUrl = getCurrentOriginalUrl();
		if (!originalUrl) return null;

		// `file_type` on a `files` row is UPPERCASE; match on the normalized
		// value so a mesh isn't silently looked up as an image.
		const fileType = displayFileType || (isAudio ? 'audio' : isVideo ? 'video' : 'image');

		const fileByIndex = generationFiles.find(file =>
			normalizeFileType(file.file_type) === fileType &&
			file.is_final === true &&
			file.file_path.includes(`${workbenchIndex}.`)
		);

		if (fileByIndex) return fileByIndex;

		const finalFiles = generationFiles.filter(file =>
			normalizeFileType(file.file_type) === fileType && file.is_final === true
		);

		if (finalFiles.length > 0) {
			if (workbenchIndex < finalFiles.length) return finalFiles[workbenchIndex];
			return finalFiles[finalFiles.length - 1];
		}

		return null;
	}

	function handlePrevious() {
		dispatch('previous');
	}

	function handleNext() {
		dispatch('next');
	}

	// Arrow-key nav for the fullscreen preview modal, mirroring
	// GenerationDetailsModal's handleKeyDown - only active while the modal is open,
	// so it doesn't compete with the inline workbench's own arrow-key handling (it has none).
	function handleModalKeyDown(event: KeyboardEvent) {
		if (!showImageModal) return;
		if (event.key === 'ArrowLeft' && workbenchIndex > 0) {
			event.preventDefault();
			handlePrevious();
		} else if (event.key === 'ArrowRight' && workbenchIndex < effectiveTotal - 1) {
			event.preventDefault();
			handleNext();
		}
	}

	function handleHeightChange(event: Event) {
		const target = event.target as HTMLInputElement;
		const height = target.value;
		dispatch('heightChange', height);
	}

	function handleGallerySelect(event: CustomEvent<{ item: any; index: number }>) {
		dispatch('moveToWorkbench', event.detail);
	}

	function openInNewTab() {
		const originalUrl = getCurrentOriginalUrl();

		if (!originalUrl) {
			logger.error('[Workbench] No original URL found');
			return;
		}

		const displayUrl = getDisplayUrl(originalUrl);

		if (displayUrl.startsWith('blob:')) {
			const link = document.createElement('a');
			link.href = displayUrl;
			link.target = '_blank';
			const kind = displayFileType || 'image';
			const ext = downloadExtensionFor(displayFileType, getCurrentWorkbenchItem());
			link.download = `generated-${kind}-${workbenchIndex + 1}.${ext}`;
			link.click();
		} else {
			window.open(displayUrl, '_blank');
		}
	}

	// Track the generation ID to avoid refetching on every render
	let lastFetchedGenerationId: string | null = null;

	// A generation is "settled" (has a final generation detail worth fetching --
	// files/tags/has_profile) once it's completed OR failed. A failed run can
	// still have captured a resource profile up to the crash (see profiler.stop()
	// in the finally block of GenerationManager.generate()), so this must not be
	// completed-only or a failed generation's profile viewer never appears.
	$: isSettledGeneration = currentGeneration?.status === 'completed' || currentGeneration?.status === 'failed';

	// Reset tags when generation changes (before settling)
	$: if (currentGeneration?.id && currentGeneration.id !== lastFetchedGenerationId && !isSettledGeneration) {
		selectedTags = [];
		selectedTagIds = [];
		hasProfile = false;
	}

	// Fetch generation files once the generation settles, to get full metadata
	$: if (currentGeneration?.id && isSettledGeneration && currentGeneration.id !== lastFetchedGenerationId) {
		fetchGenerationFiles();
		loadGenerationTags();
	}

	async function fetchGenerationFiles() {
		const generation_id = currentGeneration?.generation_id || currentGeneration?.id;
		if (!generation_id) return;

		try {
			const response = await api.getGenerationById(generation_id, false, true);
			if (response.success && response.data?.files) {
				generationFiles = response.data.files;
				hasProfile = response.data.has_profile === true;
				lastFetchedGenerationId = currentGeneration.id;
			}
		} catch (error) {
			logger.error('Error fetching generation files:', error);
		}
	}

	// Load metadata when workbench index or media type changes. `generationFiles`
	// is referenced here (even though getCurrentFileMetadata() reads it) so this
	// block also reruns once the async fetchGenerationFiles() call populates real
	// width/height — otherwise the metadata calculated the instant the video/image
	// becomes displayable (before that fetch resolves) never gets recomputed and a
	// video with no live `resolution` (unlike images, see below) stays stuck at 0x0.
	// Only raster outputs have width/height/size to probe. A mesh would
	// otherwise take the `displayImage` branch (that URL is type-blind) and be
	// fed to `new Image()` as a .glb.
	$: if (hasDisplayMedia && generationFiles && actions.hasPixelMetadata) {
		if (displayFileType === 'video' && displayVideo) {
			loadVideoMetadata();
		} else if (displayImage) {
			loadImageMetadata();
		}
	} else if (hasDisplayMedia && !actions.hasPixelMetadata) {
		imageMetadata = null;
	}

	function loadVideoMetadata() {
		const currentItem = isGalleryMode ? getCurrentWorkbenchItem() : null;
		const fileMetadata = getCurrentFileMetadata();

		let videoMetadata = {
			width: 0,
			height: 0,
			fileSize: 'Unknown',
			format: 'MP4'
		};

		if (fileMetadata?.width && fileMetadata?.height) {
			videoMetadata.width = fileMetadata.width;
			videoMetadata.height = fileMetadata.height;
		} else if (currentItem && 'resolution' in currentItem && currentItem.resolution && Array.isArray(currentItem.resolution) && currentItem.resolution.length >= 2) {
			videoMetadata.width = currentItem.resolution[0];
			videoMetadata.height = currentItem.resolution[1];
		} else if (currentGeneration?.video_metadata?.resolution) {
			videoMetadata.width = currentGeneration.video_metadata.resolution[0];
			videoMetadata.height = currentGeneration.video_metadata.resolution[1];
		}

		if (fileMetadata?.file_size) {
			const bytes = fileMetadata.file_size;
			if (bytes < 1024) {
				videoMetadata.fileSize = `${bytes} B`;
			} else if (bytes < 1024 * 1024) {
				videoMetadata.fileSize = `${(bytes / 1024).toFixed(1)} KB`;
			} else {
				videoMetadata.fileSize = `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
			}
		}

		const displayUrl = displayVideo ? getDisplayUrl(displayVideo) : null;
		if (displayUrl) {
			if (displayUrl.includes('.webm')) {
				videoMetadata.format = 'WEBM';
			} else if (displayUrl.includes('.avi')) {
				videoMetadata.format = 'AVI';
			} else if (displayUrl.includes('.mov')) {
				videoMetadata.format = 'MOV';
			} else {
				videoMetadata.format = 'MP4';
			}

			if (!displayUrl.startsWith('blob:') && !displayUrl.startsWith('data:')) {
				fetch(displayUrl, { method: 'HEAD' })
					.then(response => {
						const contentLength = response.headers.get('content-length');
						if (contentLength) {
							const bytes = parseInt(contentLength);
							let sizeStr: string;
							if (bytes < 1024) {
								sizeStr = `${bytes} B`;
							} else if (bytes < 1024 * 1024) {
								sizeStr = `${(bytes / 1024).toFixed(1)} KB`;
							} else {
								sizeStr = `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
							}
							if (imageMetadata) {
								imageMetadata = { ...imageMetadata, fileSize: sizeStr };
							} else {
								imageMetadata = { ...videoMetadata, fileSize: sizeStr };
							}
						}
					})
					.catch(() => {});
			}
		}

		imageMetadata = videoMetadata;
	}

	function loadImageMetadata() {
		const currentItem = isGalleryMode ? getCurrentWorkbenchItem() : null;
		const originalUrl = isGalleryMode ? getCurrentOriginalUrl() : displayImage;
		const fileMetadata = getCurrentFileMetadata();

		if (fileMetadata?.width && fileMetadata?.height) {
			let fileSize: string = 'Unknown';
			let format: string = 'PNG';

			if (fileMetadata.file_size) {
				const bytes = fileMetadata.file_size;
				if (bytes < 1024) {
					fileSize = `${bytes} B`;
				} else if (bytes < 1024 * 1024) {
					fileSize = `${(bytes / 1024).toFixed(1)} KB`;
				} else {
					fileSize = `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
				}
			}

			if (fileMetadata.mime_type) {
				const mimeFormat = fileMetadata.mime_type.split('/')[1];
				format = mimeFormat ? mimeFormat.toUpperCase() : 'PNG';
			} else if (fileMetadata.file_path) {
				const extension = fileMetadata.file_path.split('.').pop();
				format = extension ? extension.toUpperCase() : 'PNG';
			}

			imageMetadata = {
				width: fileMetadata.width,
				height: fileMetadata.height,
				fileSize,
				format
			};
		} else if (currentItem && 'resolution' in currentItem && currentItem.resolution && Array.isArray(currentItem.resolution) && currentItem.resolution.length >= 2) {
			imageMetadata = {
				width: currentItem.resolution[0],
				height: currentItem.resolution[1],
				fileSize: 'Unknown',
				format: 'PNG'
			};

			if (originalUrl) {
				const displayUrl = getDisplayUrl(originalUrl);
				if (!displayUrl.startsWith('blob:') && !displayUrl.startsWith('data:')) {
					fetch(displayUrl, { method: 'HEAD' })
						.then(response => {
							const contentLength = response.headers.get('content-length');
							if (contentLength && imageMetadata) {
								const bytes = parseInt(contentLength);
								let sizeStr: string;
								if (bytes < 1024) {
									sizeStr = `${bytes} B`;
								} else if (bytes < 1024 * 1024) {
									sizeStr = `${(bytes / 1024).toFixed(1)} KB`;
								} else {
									sizeStr = `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
								}
								imageMetadata = { ...imageMetadata, fileSize: sizeStr };
							}
						})
						.catch(() => {});
				}
			}
		} else if (originalUrl) {
			const displayUrl = getDisplayUrl(originalUrl);
			const img = new Image();
			img.onload = () => {
				imageMetadata = {
					width: img.naturalWidth,
					height: img.naturalHeight,
					fileSize: 'Unknown',
					format: 'PNG'
				};
			};
			img.src = displayUrl;
		}
	}

	let lastFetchedParamsKey: string | null = null;

	$: {
		const paramsKey = `${currentGeneration?.id}-${workbenchIndex}`;
		if (currentGeneration?.id && workbenchIndex !== undefined && currentGeneration?.status === 'completed' && paramsKey !== lastFetchedParamsKey) {
			fetchGenerationParams();
		}
	}

	async function fetchGenerationParams() {
		const generation_id = currentGeneration?.generation_id || currentGeneration?.id;

		if (!generation_id || workbenchIndex === undefined) return;

		try {
			const response = await api.getGenerationParams(generation_id, workbenchIndex);

			if (response.success && response.data) {
				fetchedParams = response.data.parameters || {};
				lastFetchedParamsKey = `${currentGeneration.id}-${workbenchIndex}`;
			} else {
				const currentItem = getCurrentWorkbenchItem();
				if (currentItem) {
					fetchedParams = buildParamsFromItem(currentItem);
				}
				lastFetchedParamsKey = `${currentGeneration.id}-${workbenchIndex}`;
			}
		} catch (error) {
			logger.error('Error fetching generation parameters:', error);
			const currentItem = getCurrentWorkbenchItem();
			if (currentItem) {
				fetchedParams = buildParamsFromItem(currentItem);
			}
			lastFetchedParamsKey = `${currentGeneration.id}-${workbenchIndex}`;
		}
	}

	function buildParamsFromItem(item: any): Record<string, any> {
		return {
			...(item.seed !== undefined && { seed: item.seed }),
			...(item.resolution && {
				resolution: Array.isArray(item.resolution)
					? `${item.resolution[0]} × ${item.resolution[1]}`
					: item.resolution
			}),
			...(item.sampler && { sampler: item.sampler }),
			...(item.cfg !== undefined && { cfg: item.cfg }),
			...(item.step !== undefined && { steps: item.step }),
			...(item.clip_skip !== undefined && { clip_skip: item.clip_skip }),
			...(item.denoise !== undefined && { denoise: item.denoise }),
			...(item.duration !== undefined && { duration: `${Math.round(item.duration)}s` }),
			...(item.fps !== undefined && { fps: item.fps }),
			...(item.motion_strength !== undefined && { motion_strength: item.motion_strength }),
		};
	}

	async function loadGenerationTags() {
		const generation_id = currentGeneration?.generation_id || currentGeneration?.id;
		if (!generation_id) return;

		try {
			const response = await api.getGenerationById(generation_id, true, false);
			if (response.success && response.data?.tags) {
				selectedTags = response.data.tags.map((tag: any) => ({
					id: tag.id,
					name: tag.name
				}));
				selectedTagIds = selectedTags.map(tag => tag.id);
			}
		} catch (error) {
			logger.error('[Workbench] Error loading tags:', error);
		}
	}

	async function handleTagsChange(event: CustomEvent<string[]>) {
		const generation_id = currentGeneration?.generation_id || currentGeneration?.id;
		if (!generation_id) return;

		const newTagIds = event.detail;
		isSavingTags = true;

		try {
			const response = await api.updateGenerationTags(generation_id, newTagIds);
			if (response.success) {
				selectedTagIds = newTagIds;
				await loadGenerationTags();
			} else {
				logger.error('[Workbench] Failed to update tags:', response.message);
			}
		} catch (error) {
			logger.error('[Workbench] Error updating tags:', error);
		} finally {
			isSavingTags = false;
		}
	}

	// Comparison functionality
	function openCompareModal() {
		showCompareModal = true;
	}

	function handleSelectComparisonImage(generation: any, file: any) {
		if (!file) return;

		const filename = file.file_path.split('/').pop();
		const segments = file.file_path.split('/').filter((s: string) => s);
		const generationId = segments[segments.length - 2];

		const apiUrl = `/api/media/generations/${generationId}/${filename}`;
		comparisonImage = apiUrl;
		isComparing = true;
		showCompareModal = false;
	}

	function handleSelectComparisonVideo(generation: any, file: any) {
		if (!file) return;

		const filename = file.file_path.split('/').pop();
		const segments = file.file_path.split('/').filter((s: string) => s);
		const generationId = segments[segments.length - 2];

		const apiUrl = `/api/media/generations/${generationId}/${filename}`;
		comparisonVideo = apiUrl;
		isComparingVideo = true;
		isComparing = true;
		showCompareModal = false;
	}

	function exitComparisonMode() {
		isComparing = false;
		comparisonImage = null;
		comparisonVideo = null;
		isComparingVideo = false;
	}

	function enterZoomMode() {
		isZoomMode = true;
	}

	function exitZoomMode() {
		isZoomMode = false;
	}

	async function handleImageClick(event: CustomEvent<MouseEvent>) {
		const hooks = pluginStore.getPluginsByHook('workbench.image.click');
		if (hooks.length === 0) return;

		const imageUrl = displayImage ? getDisplayUrl(displayImage) : null;
		if (!imageUrl) return;

		const mouseEvent = event.detail;
		const context = {
			imageUrl,
			clickX: mouseEvent.clientX,
			clickY: mouseEvent.clientY,
			generationId: currentGeneration?.id,
			workbenchIndex,
			metadata: imageMetadata
		};

		for (const hook of hooks) {
			try {
				const response = await fetch(`${api.getBaseURL()}/api/plugins/${hook.plugin_id}/execute`, {
					method: 'POST',
					credentials: 'include',
					headers: {
						'Content-Type': 'application/json',
						...(api.getToken() ? { Authorization: `Bearer ${api.getToken()}` } : {})
					},
					body: JSON.stringify({
						hook: 'workbench.image.click',
						context
					})
				});

				if (!response.ok) {
					logger.error(`Plugin hook error for ${hook.plugin_id}:`, response.statusText);
				}
			} catch (error) {
				logger.error(`Plugin hook error for ${hook.plugin_id}:`, error);
			}
		}
	}

	function handleVideoMuteChange(event: CustomEvent<boolean>) {
		isVideoMuted = event.detail;
	}

	function formatTime(seconds: number): string {
		const mins = Math.floor(seconds / 60);
		const secs = Math.floor(seconds % 60);
		return `${mins}:${secs.toString().padStart(2, '0')}`;
	}
</script>

<svelte:window on:keydown={handleModalKeyDown} />

<div
	class="relative flex-1 flex flex-col {className} {showAmbient ? 'overflow-visible' : 'rounded-lg overflow-hidden bg-canvas border border-line-strong shadow-floating'}"
	id="workbench-container"
	use:trackPanelWidth
>
	{#if hasDisplayMedia}
		<!-- Main media display area -->
		<div class="relative isolate flex items-center justify-center group {showAmbient ? '' : 'bg-surface-2'}" style="height: {maxHeight}px">
			<!-- Ambient "screen extend" halo: a scaled, heavily-blurred echo of the current
			     media that spills well past the media box and radiates onto the page. -->
			{#if showAmbient}
				<div class="ambient-glow" aria-hidden="true">
					{#key displayImage || displayVideo}
						{#if displayFileType === 'video' && displayVideo}
							<!-- svelte-ignore a11y-media-has-caption -->
							<video
								src={getDisplayUrl(displayVideo)}
								class="ambient-glow-media"
								autoplay
								muted
								loop
								playsinline
								preload="auto"
							></video>
						{:else if displayImage}
							<img src={getDisplayUrl(displayImage)} alt="" class="ambient-glow-media" />
						{/if}
					{/key}
				</div>
			{/if}

			<!-- Left Navigation Arrow - hidden during comparison and zoom -->
			{#if hasDisplayMedia && !isGenerating && !isComparing && !isZoomMode && effectiveTotal > 1 && workbenchIndex > 0}
				<button
					on:click={handlePrevious}
					class="absolute left-4 z-20 bg-black/90 backdrop-blur-sm hover:bg-black/70 shadow-lg border border-white/10 w-12 h-12 rounded-full opacity-80 hover:opacity-100 transition-all duration-200 flex items-center justify-center"
					title="Previous image"
				>
					<svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
					</svg>
				</button>
			{/if}

			<!-- Right Navigation Arrow - hidden during comparison and zoom -->
			{#if hasDisplayMedia && !isGenerating && !isComparing && !isZoomMode && effectiveTotal > 1 && workbenchIndex < effectiveTotal - 1}
				<button
					on:click={handleNext}
					class="absolute right-4 z-20 bg-black/90 backdrop-blur-sm hover:bg-black/70 shadow-lg border border-white/10 w-12 h-12 rounded-full opacity-80 hover:opacity-100 transition-all duration-200 flex items-center justify-center"
					title="Next image"
				>
					<svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
					</svg>
				</button>
			{/if}

			<!-- Media Display -->
			{#key displayImage || displayVideo || displayAudio || displayMesh}
				<div in:fade={{ duration: 200 }} class="contents">
					{#if displayFileType === 'audio' && audioTracks.length > 0}
						<!-- Audio Player -->
						<AudioPreview
							tracks={audioTracks}
							showWaveform={true}
							showDownload={true}
							className="shadow-lg"
						/>
					{:else if displayFileType === 'video' && displayVideo}
						<VideoPreview
							bind:this={videoPlayerRef}
							bind:isRegularVideoPlaying={vpRegularPlaying}
							bind:regularVideoCurrentTime={vpRegularCurrentTime}
							bind:regularVideoDuration={vpRegularDuration}
							videoUrl={getDisplayUrl(displayVideo)}
							comparisonVideoUrl={comparisonVideo}
							isComparing={isComparingVideo && isComparing}
							isMuted={isVideoMuted}
							on:exitComparison={exitComparisonMode}
							on:muteChange={handleVideoMuteChange}
						/>
					{:else if isFallbackFileType && resolvedFallbackRenderer}
						<!-- A registered `workbench.file` renderer for a file_type with no
						     dedicated branch above - mesh, and whatever registers next.
						     This has to sit BEFORE the image branch: `displayImage` is just
						     the gallery item's url and does not look at file_type, so a
						     non-image would otherwise render as a broken <img>. -->
						<svelte:component this={resolvedFallbackRenderer} file={currentGalleryItem ?? currentGeneration} />
					{:else if displayImage}
						<ImagePreview
							imageUrl={getDisplayUrl(displayImage)}
							comparisonImage={comparisonImage}
							isComparing={isComparing && !isComparingVideo}
							isZoomMode={isZoomMode}
							on:exitComparison={exitComparisonMode}
							on:exitZoom={exitZoomMode}
							on:imageClick={handleImageClick}
							on:imageDoubleClick={() => (showImageModal = true)}
						/>
					{/if}
				</div>
			{/key}

			<!-- Action buttons - only when generation is settled (completed or failed) and NOT comparing or zooming -->
			{#if hasDisplayMedia && isSettledGeneration && !isComparing && !isZoomMode}
				<div class="absolute top-4 right-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex gap-2">
					<!-- Tag icon button with dropdown -->
					<Tooltip text="Edit tags" position="bottom" delay={150}>
					<TagSelector
						{selectedTagIds}
						on:change={handleTagsChange}
						placeholder="Add tags..."
						allowCreate={true}
						compact={true}
						iconOnly={true}
					/>
					</Tooltip>

					<!-- Generation parameters button -->
					<Tooltip text="Generation parameters" position="bottom" delay={150}>
					<button
						on:click={() => (showParamsModal = true)}
						class={actionBtnClass}
						aria-label="Generation parameters"
					>
						<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
						</svg>
					</button>
					</Tooltip>

					<!-- Resource profile button (admin-only; only when a profile was captured) -->
					{#if isAdmin && hasProfile}
						<Tooltip text="Resource profile" position="bottom" delay={150}>
						<button
							on:click={() => (showProfileModal = true)}
							class={actionBtnClass}
							aria-label="Resource profile"
						>
							<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
							</svg>
						</button>
						</Tooltip>
					{/if}

					<!-- Download button (hide for audio as AudioPlayer has its own) -->
					{#if actions.canDownload}
						<Tooltip text="Download file" position="bottom" delay={150}>
						<button
							on:click={() => {
								const originalUrl = getCurrentOriginalUrl();
								if (originalUrl) {
									const link = document.createElement('a');
									link.href = getDisplayUrl(originalUrl);
									const ext = downloadExtensionFor(displayFileType, getCurrentWorkbenchItem());
									link.download = `generation-${currentGeneration.id}-${workbenchIndex + 1}.${ext}`;
									link.click();
								}
							}}
							class={actionBtnClass}
							aria-label="Download file"
						>
							<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
							</svg>
						</button>
						</Tooltip>
					{/if}

					<!-- Open in new tab button -->
					<Tooltip text="Open in new tab" position="bottom" delay={150}>
					<button
						on:click={openInNewTab}
						class={actionBtnClass}
						aria-label="Open in new tab"
					>
						<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
						</svg>
					</button>
					</Tooltip>

					<!-- Show in modal button. The modal only knows how to draw <img>
					     and <video>, so a mesh (which renders through its own
					     registered renderer) has nothing to expand into. -->
					{#if actions.canExpand}
						<Tooltip text="Expand preview" position="bottom" delay={150}>
						<button
							on:click={() => showImageModal = true}
							class={actionBtnClass}
							aria-label="Expand preview"
						>
							<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
							</svg>
						</button>
						</Tooltip>
					{/if}

					<!-- Plugin actions slot -->
					<PluginSlot
						hookName="workbench.actions"
						position="top-right"
						context={{
							imageUrl: displayImage ? getDisplayUrl(displayImage) : null,
							videoUrl: displayVideo ? getDisplayUrl(displayVideo) : null,
							audioData: displayAudio,
							generationId: currentGeneration?.id,
							workbenchIndex,
							metadata: imageMetadata,
							fileType: displayFileType
						}}
					/>
				</div>
			{/if}

			<!-- Top-left metadata overlay - shown on hover/focus, hidden during comparison and zoom -->
			{#if hasDisplayMedia && currentGeneration?.status === 'completed' && !isComparing && !isZoomMode}
				<div class="absolute top-4 left-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
					<div class="flex flex-col gap-2">
						<div class="flex flex-wrap items-center gap-2">
							<!-- Navigation Counter (always shown) -->
							{#if effectiveTotal > 1}
								<div class="bg-black/70 backdrop-blur-sm text-white px-3 py-1.5 rounded-full text-sm font-medium shadow-lg">
									<span class="font-mono text-xs tabular-nums">
										{workbenchIndex + 1} / {effectiveTotal}
									</span>
								</div>
							{/if}

							<!-- Image metadata — progressively hidden on narrow panels
							     (all of it remains available in the Parameters modal). -->
							{#if imageMetadata}
								{#if !tightChrome}
									<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
										{imageMetadata.width} × {imageMetadata.height}
									</span>
								{/if}
								{#if !compactChrome}
									{#if imageMetadata.fileSize && imageMetadata.fileSize !== 'Unknown'}
										<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs font-mono tabular-nums rounded shadow-lg">
											{imageMetadata.fileSize}
										</span>
									{/if}
									{#if imageMetadata.format}
										<span class="px-2 py-1 bg-black/50 backdrop-blur-sm text-white text-xs rounded shadow-lg">
											{imageMetadata.format}
										</span>
									{/if}
								{/if}
							{/if}
						</div>

						<!-- Selected tags display - shown on hover -->
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
			{/if}

			<!-- Processing indicator -->
			{#if currentGeneration?.status === 'running'}
				<div class="absolute inset-0 flex items-end justify-center pb-4 z-10">
					<div class="bg-black/80 backdrop-blur-sm text-white px-4 py-2 rounded text-sm font-medium shadow-lg border border-white/10">
						<div class="flex items-center gap-2">
							<div class="spinner"></div>
							{isVideo ? 'Generating video...' : 'Processing...'}
						</div>
					</div>
				</div>
			{/if}

			<!-- Tools section - shown in gallery mode OR for completed videos.
			     Image/video tools only: `displayImage` is type-blind (it is just the
			     gallery item's URL), so without the `canCompare` guard these appear
			     for a mesh - where neither compare nor zoom works - and sit on top
			     of MeshPreview's bottom-left stats. -->
			{#if !isComparing && !isZoomMode && actions.canCompare && currentGeneration?.status === 'completed' && (
				(isGalleryMode && (displayImage || (displayFileType === 'video' && displayVideo))) ||
				(!isGalleryMode && displayFileType === 'video' && displayVideo)
			)}
				<div class="absolute bottom-4 left-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex gap-2">
					<!-- Compare button -->
					<Tooltip text={displayFileType === 'video' ? 'Compare with another video' : 'Compare with another image'} position="top" delay={150}>
					<button
						on:click={openCompareModal}
						class={actionBtnClass}
						aria-label={displayFileType === 'video' ? 'Compare with another video' : 'Compare with another image'}
					>
						<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
						</svg>
					</button>
					</Tooltip>

					<!-- Zoom/Pan button (images only) -->
					{#if actions.canZoom}
						<Tooltip text="Zoom and pan image" position="top" delay={150}>
						<button
							on:click={enterZoomMode}
							class={actionBtnClass}
							aria-label="Zoom and pan image"
						>
							<svg class={actionIconClass} fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
							</svg>
						</button>
						</Tooltip>
					{/if}

					<!-- Plugin tools slot -->
					<PluginSlot
						hookName="workbench.tools"
						position="bottom-left"
						context={{
							imageUrl: displayImage ? getDisplayUrl(displayImage) : null,
							generationId: currentGeneration?.id,
							workbenchIndex,
							metadata: imageMetadata,
							fileType: displayFileType
						}}
					/>
				</div>
			{/if}

			<!-- Height slider - bottom right, appears on hover or while adjusting -->
			{#if !isComparing && !isZoomMode}
				<div class="absolute bottom-4 right-4 z-20 transition-opacity duration-200 {isAdjustingHeight ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
					<div class="flex items-center gap-2 bg-black/50 backdrop-blur-sm rounded-lg px-3 py-2 shadow-lg">
						<svg class="w-4 h-4 text-white/70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
						</svg>
						<input
							type="range"
							min="550"
							max="1000"
							step="50"
							value={maxHeight}
							on:input={handleHeightChange}
							on:mousedown={() => isAdjustingHeight = true}
							on:mouseup={() => isAdjustingHeight = false}
							on:mouseleave={() => isAdjustingHeight = false}
							class="w-24 h-1.5 bg-white/20 rounded-lg appearance-none cursor-pointer accent-white"
						/>
						<span class="text-xs text-white/70 font-mono tabular-nums w-12">{maxHeight}px</span>
					</div>
				</div>
			{/if}
		</div>

		<!-- Video controls bar for regular (non-comparison) video - rendered outside media area -->
		{#if displayFileType === 'video' && displayVideo && !isComparing}
			<div class="flex-shrink-0 bg-canvas border-t border-line-strong px-4 py-2 flex items-center gap-3">
				<!-- Play/Pause button -->
				<Tooltip text={vpRegularPlaying ? 'Pause video' : 'Play video'} position="top" delay={150}>
				<button
					on:click={() => videoPlayerRef?.handleRegularVideoTogglePlayPause()}
					class="text-fg hover:text-signal transition-colors p-1"
					aria-label={vpRegularPlaying ? 'Pause video' : 'Play video'}
				>
					{#if vpRegularPlaying}
						<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
							<path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
						</svg>
					{:else}
						<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
							<path d="M8 5v14l11-7z" />
						</svg>
					{/if}
				</button>
				</Tooltip>

				<!-- Time display -->
				<span class="text-xs text-fg-muted font-mono tabular-nums w-20 text-center flex-shrink-0">
					{formatTime(vpRegularCurrentTime)} / {formatTime(vpRegularDuration)}
				</span>

				<!-- Seek/progress bar -->
				<div
					class="flex-1 h-2 bg-surface-3 rounded-full cursor-pointer relative group/seek"
					on:click={(e) => videoPlayerRef?.handleRegularVideoSeek(e)}
					role="slider"
					aria-label="Seek video"
					aria-valuenow={vpRegularCurrentTime}
					aria-valuemin={0}
					aria-valuemax={vpRegularDuration}
					tabindex="0"
				>
					<div
						class="h-full bg-signal rounded-full transition-[width] duration-75"
						style="width: {vpRegularDuration > 0 ? (vpRegularCurrentTime / vpRegularDuration) * 100 : 0}%;"
					></div>
					<!-- Thumb -->
					<div
						class="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-fg rounded-full shadow opacity-0 group-hover/seek:opacity-100 transition-opacity"
						style="left: {vpRegularDuration > 0 ? (vpRegularCurrentTime / vpRegularDuration) * 100 : 0}%;"
					></div>
				</div>

				<!-- Mute toggle -->
				<Tooltip text={isVideoMuted ? 'Unmute video' : 'Mute video'} position="top" delay={150}>
				<button
					on:click={() => videoPlayerRef?.handleVideoToggleMute()}
					class="text-fg hover:text-signal transition-colors p-1 flex-shrink-0"
					aria-label={isVideoMuted ? 'Unmute video' : 'Mute video'}
				>
					{#if isVideoMuted}
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
								d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z M17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
						</svg>
					{:else}
						<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
								d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
						</svg>
					{/if}
				</button>
				</Tooltip>
			</div>
		{/if}

		<!-- Gallery strip -->
		{#if showGalleryStrip}
			<GalleryStrip
				{batchImages}
				{batchVideos}
				{batchAudios}
				{batchMeshes}
				selectedIndex={workbenchIndex}
				on:select={handleGallerySelect}
			/>
		{/if}
	{:else if currentGeneration?.status === 'failed'}
		<!-- Failed state: the generation errored and no media survived (or none
			was produced before the failure) - the generic "creations appear here"
			empty state would otherwise silently swallow a real error. -->
		<div class="relative flex items-center justify-center dot-grid group" style="height: {maxHeight}px">
			<div class="flex flex-col items-center justify-center h-full text-center px-8 select-none">
				<div class="w-20 h-20 rounded-2xl bg-danger/10 flex items-center justify-center mb-5">
					<svg class="w-10 h-10 text-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
					</svg>
				</div>
				<p class="text-fg text-sm font-medium mb-1">Generation failed</p>
				<p class="text-fg-subtle text-xs max-w-[320px]">
					{currentGeneration?.message || 'Something went wrong while generating. Try again, or check the details below.'}
				</p>

				{#if currentGeneration?.errorDetail}
					<details class="mt-3 w-full max-w-sm text-left select-text">
						<summary class="text-2xs text-fg-subtle cursor-pointer select-none w-fit mx-auto">Error details</summary>
						<div class="relative mt-1.5">
							<pre class="font-mono text-2xs bg-surface-3 text-fg-muted rounded px-2 py-1.5 pr-8 overflow-x-auto whitespace-pre-wrap break-words max-h-32 overflow-y-auto text-left">{currentGeneration.errorDetail}</pre>
							<div class="absolute top-1 right-1">
								<IconButton
									icon={copiedErrorDetail ? 'check' : 'copy'}
									label="Copy error"
									size="sm"
									class={copiedErrorDetail ? 'text-success' : ''}
									onclick={handleCopyErrorDetail}
								/>
							</div>
						</div>
					</details>
				{/if}

				{#if isAdmin && hasProfile}
					<div class="mt-4">
						<Button variant="secondary" size="sm" icon="gauge" onclick={() => (showProfileModal = true)}>
							View resource profile
						</Button>
					</div>
				{/if}
			</div>
		</div>
	{:else}
		<!-- Empty state -->
		<div class="relative flex items-center justify-center dot-grid group" style="height: {maxHeight}px">
			<div class="flex flex-col items-center justify-center h-full text-center px-8 select-none">
				<div class="w-20 h-20 rounded-2xl bg-surface-2/50 flex items-center justify-center mb-5">
					<svg class="w-10 h-10 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M3.75 21h16.5a1.5 1.5 0 001.5-1.5V5.25a1.5 1.5 0 00-1.5-1.5H3.75a1.5 1.5 0 00-1.5 1.5v14.25a1.5 1.5 0 001.5 1.5zm14.25-14.25h.008v.008h-.008V6.75z" />
					</svg>
				</div>
				<p class="text-fg-muted text-sm font-medium mb-1">Your creations appear here</p>
				<p class="text-fg-subtle text-xs max-w-[240px]">Configure your settings and hit Generate to create something</p>
			</div>

			<!-- Height slider - bottom right, appears on hover or while adjusting -->
			<div class="absolute bottom-4 right-4 z-20 transition-opacity duration-200 {isAdjustingHeight ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}">
				<div class="flex items-center gap-2 bg-black/50 backdrop-blur-sm rounded-lg px-3 py-2 shadow-lg">
					<svg class="w-4 h-4 text-white/70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
					</svg>
					<input
						type="range"
						min="550"
						max="1000"
						step="50"
						value={maxHeight}
						on:input={handleHeightChange}
						on:mousedown={() => isAdjustingHeight = true}
						on:mouseup={() => isAdjustingHeight = false}
						on:mouseleave={() => isAdjustingHeight = false}
						class="w-24 h-1.5 bg-white/20 rounded-lg appearance-none cursor-pointer accent-white"
					/>
					<span class="text-xs text-white/70 font-mono tabular-nums w-12">{maxHeight}px</span>
				</div>
			</div>
		</div>
	{/if}
</div>

<!-- Generation History Modal for Image Comparison -->
<GenerationHistoryModal
	isOpen={showCompareModal}
	onClose={() => showCompareModal = false}
	onSelect={displayFileType === 'video' ? handleSelectComparisonVideo : handleSelectComparisonImage}
	mediaType={displayFileType === 'video' ? 'video' : 'image'}
	title={displayFileType === 'video' ? 'Select Video for Comparison' : 'Select Image for Comparison'}
/>

<!-- Image/Video Preview Modal -->
{#if showImageModal && hasDisplayMedia}
	<!-- Portaled to <body>: Workbench mounts inside the mobile generate
	     carousel's transformed panel track, which would otherwise become the
	     containing block for this `position: fixed` fullscreen preview. -->
	<div
		use:portal
		class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm"
		on:click={() => showImageModal = false}
		on:keydown={(e) => e.key === 'Escape' && (showImageModal = false)}
		role="dialog"
		aria-modal="true"
		aria-label="Image preview"
		tabindex="-1"
	>
		<!-- Left Navigation Arrow -->
		{#if effectiveTotal > 1 && workbenchIndex > 0}
			<button
				on:click|stopPropagation={handlePrevious}
				class="absolute left-4 top-1/2 -translate-y-1/2 z-10 bg-black/50 hover:bg-black/70 text-white p-3 rounded-full shadow-lg backdrop-blur-sm transition-colors"
				aria-label="Previous item"
			>
				<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
				</svg>
			</button>
		{/if}

		<!-- Right Navigation Arrow -->
		{#if effectiveTotal > 1 && workbenchIndex < effectiveTotal - 1}
			<button
				on:click|stopPropagation={handleNext}
				class="absolute right-4 top-1/2 -translate-y-1/2 z-10 bg-black/50 hover:bg-black/70 text-white p-3 rounded-full shadow-lg backdrop-blur-sm transition-colors"
				aria-label="Next item"
			>
				<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
				</svg>
			</button>
		{/if}

		<!-- Ambient "screen extend" background echo behind the full-screen media.
		     A mesh has no flat 2D frame to blur (and, in gallery mode,
		     `displayImage` resolves to the gallery item's URL regardless of its
		     actual file type - it isn't image-gated the way `displayVideo` is -
		     so a mesh entry must be excluded explicitly here or it would try to
		     render its GLB URL as a broken <img>). -->
		{#if displayFileType !== 'audio' && displayFileType !== 'mesh' && (displayImage || displayVideo)}
			<div class="ambient-bg ambient-bg--modal" aria-hidden="true">
				{#if displayFileType === 'video' && displayVideo}
					<!-- svelte-ignore a11y-media-has-caption -->
					<video
						src={getDisplayUrl(displayVideo)}
						class="ambient-media"
						autoplay
						muted
						loop
						playsinline
						preload="auto"
					></video>
				{:else if displayImage}
					<img src={getDisplayUrl(displayImage)} alt="" class="ambient-media" />
				{/if}
			</div>
		{/if}

		<!-- Modal content container - 80% width -->
		<div
			class="relative w-[80vw] max-h-[90vh] flex items-center justify-center"
			on:click|stopPropagation
			on:keydown|stopPropagation
			role="document"
		>
			<!-- Close button -->
			<button
				on:click={() => showImageModal = false}
				class="absolute top-4 right-4 z-10 bg-black/50 hover:bg-black/70 text-white p-3 rounded-full shadow-lg backdrop-blur-sm transition-colors"
				title="Close (Esc)"
			>
				<svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>

			<!-- Media display -->
			{#if displayFileType === 'video' && displayVideo}
				<video
					src={getDisplayUrl(displayVideo)}
					class="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl"
					controls
					autoplay
					muted={isVideoMuted}
					loop
				></video>
			{:else if displayFileType === 'mesh' && displayMesh}
				<!-- MeshPreview is its own interactive viewer (wireframe, camera
				     presets, material inspector, ...), not a flat <img>/<video> -
				     it fills whatever box it's given rather than intrinsically
				     sizing itself, hence the explicit w/h here. -->
				<div class="w-[80vw] h-[80vh] max-h-[90vh]">
					<MeshPreview
						file={{ url: getDisplayUrl(displayMesh), originalUrl: getDisplayUrl(displayMesh) }}
					/>
				</div>
			{:else if displayImage}
				<img
					src={getDisplayUrl(displayImage)}
					alt="Full size preview"
					class="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl"
				/>
			{/if}

			<!-- Image metadata overlay -->
			{#if imageMetadata}
				<div class="absolute bottom-4 left-4 flex items-center gap-2">
					<span class="px-3 py-1.5 bg-black/70 backdrop-blur-sm text-white text-sm font-mono tabular-nums rounded shadow-lg">
						{imageMetadata.width} × {imageMetadata.height}
					</span>
					{#if imageMetadata.fileSize && imageMetadata.fileSize !== 'Unknown'}
						<span class="px-3 py-1.5 bg-black/70 backdrop-blur-sm text-white text-sm font-mono tabular-nums rounded shadow-lg">
							{imageMetadata.fileSize}
						</span>
					{/if}
					{#if imageMetadata.format}
						<span class="px-3 py-1.5 bg-black/70 backdrop-blur-sm text-white text-sm rounded shadow-lg">
							{imageMetadata.format}
						</span>
					{/if}
				</div>
			{/if}

			<!-- Navigation counter -->
			{#if effectiveTotal > 1}
				<div class="absolute top-4 left-4 bg-black/70 backdrop-blur-sm text-white px-4 py-2 rounded-full text-sm font-medium shadow-lg">
					<span class="font-mono text-xs tabular-nums">{workbenchIndex + 1} / {effectiveTotal}</span>
				</div>
			{/if}
		</div>
	</div>
{/if}

<!-- Generation Parameters Modal -->
<WorkbenchParametersModal
	isOpen={showParamsModal}
	params={fetchedParams}
	metadata={imageMetadata}
	index={workbenchIndex}
	on:close={() => (showParamsModal = false)}
/>

<!-- Resource Profile Modal (admin-only) -->
{#if isAdmin && showProfileModal}
	<WorkbenchProfileModal
		isOpen={showProfileModal}
		generationId={currentGeneration?.generation_id || currentGeneration?.id || null}
		on:close={() => (showProfileModal = false)}
	/>
{/if}

<style>
	/* Ambient "screen extend" background — a scaled, heavily-blurred echo of the current
	   media that projects its edge colors into the empty letterbox space behind it. */
	.ambient-bg {
		position: absolute;
		inset: 0;
		z-index: -1; /* below in-flow media, above the container's own background */
		overflow: hidden;
		pointer-events: none;
	}

	.ambient-media {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		/* Overscale so the blur never reveals a hard media edge at the container bounds. */
		transform: scale(1.35);
		filter: blur(56px) saturate(1.35);
		opacity: 0.55;
		will-change: transform;
		animation: ambient-in 0.5s var(--ease-out-quart, ease-out);
	}

	/* In-page floating halo. The box stays the same size as the media area (inset: 0) so it
	   adds no scrollable overflow; the heavy blur is "ink" overflow that spreads the color
	   past the box onto the page around the "floating" image/video without a scrollbar. */
	.ambient-glow {
		position: absolute;
		inset: 0;
		z-index: -1; /* behind the in-flow media, over the page background */
		pointer-events: none;
	}

	.ambient-glow-media {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		/* Large blur radius = how far the halo bleeds out; no transform/negative inset so
		   nothing contributes to the panel's scroll height. */
		filter: blur(90px) saturate(1.4);
		opacity: 0.6;
		animation: ambient-in 0.5s var(--ease-out-quart, ease-out);
	}

	/* In the full-screen preview the echo sits above the black scrim, so lift it a touch. */
	.ambient-bg--modal {
		z-index: 0;
	}

	.ambient-bg--modal .ambient-media {
		opacity: 0.6;
		filter: blur(72px) saturate(1.35);
	}

	@keyframes ambient-in {
		from {
			opacity: 0;
		}
		/* `to` is implicit — settles at each element's own opacity (0.55 / 0.6). */
	}

	@media (prefers-reduced-motion: reduce) {
		.ambient-media,
		.ambient-glow-media {
			animation: none;
		}
	}

	/* Spinner sits on black media-chrome (processing pill) — stays white in both themes to match. */
	.spinner {
		width: 16px;
		height: 16px;
		border: 2px solid rgb(255 255 255 / 0.3);
		border-top-color: rgb(255 255 255);
		border-radius: 50%;
		animation: spin 0.6s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
