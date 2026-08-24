<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import type { EditedMediaItem, UploadFileInfo } from '$lib/services/api/media';
	import { formatBytes } from '$lib/utils/format';
	import { resolvedTheme } from '$lib/stores/theme';
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import MediaPreviewModal from '$lib/components/modals/MediaPreviewModal.svelte';
	import MediaEditors from '$lib/media/editors/MediaEditors.svelte';
	import {
		hasEditor,
		type MediaEditorKind,
		type MediaEditorRequest,
		type MediaEditorResult
	} from '$lib/media/editors';
	import Waveform from '$lib/components/Waveform.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { maskSubjectKey, shouldClearMask } from './mediaLoaderMask';
	import { buildUploadedMediaItem, type UploadedMediaItem } from './mediaLoaderUpload';
	import { describeDropTarget, describeFormats, readMediaLoaderConfig, type MediaKind } from './mediaLoaderConfig';
	import { kindFromDeclared, kindFromFilename, kindFromMimeType, kindOfMediaItem } from './mediaLoaderKind';
	import {
		describeCandidate,
		evaluateCandidate,
		summarizeContents,
		type MediaCandidate
	} from './mediaLoaderAcceptance';
	import { probeMediaFile } from './mediaLoaderProbe';
	import { selectFace, toolbarTools, usesLanes, itemEditorFor, type MediaToolKey } from './mediaLoaderFaces';
	import { groupIntoLanes, isDropAllowed, moveWithinLane, NO_DRAG, type DragState } from './mediaLoaderReorder';
	import { durationBadge, metaChips, metaLine, type MediaItemMetadata } from './mediaLoaderMeta';
	import { EDITOR_ICON_PATHS, META_ICON_PATHS, TOOL_ICON_PATHS } from './mediaLoaderIcons';
	import { originFileIndex } from './mediaLoaderOrigin';
	import { buildEditedMediaItem } from './mediaLoaderEdited';

	// Props
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	// Provenance sibling, kept off the `onChange` value channel - see
	// setOriginKey below. Callers that don't need it (video director,
	// RelayTimeline, chat) simply don't pass it.
	export let onOriginChange: ((fieldName: string, origin: { generation_id: string; file_index: number } | undefined) => void) | undefined = undefined;
	// `${name}_inpaint_mask` sibling, same reasoning as onOriginChange above -
	// off the value channel entirely. Only reachable via preset-driven fields
	// with `configuration.allow_inpaint` (compact fields hide the button).
	// `undefined` clears it, exactly like onOriginChange.
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let autoPaste: boolean = false; // New prop to auto-activate paste mode
	export let compact: boolean = false; // Smaller variant for chat/embedded contexts
	export let compactFullWidth: boolean = false;
	// Stretch variant of `compact`: keeps the embedded chrome (no field-card,
	// no label header) but fills the host box in both axes and labels the
	// action buttons. Only meaningful together with `compact`.
	export let fill: boolean = false;
	// An OVERRIDE for the media editors, not a prerequisite: the field mounts
	// the shared surfaces itself (see $lib/media/editors), so every tool in the
	// toolbar works with no host wiring at all. A host that wants to own the
	// editing surface - to place it somewhere else, or to intercept the result -
	// passes this and gets the request instead.
	export let onOpenEditor: ((request: MediaEditorRequest) => void) | undefined = undefined;

	$: label = config.title || name || '';
	$: description = config.description || '';

	// Every declared limit, read once from whichever depth the schema emitted
	// it at - see mediaLoaderConfig.ts.
	$: limits = readMediaLoaderConfig(config);
	$: kinds = limits.kinds;
	$: soleKind = kinds.length === 1 ? kinds[0] : null;
	$: acceptedTypes = limits.accept;
	$: multiple = limits.multiple;
	$: maxItems = limits.maxItems;
	$: multiItems = multiple && Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
	$: atMax = maxItems != null && multiItems.length >= maxItems;
	$: laneMode = usesLanes(limits);
	$: lanes = groupIntoLanes(multiItems, kinds, (item) => kindOfMediaItem(item));

	// State
	let fileInput: HTMLInputElement;
	let isDragging = false;
	let isUploading = false;
	let uploadProgress = 0;
	let uploadingName: string | null = null;
	let uploadingSize: number | null = null;
	let previewUrl: string | null = null;
	let fileName: string | null = null;
	let fileType: MediaKind | null = null;
	let videoElement: HTMLVideoElement | null = null;
	let audioElement: HTMLAudioElement | null = null;

	// A refusal names the limit AND the value that tripped it - "too long" with
	// the file already out of the picker is untraceable. `detail` carries the
	// file's identity, `reason` the limit.
	// Every violation, not just the first - the server reports them all for a
	// submission, and a file that is both too big and too long should say so
	// once rather than over two attempts.
	let rejection: { reasons: string[]; detail: string | null } | null = null;

	function reject(reason: string, detail: string | null = null) {
		rejection = { reasons: [reason], detail };
	}

	// Best-effort probed metadata - resolution/size for images,
	// plus duration/fps for videos. null whenever the source (an older upload
	// or generation predating probing) has none, so nothing renders instead of
	// a placeholder.
	let mediaMetadata: MediaItemMetadata | null = null;

	$: face = selectFace({ multiple, uploading: isUploading, compact, previewUrl, fileType });
	$: previewChips =
		fileType && !compact ? metaChips(mediaMetadata, fileType, fileName, { edited: false }) : [];
	$: compactMetaLine = metaLine(mediaMetadata, fileType);

	// Fetch-when-missing: a populated value with no
	// `metadata` - a saved session, a pre-existing form value, anything set
	// before probing existed - has nothing to show until we resolve it. Cached
	// per raw path so repeated form re-renders (or flipping back to a
	// previously-seen value) don't refetch; `null` is cached too so a
	// permanently-unresolvable path (a file that's gone) isn't retried
	// every render.
	type MediaLocator =
		| { kind: 'generation'; generationId: string; filename: string }
		| { kind: 'upload'; filename: string };

	const metadataCache = new Map<string, MediaItemMetadata | null>();

	function currentRawPath(): string | null {
		if (value && typeof value === 'object') return value.relative_path || value.path || null;
		if (typeof value === 'string') return value;
		return null;
	}

	function parseMediaLocator(rawPath: string): MediaLocator | null {
		if (rawPath.includes('/tmp/')) return null; // temporary file, never persisted - nothing to resolve
		const segments = rawPath.split('/').filter((s) => s.length > 0);
		if (segments.length === 0) return null;
		const filename = segments[segments.length - 1];

		if (segments[0] === 'tmp') return null;
		if (rawPath.startsWith('/') || segments[0] === 'uploads') {
			return { kind: 'upload', filename };
		}
		if (segments.length >= 2) {
			// Relative generation-storage path, e.g. "generations/2024-01-01/<genId>/0.png".
			const generationId = segments[segments.length - 2];
			return { kind: 'generation', generationId, filename };
		}
		return null;
	}

	async function fetchMissingMetadata() {
		if (!fileType) return;
		if (mediaMetadata) return;

		const raw = currentRawPath();
		if (!raw) return;

		if (metadataCache.has(raw)) {
			mediaMetadata = metadataCache.get(raw) ?? null;
			return;
		}

		const locator = parseMediaLocator(raw);
		if (!locator) {
			metadataCache.set(raw, null);
			return;
		}

		// Reserve the key immediately so a second reactive fire before this
		// resolves doesn't launch a duplicate request.
		metadataCache.set(raw, null);

		try {
			let fetched: MediaItemMetadata | null = null;

			if (locator.kind === 'generation') {
				const response = await api.listGenerationMedia(locator.generationId);
				const match =
					response.success && response.data
						? response.data.media.find((m) => m.filename === locator.filename)
						: undefined;
				if (match) {
					fetched = {
						width: match.width,
						height: match.height,
						duration_seconds: match.duration_seconds,
						fps: match.fps,
						size: match.size
					};
				}
			} else {
				const response = await api.getUploadInfo(locator.filename);
				if (response.success && response.data) {
					fetched = {
						width: response.data.width,
						height: response.data.height,
						duration_seconds: response.data.duration_seconds,
						fps: response.data.fps,
						size: response.data.size
					};
				}
			}

			if (fetched) {
				metadataCache.set(raw, fetched);
				// Only apply if the field is still showing this exact value - guards
				// against a stale response landing after the user picked something else.
				if (raw === currentRawPath()) {
					mediaMetadata = fetched;
				}
			}
		} catch (error) {
			logger.error('Failed to fetch media metadata:', error);
		}
	}

	$: if (previewUrl && fileType && !mediaMetadata) {
		fetchMissingMetadata();
	}

	let isPasteActive = autoPaste; // Initialize from autoPaste prop
	let uploadAreaRef: HTMLDivElement;

	// Generation history modal state
	let showHistoryModal = false;

	// Upload library modal state
	let showUploadLibraryModal = false;

	// `maskSubject` is the identity of the image the held mask was painted on -
	// see mediaLoaderMask.ts. A mask outlives nothing: the moment the field
	// points at a different image it is dropped, or the new image would be
	// generated through the previous one's shape.
	let existingMaskUrl: string | null = null;
	let maskSubject: string | null = null;

	function clearMask() {
		if (maskSubject === null && existingMaskUrl === null) return;
		maskSubject = null;
		existingMaskUrl = null;
		if (name) {
			onMaskChange?.(name, undefined);
		}
	}

	// Catches every way the image can change, including a parent swapping the
	// value in from outside (session restore, a chat tool, the video director).
	$: if (shouldClearMask(maskSubject, value)) {
		clearMask();
	}

	// The open media editor, or null. Its `itemIndex` is the multi-item slot the
	// result belongs to - null in single-item mode - so a result written back
	// after the list was reordered still lands on the item it came from.
	let editorRequest: MediaEditorRequest | null = null;

	// Media preview modal state - carries the kind alongside the URL so the
	// same modal serves an image thumbnail, a video tile, and an audio tile
	// (multi mode) as well as the single-image face.
	let previewModal: { kind: MediaKind; url: string; label: string } | null = null;

	// Watch autoPaste prop changes. Multi mode has no single "current preview"
	// to gate on, so it's excluded rather than perpetually re-arming.
	$: if (autoPaste && !multiple && !previewUrl) {
		isPasteActive = true;
	}

	// Initialize from value if exists - using reactive assignment. Multi mode
	// keeps `value` as an ARRAY (see `multiItems` above) - this whole
	// single-preview sync is skipped for it, so `previewUrl`/`fileName`/
	// `fileType`/`mediaMetadata` (which the multi template never reads) just
	// stay at their initial null.
	$: if (!multiple) {
		if (value && typeof value === 'object') {
			// Object value with url, name, type, path properties
			previewUrl = value.url || null;
			fileName = value.name || null;
			fileType = kindFromDeclared(value.type) ?? kindOfMediaItem(value);
			mediaMetadata = value.metadata || null;
		} else if (value && typeof value === 'string') {
			// Legacy string value support - this is a file path from generation
			// history or an upload; both conventions resolve to a served URL.
			mediaMetadata = null;
			const pathParts = value.split('/');
			const filename = pathParts[pathParts.length - 1];

			if (!value.startsWith('/')) {
				// Relative path = generation media. The generation id is the
				// second-to-last segment.
				const segments = value.split('/').filter((s: string) => s);
				const generationId = segments[segments.length - 2];
				previewUrl = `/api/media/generations/${generationId}/${filename}`;
			} else if (value.includes('/tmp/')) {
				previewUrl = `/api/media/tmp/${filename}`;
			} else {
				previewUrl = `/api/media/uploads/${filename}`;
			}
			fileName = filename;
			fileType = kindFromFilename(filename);
		} else if (!value) {
			// Clear preview if value is null/undefined
			if (previewUrl && previewUrl.startsWith('blob:')) {
				URL.revokeObjectURL(previewUrl);
			}
			previewUrl = null;
			fileName = null;
			fileType = null;
			mediaMetadata = null;
		}
	}

	// --- Limits, enforced before anything is uploaded ---------------------

	function itemMetadata(item: unknown): MediaItemMetadata | null {
		if (!item || typeof item !== 'object') return null;
		const meta = (item as Record<string, unknown>).metadata;
		return meta && typeof meta === 'object' ? (meta as MediaItemMetadata) : null;
	}

	function itemDuration(item: unknown): number | null {
		const duration = itemMetadata(item)?.duration_seconds;
		return typeof duration === 'number' ? duration : null;
	}

	function currentContents() {
		const held = multiple ? multiItems : value ? [value] : [];
		return summarizeContents(held, (item) => kindOfMediaItem(item), itemDuration);
	}

	/**
	 * Gate for everything entering the field, whatever door it came through -
	 * a drop, a paste, a browse, a history pick, a library pick. A limit
	 * enforced on one path only is a limit the user learns about at random.
	 */
	function admit(candidate: MediaCandidate): boolean {
		const verdict = evaluateCandidate(candidate, limits, currentContents(), { fieldName: name });
		if (!verdict.accepted) {
			rejection = { reasons: verdict.reasons, detail: describeCandidate(candidate) };
			return false;
		}
		rejection = null;
		return true;
	}

	// Handle paste from clipboard
	function handlePaste(e: ClipboardEvent) {
		// Only handle paste if THIS component is active for pasting
		if (!isPasteActive) return;

		// Don't handle paste if we already have a file (single mode), we're at
		// the item cap (multi mode), or we're in a modal.
		if ((!multiple && previewUrl) || (multiple && atMax) || showHistoryModal || showUploadLibraryModal) return;

		// Prevent default immediately when paste mode is active to intercept the paste
		e.preventDefault();
		e.stopPropagation();

		const items = e.clipboardData?.items;
		if (!items) {
			reject('Unable to read the clipboard');
			return;
		}

		for (let i = 0; i < items.length; i++) {
			const item = items[i];
			if (item.type.indexOf('image') === -1) continue;

			const blob = item.getAsFile();
			if (blob) {
				const file = new File([blob], `pasted-image-${Date.now()}.png`, { type: blob.type });
				uploadFile(file);
				isPasteActive = false;
			}
			return;
		}

		reject('Nothing pasteable in the clipboard', 'Copy an image first');
	}

	// Handle click outside to deactivate paste
	function handleClickOutside(event: MouseEvent) {
		// Deactivate paste if clicking outside the upload area
		// BUT: Don't deactivate if clicking within a modal (z-50 or higher)
		const target = event.target as HTMLElement;
		const isInModal = target.closest('[class*="z-50"], [class*="z-[50]"], [class*="z-[60]"], [class*="z-[70]"], [class*="z-[80]"], [class*="z-[90]"], [class*="z-[100]"]');

		// If we're in a modal context and the click is within that modal, don't deactivate
		if (uploadAreaRef) {
			const uploadAreaModal = uploadAreaRef.closest('[class*="z-50"], [class*="z-[50]"], [class*="z-[60]"], [class*="z-[70]"], [class*="z-[80]"], [class*="z-[90]"], [class*="z-[100]"]');

			// Only deactivate if:
			// 1. Click is outside upload area AND
			// 2. Either we're not in a modal OR the click is outside our modal
			if (!uploadAreaRef.contains(event.target as Node)) {
				if (!uploadAreaModal || (uploadAreaModal && !uploadAreaModal.contains(target))) {
					isPasteActive = false;
				}
			}
		}
	}

	// Mount and unmount listeners
	onMount(() => {
		// Use capture phase to intercept paste before it reaches focused elements
		document.addEventListener('paste', handlePaste, true);
		document.addEventListener('mousedown', handleClickOutside);
	});

	onDestroy(() => {
		document.removeEventListener('paste', handlePaste, true);
		document.removeEventListener('mousedown', handleClickOutside);
	});

	// Handle file selection
	async function handleFileSelect(event: Event) {
		const target = event.target as HTMLInputElement;
		const file = target.files?.[0];
		// Cleared before awaiting: an input that still holds the file fires no
		// `change` when the same file is picked again, which is exactly what a
		// user does after a rejection they went away and fixed.
		target.value = '';
		if (file) {
			await uploadFile(file);
		}
	}

	// Handle drag and drop
	function handleDragEnter(event: DragEvent) {
		event.preventDefault();
		event.stopPropagation();
		isDragging = true;
	}

	function handleDragLeave(event: DragEvent) {
		event.preventDefault();
		event.stopPropagation();
		isDragging = false;
	}

	function handleDragOver(event: DragEvent) {
		event.preventDefault();
		event.stopPropagation();
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		event.stopPropagation();
		isDragging = false;

		const file = event.dataTransfer?.files?.[0];
		if (file) {
			uploadFile(file);
		}
	}

	/**
	 * POSTs through XHR rather than `fetch` for one reason: `fetch` reports no
	 * upload progress, and a 400 MB video behind a silent spinner is
	 * indistinguishable from a hung field.
	 */
	function postUpload(file: File): Promise<any> {
		return new Promise((resolve, promiseReject) => {
			const request = new XMLHttpRequest();
			request.open('POST', '/api/media/upload');
			request.withCredentials = true;

			const token = storage.get('auth_token');
			if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);

			request.upload.onprogress = (event) => {
				if (event.lengthComputable && event.total > 0) {
					uploadProgress = Math.min(100, Math.round((event.loaded / event.total) * 100));
				}
			};
			request.onload = () => {
				let parsed: any = null;
				try {
					parsed = JSON.parse(request.responseText);
				} catch {
					parsed = null;
				}
				if (request.status >= 200 && request.status < 300 && parsed?.success && parsed?.data) {
					resolve(parsed);
				} else {
					promiseReject(new Error(parsed?.message || 'Failed to upload file'));
				}
			};
			request.onerror = () => promiseReject(new Error('Failed to upload file'));

			const formData = new FormData();
			formData.append('file', file);
			request.send(formData);
		});
	}

	// Upload file to server
	// `replaceIndex` is the multi-item slot an edited version comes back into.
	// Appending an edited item instead of replacing it leaves the original in
	// the list AND changes the prompt order, which is the one thing the order
	// of this field is load-bearing for.
	async function uploadFile(file: File, replaceIndex: number | null = null) {
		const kind = kindFromMimeType(file.type) ?? kindFromFilename(file.name);
		const probed = await probeMediaFile(file, kind);
		const candidate: MediaCandidate = {
			name: file.name,
			kind,
			mimeType: file.type || null,
			sizeBytes: file.size,
			width: probed.width ?? null,
			height: probed.height ?? null,
			durationSeconds: probed.durationSeconds ?? null
		};
		// A replacement takes the slot it came from, so the item cap it is
		// already inside must not count against it.
		if (replaceIndex === null && !admit(candidate)) return;

		isUploading = true;
		uploadProgress = 0;
		uploadingName = file.name;
		uploadingSize = file.size;

		try {
			const result = await postUpload(file);
			const resolvedType = kind as MediaKind;
			const mediaItem = buildUploadedMediaItem(result.data, file.name, resolvedType);

			if (multiple) {
				if (replaceIndex === null) appendMultiItem(mediaItem);
				else replaceMultiItem(replaceIndex, mediaItem);
			} else if (name) {
				previewUrl = mediaItem.url;
				fileName = file.name;
				fileType = resolvedType;
				mediaMetadata = mediaItem.metadata;
				onChange(name, mediaItem);
				clearOriginKey();
				clearMask();
			}
		} catch (error: any) {
			logger.error('Upload error:', error);
			reject(error?.message || 'Failed to upload file', file.name);
			if (!multiple) {
				previewUrl = null;
				fileName = null;
				fileType = null;
				mediaMetadata = null;
			}
		} finally {
			isUploading = false;
			uploadingName = null;
			uploadingSize = null;
			uploadProgress = 0;
		}
	}

	// A resolved-from-generation value carries provenance (`{generation_id,
	// file_index}`) so a later params lookup can attribute back to the exact
	// source frame. Anything else - a fresh upload, a library pick, or a
	// clear - must remove it; a stale origin left on a swapped-in image would
	// attribute the wrong generation's params.
	//
	// This travels through its own callback, never through `onChange` - a
	// consumer that keys its value-update logic by field name has no way to
	// distinguish "the real value" from "provenance for the real value" on
	// the same channel, and three different consumers (chat, the video
	// director composers, RelayTimeline) each got that wrong at least once.
	// `onOriginChange` makes the mistake structurally impossible: nothing
	// shaped like an origin can ever reach `onChange`.
	// A negative index means the picked file could not be located in the
	// generation's own `files` array: emit no provenance rather than a
	// position that would attribute some other file's params.
	function setOriginKey(generationId: string, fileIndex: number) {
		if (!name) return;
		if (fileIndex < 0) {
			onOriginChange?.(name, undefined);
			return;
		}
		onOriginChange?.(name, { generation_id: generationId, file_index: fileIndex });
	}

	function clearOriginKey() {
		if (name) {
			onOriginChange?.(name, undefined);
		}
	}

	// Clear selected file
	function handleClear() {
		if (previewUrl && previewUrl.startsWith('blob:')) {
			URL.revokeObjectURL(previewUrl);
		}
		previewUrl = null;
		fileName = null;
		fileType = null;
		mediaMetadata = null;
		rejection = null;
		if (fileInput) {
			fileInput.value = '';
		}
		if (name) {
			onChange(name, null);
		}
		clearOriginKey();
		clearMask();
	}

	// Which lane a picker was opened from, so a field that takes several kinds
	// still opens a picker filtered to the one lane the user pressed. Null for
	// the single grid and for single-item mode, where the whole accepted set
	// applies.
	let pendingKind: MediaKind | null = null;

	// The `accept` attribute is set on the element rather than through the
	// template: `.click()` happens in the same tick, before Svelte has flushed
	// a reactive attribute update, so a templated value would open the picker
	// with the PREVIOUS lane's filter.
	function openFilePicker(kind: MediaKind | null = null) {
		pendingKind = kind;
		if (fileInput) fileInput.accept = kind ? `${kind}/*` : acceptedTypes;
		fileInput?.click();
	}

	// --- Tools under the preview -----------------------------------------

	$: singleTools =
		fileType && previewUrl
			? toolbarTools(fileType, {
					allowInpaint: limits.allowInpaint,
					compact: compact && !fill,
					canEmitMask: onMaskChange !== undefined
				})
			: [];

	function openEditor(kind: MediaEditorKind, item: unknown, index: number | null) {
		const mediaKind = kindOfMediaItem(item);
		if (!mediaKind || !hasEditor(kind, mediaKind)) return;

		const record = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
		const meta = itemMetadata(item);
		const request: MediaEditorRequest = {
			kind,
			source: {
				url: (record.url as string) || previewUrl || '',
				kind: mediaKind,
				fileName: (record.name as string) || fileName || 'Media',
				storedPath: (record.relative_path as string) || (record.path as string) || null,
				width: meta?.width ?? null,
				height: meta?.height ?? null,
				durationSeconds: meta?.duration_seconds ?? null,
				fps: meta?.fps ?? null
			},
			itemIndex: index
		};

		// A host that wants to own the surface - to place it elsewhere, or to
		// intercept the result - takes the request instead. Otherwise the field
		// mounts the shared editors itself, so every tool works unwired.
		if (onOpenEditor) onOpenEditor(request);
		else editorRequest = request;
	}

	/**
	 * What comes back from an editor.
	 *
	 * Three shapes, one rule: nothing that lands in `value` is ever a blob
	 * handle. An edit is performed by the server and already carries the served
	 * URL of the resource(s) it produced; a mask is not a value at all and goes
	 * to its own sibling channel as a stored path.
	 */
	async function handleEditorResult(result: MediaEditorResult, request: MediaEditorRequest) {
		if (result.type === 'mask') {
			applyMaskPath(result.maskPath);
			return;
		}
		if (result.type === 'items') {
			applySplitItems(result.items, request.itemIndex);
			return;
		}
		applyEditedItem(buildEditedMediaItem(result.item), request.itemIndex);
	}

	/** An edit the server performed, written back to the slot it came from. */
	function applyEditedItem(mediaItem: ReturnType<typeof buildEditedMediaItem>, target: number | null) {
		if (!name) return;

		if (multiple) {
			if (target !== null) replaceMultiItem(target, mediaItem);
			return;
		}

		previewUrl = mediaItem.url;
		fileName = mediaItem.name;
		fileType = mediaItem.type;
		mediaMetadata = mediaItem.metadata;
		onChange(name, mediaItem);
		clearOriginKey();
		clearMask();
	}

	/**
	 * A split, written back to the slot it was performed on.
	 *
	 * In multi-item mode the slot the original occupied becomes the N parts,
	 * in order - the same "stay where the source was" rule `uploadFile` follows
	 * for a single replacement, generalised to several. A single-value field has
	 * exactly one slot, which cannot hold several parts at once; the first part
	 * takes it, and the rest remain real library resources the field simply has
	 * no room to reference - nothing is discarded, only left unattached.
	 */
	function applySplitItems(items: EditedMediaItem[], target: number | null) {
		if (!name || items.length === 0) return;
		const built = items.map(buildEditedMediaItem);

		if (multiple) {
			const next: (Record<string, unknown> | UploadedMediaItem)[] = [...multiItems];
			if (target !== null && target >= 0 && target < next.length) {
				next.splice(target, 1, ...built);
			} else {
				next.push(...built);
			}
			onChange(name, next);
			return;
		}

		if (built.length > 1) {
			logger.warn(
				`Split produced ${built.length} parts; this field only holds one, so the rest were left in the library.`
			);
		}
		applyEditedItem(built[0], null);
	}

	function runTool(tool: MediaToolKey) {
		switch (tool) {
			case 'crop':
				openEditor('crop', value, null);
				break;
			case 'mask':
				openEditor('mask', value, null);
				break;
			case 'trim':
				openEditor('trim', value, null);
				break;
			case 'frame':
				openEditor('frame', value, null);
				break;
			case 'full':
				// Video and audio already play inline (a live `<video>`/`<audio>`
				// element, not a static thumbnail) - only the image face has the
				// "small dead preview" problem the modal solves.
				if (fileType === 'image') {
					previewModal = { kind: 'image', url: previewUrl || '', label: fileName || 'Preview' };
				} else {
					videoElement?.requestFullscreen?.();
				}
				break;
			case 'swap':
				openFilePicker();
				break;
			case 'remove':
				handleClear();
				break;
		}
	}

	// --- Multi-item mode helpers ---
	// `value` IS the source of truth (via `multiItems` above) - every
	// mutation below builds a new array and commits it through `onChange`
	// rather than keeping separate local list state.

	const MAX_LABEL_LENGTH = 64;

	function appendMultiItem(item: Record<string, unknown> | UploadedMediaItem) {
		if (!name || atMax) return;
		onChange(name, [...multiItems, item]);
	}

	// Keeps the edited item's label - it names the slot, not the pixels.
	function replaceMultiItem(index: number, item: Record<string, unknown> | UploadedMediaItem) {
		if (!name) return;
		const next = multiItems.map((existing: Record<string, unknown>, i: number) =>
			i === index ? { ...item, ...(existing.label ? { label: existing.label } : {}) } : existing
		);
		onChange(name, next);
	}

	function removeMultiItem(index: number) {
		if (!name) return;
		onChange(name, multiItems.filter((_: unknown, i: number) => i !== index));
	}

	function clearAllItems() {
		if (!name) return;
		onChange(name, []);
	}

	function updateMultiLabel(index: number, label: string) {
		if (!name) return;
		const cleaned = label.slice(0, MAX_LABEL_LENGTH);
		const next = multiItems.map((item: Record<string, unknown>, i: number) =>
			i === index ? { ...item, label: cleaned } : item
		);
		onChange(name, next);
	}

	// --- Drag to reorder --------------------------------------------------
	// Order is the prompt order, so a drop that lands a slot off silently
	// changes what the model is told - the arithmetic lives in
	// mediaLoaderReorder.ts where it can be asserted.

	let drag: DragState = NO_DRAG;

	function laneKeyFor(kind: MediaKind | null): string {
		return laneMode ? (kind ?? 'other') : 'all';
	}

	function laneIndicesFor(kind: MediaKind | null): number[] {
		if (!laneMode) return multiItems.map((_, i) => i);
		return lanes.find((lane) => lane.kind === kind)?.indices ?? [];
	}

	function startDrag(event: DragEvent, laneKind: MediaKind | null, laneIndex: number) {
		drag = { laneKey: laneKeyFor(laneKind), fromIndex: laneIndex, overIndex: laneIndex };
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			// Firefox refuses to start a drag without payload.
			event.dataTransfer.setData('text/plain', String(laneIndex));
		}
	}

	function dragOverTile(event: DragEvent, laneKind: MediaKind | null, laneIndex: number) {
		if (drag.laneKey !== laneKeyFor(laneKind)) return;
		event.preventDefault();
		if (drag.overIndex !== laneIndex) drag = { ...drag, overIndex: laneIndex };
	}

	function dropOnTile(event: DragEvent, laneKind: MediaKind | null, laneIndex: number) {
		event.preventDefault();
		event.stopPropagation();
		const laneKey = laneKeyFor(laneKind);
		if (!isDropAllowed(drag, laneKey, laneIndex) || !name) {
			drag = NO_DRAG;
			return;
		}
		onChange(name, moveWithinLane(multiItems, laneIndicesFor(laneKind), drag.fromIndex as number, laneIndex));
		drag = NO_DRAG;
	}

	function endDrag() {
		drag = NO_DRAG;
	}

	/**
	 * Keyboard equivalent of the drag. Dragging is the only affordance the
	 * design draws, but reordering by pointer alone would put the field's one
	 * order-carrying interaction out of reach of the keyboard.
	 */
	function nudgeItem(laneKind: MediaKind | null, laneIndex: number, direction: -1 | 1) {
		if (!name) return;
		const indices = laneIndicesFor(laneKind);
		const target = laneIndex + direction;
		if (target < 0 || target >= indices.length) return;
		onChange(name, moveWithinLane(multiItems, indices, laneIndex, target));
	}

	function handleGripKeydown(event: KeyboardEvent, laneKind: MediaKind | null, laneIndex: number) {
		const back = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
		const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
		if (!back && !forward) return;
		event.preventDefault();
		nudgeItem(laneKind, laneIndex, back ? -1 : 1);
	}

	function itemDisplayUrl(item: Record<string, unknown> | null | undefined): string {
		const url = item && typeof item === 'object' ? (item.url as string | undefined) : undefined;
		return url || '';
	}

	// Peek at a multi tile full-size - every kind, not just image, since the
	// grid tile is a small square crop (or a bare icon for audio) either way.
	function openTilePreview(tile: { kind: MediaKind | null; url: string; label: string; alt: string }) {
		if (!tile.kind || !tile.url) return;
		previewModal = { kind: tile.kind, url: tile.url, label: tile.label || tile.alt };
	}

	// Everything a tile renders, resolved in one place rather than through
	// `{@const}` calls in the markup: a `{@const}` that calls a function reads
	// its state untracked, so a tile's drag highlight would freeze at whatever
	// it was on first render.
	$: laneViews = (laneMode
		? lanes
		: [{ kind: kinds[0], items: multiItems, indices: multiItems.map((_, i: number) => i) }]
	).map((lane) => {
		const laneKey = laneKeyFor(lane.kind);
		return {
			kind: lane.kind,
			laneKey,
			count: lane.items.length,
			tiles: lane.items.map((item: Record<string, unknown>, laneIndex: number) => {
				const itemKind = kindOfMediaItem(item);
				const meta = itemMetadata(item);
				return {
					item,
					laneIndex,
					flatIndex: lane.indices[laneIndex],
					kind: itemKind,
					url: itemDisplayUrl(item),
					label: (item.label as string) || '',
					alt: (item.name as string) || `Item ${laneIndex + 1}`,
					editor: itemEditorFor(itemKind),
					metaLine: metaLine(meta, itemKind),
					duration: durationBadge(meta, itemKind),
					dragging: drag.laneKey === laneKey && drag.fromIndex === laneIndex,
					over: isDropAllowed(drag, laneKey, laneIndex) && drag.overIndex === laneIndex
				};
			})
		};
	});

	// Handle file selection from generation. The picker hands over the file
	// RECORD, not a position: the card's carousel is filtered and re-ordered, so
	// an index into it addresses a different element of `generation.files`.
	function handleSelectFromGeneration(generation: any, file: any) {
		if (!file) return;

		const filename = file.file_path.split('/').pop();
		const kind = kindFromDeclared(file.file_type) ?? kindFromFilename(filename);
		const metadata: MediaItemMetadata = {
			width: file.width,
			height: file.height,
			duration_seconds: file.duration_seconds,
			fps: file.fps,
			size: file.file_size
		};

		if (
			!admit({
				name: filename || 'file',
				kind,
				mimeType: null,
				sizeBytes: file.file_size ?? null,
				width: file.width ?? null,
				height: file.height ?? null,
				durationSeconds: file.duration_seconds ?? null
			})
		) {
			return;
		}

		try {
			// Extract generation ID from path (second-to-last segment)
			// Path: "outputs/2025-10-10/01K77DF21Z2TH1YXS7NMTVR4CE/0.png"
			const segments = file.file_path.split('/').filter((s: string) => s);
			const generationId = segments[segments.length - 2];

			const apiUrl = `/api/media/generations/${generationId}/${filename}`;
			const resolvedName = filename || `media.${kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : 'png'}`;
			const mediaItem = {
				path: file.file_path,
				relative_path: file.file_path,
				url: apiUrl,
				name: resolvedName,
				type: kind,
				metadata
			};

			if (multiple) {
				// No per-item origin channel (`onOriginChange` carries a single
				// origin) - a multi item picked from generation history has no
				// provenance sibling.
				appendMultiItem(mediaItem);
			} else if (name) {
				previewUrl = apiUrl;
				fileName = resolvedName;
				fileType = kind;
				mediaMetadata = metadata;
				onChange(name, mediaItem);
				setOriginKey(generation.id, originFileIndex(generation, file));
				clearMask();
			}

			showHistoryModal = false;
		} catch (error) {
			logger.error('Failed to load media from generation:', error);
			reject('Failed to load media from generation', filename ?? null);
		}
	}

	// Open history modal
	function openHistoryModal(kind: MediaKind | null = null) {
		pendingKind = kind;
		showHistoryModal = true;
	}

	// Handle file selection from the user's upload library
	function handleSelectFromUpload(upload: UploadFileInfo) {
		const kind = kindFromDeclared(upload.media_type) ?? kindFromFilename(upload.filename);
		// `original_filename` is the library's own display name (see
		// libraryItemDisplayName) - untrimmed/empty is treated as absent rather
		// than falling back to a placeholder that would masquerade as a label.
		const libraryDisplayName = (upload.original_filename ?? '').trim();
		const resolvedName = libraryDisplayName || 'Upload';
		const metadata: MediaItemMetadata = {
			width: upload.width,
			height: upload.height,
			duration_seconds: upload.duration_seconds,
			fps: upload.fps,
			size: upload.size
		};

		// The library is already filtered server-side by `mediaType`; this is
		// the same gate every other door goes through, so a mixed-kind library
		// pick meets the same limits a drop does.
		if (
			!admit({
				name: resolvedName,
				kind,
				mimeType: null,
				sizeBytes: upload.size ?? null,
				width: upload.width ?? null,
				height: upload.height ?? null,
				durationSeconds: upload.duration_seconds ?? null
			})
		) {
			return;
		}

		// Same relative-path convention `upload_media` already writes on a
		// fresh upload ("uploads/<filename>") - so a picked library item is
		// indistinguishable from a just-uploaded file to every downstream
		// consumer of this field's value (parseMediaLocator above included).
		const relativePath = `uploads/${upload.filename}`;
		const mediaItem = {
			path: relativePath,
			relative_path: relativePath,
			url: upload.url,
			name: resolvedName,
			type: kind,
			metadata,
			// A fresh pick always starts unlabeled - only set it here, never on a
			// path that could be overwriting a label the user already typed.
			...(libraryDisplayName ? { label: libraryDisplayName } : {})
		};

		if (multiple) {
			appendMultiItem(mediaItem);
		} else if (name) {
			previewUrl = upload.url;
			fileName = resolvedName;
			fileType = kind;
			mediaMetadata = metadata;
			onChange(name, mediaItem);
			clearOriginKey();
			clearMask();
		}

		showUploadLibraryModal = false;
	}

	// Open upload library modal
	function openUploadLibraryModal(kind: MediaKind | null = null) {
		pendingKind = kind;
		showUploadLibraryModal = true;
	}

	/**
	 * A painted mask, already stored by the editor and named by its PATH.
	 *
	 * The path is what travels - `maskSubjectKey` binds the mask to the image it
	 * was painted on by path too, so both ends of the pairing survive a refresh.
	 * The mask never touches the value channel.
	 */
	function applyMaskPath(maskPath: string) {
		if (!name) return;
		onMaskChange?.(name, maskPath);
		// Held so reopening the editor resumes the mask rather than starting over.
		existingMaskUrl = `/api/media/serve?path=${encodeURIComponent(maskPath)}`;
		// Bind the mask to the image it was painted on, so replacing that image
		// drops it (see the reactive guard above).
		maskSubject = maskSubjectKey(value);
	}

	// --- Presentation helpers --------------------------------------------

	const KIND_ICON: Record<MediaKind, string> = { image: 'image', video: 'video', audio: 'audio' };
	const KIND_LABEL: Record<MediaKind, string> = { image: 'Images', video: 'Video', audio: 'Audio' };
	const KIND_ADD_LABEL: Record<MediaKind, string> = {
		image: 'Add image',
		video: 'Add video',
		audio: 'Add audio'
	};

	// History has no audio filter (GenerationHistoryModal's `mediaType`, and
	// the list API's `media_type`, are image/video only), so an audio-only
	// field has no history door to offer.
	$: offersHistory = !(soleKind === 'audio');

	// A picker filters to the lane it was opened from when there is one, and
	// otherwise to the field's kind when the field takes exactly one.
	$: pickerKind = pendingKind ?? soleKind;
	$: historyPickerKind = pickerKind === 'image' || pickerKind === 'video' ? pickerKind : undefined;

	$: emptyHint = `Drop ${describeDropTarget(kinds)} here`;
	$: formatsHint = describeFormats(kinds);
	$: sizeHint = limits.maxFileSizeBytes ? `MAX ${formatBytes(limits.maxFileSizeBytes, 0)}` : null;

	// The waveform draws to a canvas, which cannot read a CSS variable - so the
	// same semantic tokens the rest of the field uses are resolved to concrete
	// values here. The re-read is deferred a frame because the theme store sets
	// `data-theme` and this statement can run before the style recalculation
	// that follows it, which would leave the canvas painted in the old theme.
	function readWaveformPalette() {
		if (typeof window === 'undefined') return waveformPalette;
		const styles = getComputedStyle(document.documentElement);
		const token = (name: string, fallback: string) => {
			const raw = styles.getPropertyValue(name).trim();
			return raw ? `rgb(${raw})` : fallback;
		};
		return {
			waveColor: token('--line-strong', 'rgb(120 120 120)'),
			progressColor: token('--signal', 'rgb(77 159 255)'),
			cursorColor: token('--fg', 'rgb(230 230 230)')
		};
	}

	let waveformPalette = {
		waveColor: 'rgb(120 120 120)',
		progressColor: 'rgb(77 159 255)',
		cursorColor: 'rgb(230 230 230)'
	};

	// `theme` is the dependency this refresh exists for, not an input.
	function refreshWaveformPalette(theme: string) {
		if (!theme || typeof requestAnimationFrame !== 'function') {
			waveformPalette = readWaveformPalette();
			return;
		}
		requestAnimationFrame(() => {
			waveformPalette = readWaveformPalette();
		});
	}

	$: refreshWaveformPalette($resolvedTheme);

	$: waveformConfig = {
		height: 56,
		backgroundColor: 'transparent',
		barWidth: 2,
		barGap: 1,
		barRadius: 1,
		...waveformPalette
	};
</script>

<!-- The whole field is the paste-arming region: a click anywhere inside it
     keeps paste armed, a click outside disarms it. Binding the ref here rather
     than on the dropzone also survives the field having several dropzones (one
     per lane). -->
<div bind:this={uploadAreaRef} class="{compact ? (fill ? 'h-full flex flex-col' : '') : 'field-card'}">
	{#if !compact}
		<div class="flex items-baseline gap-2">
			{#if label}
				<label class="label" for={name || undefined}>{label}</label>
			{/if}
			<div class="flex-1"></div>
			{#if multiple && maxItems != null}
				<span class="font-mono text-2xs tabular-nums {atMax ? 'text-warning' : 'text-fg-subtle'}">
					{multiItems.length}/{maxItems}
				</span>
			{/if}
		</div>
	{/if}

	{#if description && !compact}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mb-2">{description}</p>
	{/if}

	{#if multiple && !compact && multiItems.length > 1}
		<p class="mb-2 text-xs text-fg-subtle">Order is the prompt order — item 1 is referenced first.</p>
	{/if}

	<!-- Face 09 · Rejected. Names the limit and the value that tripped it, and
	     stays until dismissed or superseded - a toast would be gone before the
	     user got back from the file picker. -->
	{#if rejection}
		<div class="flex items-start gap-2 mb-2 px-2.5 py-2 rounded bg-danger/10 ring-1 ring-inset ring-danger/30">
			<Icon name="warning" className="w-3.5 h-3.5 shrink-0 mt-0.5 text-danger" />
			<div class="min-w-0 flex-1">
				{#each rejection.reasons as reason (reason)}
					<p class="text-xs text-danger">{reason}</p>
				{/each}
				{#if rejection.detail}
					<p class="mt-0.5 font-mono text-2xs text-danger/80 truncate" title={rejection.detail}>
						{rejection.detail}
					</p>
				{/if}
			</div>
			<button
				type="button"
				class="w-5 h-5 shrink-0 inline-flex items-center justify-center rounded text-danger hover:bg-danger/15 transition-colors"
				on:click={() => (rejection = null)}
				title="Dismiss"
			>
				<Icon name="close" className="w-3 h-3" />
			</button>
		</div>
	{/if}

	{#if face === 'multi'}
		<!-- Faces 06 / 07 · Multi. One lane per accepted kind when the field
		     takes more than one, a single grid when it doesn't. -->
		{#each laneViews as lane (lane.laneKey)}
			<div class="mb-3 last:mb-0">
				{#if laneMode}
					<div class="flex items-center gap-1.5 mb-1.5">
						<Icon name={KIND_ICON[lane.kind]} className="w-3 h-3 shrink-0 text-fg-subtle" />
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">
							{KIND_LABEL[lane.kind]}
						</span>
						<span class="font-mono text-2xs tabular-nums text-fg-subtle">{lane.count}</span>
						<div class="flex-1 h-px bg-line"></div>
						<span class="font-mono text-2xs text-fg-subtle">{describeFormats([lane.kind])}</span>
					</div>
				{/if}

				<div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
					{#each lane.tiles as tile (tile.flatIndex)}
						<div
							draggable="true"
							role="group"
							aria-label="Item {tile.laneIndex + 1}"
							data-media-tile={tile.flatIndex}
							on:dragstart={(e) => startDrag(e, lane.kind, tile.laneIndex)}
							on:dragover={(e) => dragOverTile(e, lane.kind, tile.laneIndex)}
							on:drop={(e) => dropOnTile(e, lane.kind, tile.laneIndex)}
							on:dragend={endDrag}
							class="relative rounded-lg overflow-hidden bg-surface-2 ring-1 ring-inset transition-transform duration-100 {tile.over
								? 'ring-signal translate-x-0.5'
								: 'ring-line'} {tile.dragging ? 'opacity-45' : ''}"
						>
							{#if tile.over}
								<div class="absolute left-0 inset-y-0 w-[3px] bg-signal z-10"></div>
							{/if}

							<!-- svelte-ignore a11y-no-static-element-interactions -->
							<div
								class="relative aspect-square bg-surface-3 dot-grid flex items-center justify-center"
								title={tile.label || tile.alt}
								on:dblclick={(e) => {
									e.preventDefault();
									openTilePreview(tile);
								}}
							>
								{#if tile.kind === 'image' && tile.url}
									<img src={tile.url} alt={tile.alt} class="w-full h-full object-cover" />
								{:else if tile.kind === 'video' && tile.url}
									<!-- svelte-ignore a11y-media-has-caption -->
									<video src={tile.url} class="w-full h-full object-cover" preload="metadata" muted></video>
								{:else}
									<Icon name={tile.kind ? KIND_ICON[tile.kind] : 'image'} className="w-6 h-6 text-fg-subtle" />
								{/if}

								<span
									class="absolute top-1.5 left-1.5 min-w-[18px] h-[18px] px-1 inline-flex items-center justify-center rounded bg-canvas/75 font-mono text-2xs font-semibold tabular-nums text-fg"
								>
									{tile.laneIndex + 1}
								</span>

								<button
									type="button"
									class="absolute top-1.5 right-1.5 w-5 h-5 inline-flex items-center justify-center rounded bg-canvas/75 text-fg-muted hover:text-fg cursor-grab active:cursor-grabbing"
									title="Drag to reorder — or use the arrow keys"
									aria-label="Reorder item {tile.laneIndex + 1}"
									on:keydown={(e) => handleGripKeydown(e, lane.kind, tile.laneIndex)}
									on:dblclick|stopPropagation
								>
									<Icon name="grip" className="w-3 h-3" />
								</button>

								{#if tile.duration}
									<span
										class="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded bg-canvas/75 font-mono text-2xs tabular-nums text-fg"
									>
										{tile.duration}
									</span>
								{/if}

								{#if tile.url}
									<button
										type="button"
										class="absolute bottom-1.5 right-1.5 w-5 h-5 inline-flex items-center justify-center rounded bg-canvas/75 text-fg-muted hover:text-fg"
										title="View full size"
										aria-label="View item {tile.laneIndex + 1} full size"
										on:click|stopPropagation={() => openTilePreview(tile)}
										on:dblclick|stopPropagation
									>
										<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="1.8"
												d={TOOL_ICON_PATHS.full}
											/>
										</svg>
									</button>
								{/if}
							</div>

							<div class="flex items-center gap-0.5 px-1.5 py-1 border-t border-line bg-surface-2">
								<input
									type="text"
									value={tile.label}
									placeholder="Label"
									maxlength={MAX_LABEL_LENGTH}
									aria-label="Label for item {tile.laneIndex + 1}"
									class="min-w-0 flex-1 bg-transparent text-xs text-fg placeholder:text-fg-subtle focus:outline-none"
									on:input={(e) => updateMultiLabel(tile.flatIndex, e.currentTarget.value)}
								/>
								{#if tile.editor}
									{@const editor = tile.editor}
									<button
										type="button"
										class="w-5 h-5 shrink-0 inline-flex items-center justify-center rounded text-fg-muted hover:bg-surface-3 hover:text-fg transition-colors"
										title={editor.title}
										aria-label={editor.title}
										on:click={() => openEditor(editor.key, tile.item, tile.flatIndex)}
									>
										<svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
											<path
												stroke-linecap="round"
												stroke-linejoin="round"
												stroke-width="1.8"
												d={EDITOR_ICON_PATHS[editor.key]}
											/>
										</svg>
									</button>
								{/if}
								<button
									type="button"
									class="w-5 h-5 shrink-0 inline-flex items-center justify-center rounded text-fg-subtle hover:bg-danger/15 hover:text-danger transition-colors"
									title="Remove"
									aria-label="Remove item {tile.laneIndex + 1}"
									on:click={() => removeMultiItem(tile.flatIndex)}
								>
									<Icon name="close" className="w-3 h-3" />
								</button>
							</div>

							{#if tile.metaLine}
								<p class="px-2 pb-1.5 font-mono text-2xs tabular-nums text-fg-subtle truncate">
									{tile.metaLine}
								</p>
							{/if}
						</div>
					{/each}

					{#if !atMax}
						<!-- Add tile - the same four doors the empty face offers, at
						     tile size. -->
						<div
							class="flex flex-col items-center justify-center gap-1.5 min-h-[104px] p-2 rounded-lg border border-dashed transition-colors cursor-pointer {isDragging
								? 'border-signal bg-signal/10'
								: isPasteActive
									? 'border-signal bg-signal/10'
									: 'border-line-strong bg-surface-1 hover:border-line-hover'}"
							on:dragenter={handleDragEnter}
							on:dragleave={handleDragLeave}
							on:dragover={handleDragOver}
							on:drop={handleDrop}
							on:click={() => (isPasteActive = true)}
							role="button"
							tabindex="0"
							on:keydown={(e) => e.key === 'Enter' && openFilePicker(laneMode ? lane.kind : null)}
						>
							{#if isUploading}
								<div class="spinner-sm"></div>
								<span class="font-mono text-2xs tabular-nums text-fg-muted">{uploadProgress}%</span>
							{:else}
								<span class="text-xs text-fg-muted text-center">{KIND_ADD_LABEL[lane.kind]}</span>
								<div class="flex items-center justify-center gap-1">
									<button
										type="button"
										class="w-6 h-6 inline-flex items-center justify-center border border-line-strong bg-surface-2 rounded text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
										on:click|stopPropagation={() => openFilePicker(laneMode ? lane.kind : null)}
										title="Browse files"
									>
										<Icon name="folder" className="w-3.5 h-3.5" />
									</button>
									{#if lane.kind === 'image'}
										<button
											type="button"
											class="w-6 h-6 inline-flex items-center justify-center border border-line-strong bg-surface-2 rounded text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
											on:click|stopPropagation={() => (isPasteActive = true)}
											title="Paste from clipboard"
										>
											<Icon name="clipboard-list" className="w-3.5 h-3.5" />
										</button>
									{/if}
									{#if lane.kind !== 'audio'}
										<button
											type="button"
											class="w-6 h-6 inline-flex items-center justify-center border border-line-strong bg-surface-2 rounded text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
											on:click|stopPropagation={() => openHistoryModal(laneMode ? lane.kind : null)}
											title="Pick from generation history"
										>
											<Icon name="clock" className="w-3.5 h-3.5" />
										</button>
									{/if}
									<button
										type="button"
										class="w-6 h-6 inline-flex items-center justify-center border border-line-strong bg-surface-2 rounded text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
										on:click|stopPropagation={() => openUploadLibraryModal(laneMode ? lane.kind : null)}
										title="Pick from the library"
									>
										<Icon name="grid" className="w-3.5 h-3.5" />
									</button>
								</div>
								<span class="font-mono text-2xs text-fg-subtle">or drop here</span>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		{/each}

		{#if atMax && maxItems != null}
			<div
				class="flex items-center gap-2 mt-2 px-2.5 py-2 rounded bg-warning/10 ring-1 ring-inset ring-warning/25"
			>
				<Icon name="warning" className="w-3.5 h-3.5 shrink-0 text-warning" />
				<span class="flex-1 text-xs text-warning">
					All {maxItems} slots used. Remove one to add another.
				</span>
				<button
					type="button"
					class="shrink-0 text-xs text-warning hover:underline"
					on:click={clearAllItems}
				>
					Clear all
				</button>
			</div>
		{/if}

		<input
			bind:this={fileInput}
			type="file"
			on:change={handleFileSelect}
			accept={acceptedTypes}
			class="hidden"
		/>
	{:else if face === 'uploading'}
		<!-- Face 08 · Uploading. The percentage is the point: a large video
		     behind a bare spinner is indistinguishable from a hung field. -->
		<div
			class="rounded-lg border border-line-strong overflow-hidden bg-canvas {compact
				? fill
					? 'w-full flex-1 min-h-0 flex flex-col'
					: compactFullWidth
						? 'w-full'
						: 'max-w-[200px]'
				: ''}"
		>
			<div class="{fill ? 'flex-1 min-h-[120px]' : 'h-[120px]'} bg-surface-2 flex flex-col items-center justify-center gap-2.5">
				<div class="spinner-sm"></div>
				<span class="font-mono text-2xs uppercase tracking-[0.06em] tabular-nums text-fg-muted">
					Uploading · {uploadProgress}%
				</span>
			</div>
			<div class="shrink-0 h-0.5 bg-line">
				<div class="h-full bg-signal transition-[width] duration-150" style="width: {uploadProgress}%"></div>
			</div>
			<div class="shrink-0 flex items-center gap-2 px-2.5 py-1.5 border-t border-line bg-surface-1">
				<span class="min-w-0 flex-1 truncate text-xs text-fg-muted" title={uploadingName || ''}>
					{uploadingName || 'Uploading'}
				</span>
				{#if uploadingSize}
					<span class="shrink-0 font-mono text-2xs tabular-nums text-fg-subtle">
						{formatBytes(uploadingSize)}
					</span>
				{/if}
			</div>
		</div>
	{:else if face === 'image' || face === 'video' || face === 'audio'}
		<!-- Faces 03 / 04 / 05 · Loaded. Tools sit in a toolbar UNDER the
		     preview - overlay chrome covers the pixels the user is judging,
		     and at 340px it covers a meaningful fraction of them. -->
		<div
			class="rounded-lg border border-line-strong overflow-hidden bg-canvas {compact
				? fill
					? 'w-full flex-1 min-h-0 flex flex-col'
					: compactFullWidth
						? 'w-full'
						: 'max-w-[200px]'
				: ''}"
		>
			{#if face === 'image'}
				<button
					type="button"
					class="w-full {fill ? 'flex-1 min-h-[120px]' : compact ? 'h-[120px]' : 'h-[190px]'} flex items-center justify-center dot-grid bg-surface-2"
					on:click={() => (previewModal = { kind: 'image', url: previewUrl || '', label: fileName || 'Preview' })}
					aria-label="View full size"
				>
					<img src={previewUrl} alt={fileName || 'Preview'} class="max-w-full max-h-full object-contain" />
				</button>
			{:else if face === 'video'}
				<!-- svelte-ignore a11y-media-has-caption -->
				<video
					bind:this={videoElement}
					src={previewUrl}
					class="w-full {fill ? 'flex-1 min-h-[120px]' : compact ? 'h-[120px]' : 'h-[172px]'} bg-surface-2 object-contain"
					controls
					preload="metadata"
				>
					<track kind="captions" />
				</video>
			{:else}
				<div class="px-3 pt-3 pb-1 bg-surface-2">
					{#if !compact && waveformConfig}
						<Waveform
							audioElement={audioElement}
							url={previewUrl || ''}
							config={waveformConfig}
							onSeek={(time) => {
								if (audioElement) audioElement.currentTime = time;
							}}
						/>
					{/if}
					<audio bind:this={audioElement} src={previewUrl} class="w-full mt-1" controls preload="metadata">
						Your browser does not support the audio element.
					</audio>
				</div>
			{/if}

			<div class="shrink-0 flex items-center gap-1 p-1.5 bg-surface-2 border-t border-line">
				{#each singleTools as tool (tool.key)}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 h-7 px-2 rounded bg-surface-3 border border-line-strong text-xs whitespace-nowrap hover:border-line-hover transition-colors {tool.tone ===
						'danger'
							? 'text-danger'
							: 'text-fg'} {tool.pushRight ? 'ml-auto' : ''}"
						title={tool.title}
						aria-label={tool.title}
						on:click={() => runTool(tool.key)}
					>
						<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								stroke-width="1.8"
								d={TOOL_ICON_PATHS[tool.key]}
							/>
						</svg>
						{#if tool.showLabel && tool.label}
							<span>{tool.label}</span>
						{/if}
					</button>
				{/each}
			</div>

			<div class="shrink-0 flex items-center gap-2 px-2.5 py-1.5 border-t border-line bg-surface-1">
				{#if fileType}
					<Icon name={KIND_ICON[fileType]} className="w-3 h-3 shrink-0 text-fg-subtle" strokeWidth={1.8} />
				{/if}
				<span class="min-w-0 flex-1 truncate text-xs text-fg-muted" title={fileName || ''}>
					{fileName || 'Uploaded file'}
				</span>
				{#if existingMaskUrl && fileType === 'image'}
					<span class="shrink-0 font-mono text-2xs uppercase tracking-[0.06em] text-success">mask</span>
				{/if}
				{#if value && typeof value === 'object' && value.fromGeneration}
					<span
						class="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-signal/10 font-mono text-2xs uppercase tracking-[0.06em] text-signal"
					>
						<Icon name="clock" className="w-2.5 h-2.5" />
						from generation
					</span>
				{/if}
			</div>

			{#if previewChips.length > 0}
				<div class="shrink-0 flex flex-wrap items-center gap-1.5 px-2.5 pb-2 bg-surface-1">
					{#each previewChips as chip (chip.key)}
						<span
							title={chip.title}
							class="inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded bg-surface-2 ring-1 ring-inset ring-line font-mono text-2xs tabular-nums {chip.tone ===
							'signal'
								? 'text-signal'
								: chip.tone === 'success'
									? 'text-success'
									: 'text-fg-muted'}"
						>
							<svg class="w-2.5 h-2.5 text-fg-subtle" fill="none" viewBox="0 0 24 24" stroke="currentColor">
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d={META_ICON_PATHS[chip.icon]}
								/>
							</svg>
							{chip.text}
						</span>
					{/each}
				</div>
			{:else if compact && compactMetaLine}
				<p class="shrink-0 px-2 pb-1.5 font-mono text-2xs tabular-nums text-fg-subtle truncate" title={compactMetaLine}>
					{compactMetaLine}
				</p>
			{/if}
		</div>

		<input
			bind:this={fileInput}
			type="file"
			on:change={handleFileSelect}
			accept={acceptedTypes}
			class="hidden"
		/>
	{:else}
		<!-- Faces 01 / 02 · Empty -->
		<div
			class="rounded border border-dashed text-center transition-colors cursor-pointer {compact && !fill
				? 'p-3'
				: 'px-3.5 py-4'} {isDragging
				? 'border-signal bg-signal/10'
				: isPasteActive
					? 'border-signal bg-signal/10'
					: 'border-line-strong bg-surface-1 hover:border-line-hover'} {compact
				? fill
					? 'w-full flex-1 min-h-0 flex flex-col justify-center'
					: compactFullWidth
						? 'w-full'
						: 'max-w-[200px]'
				: ''}"
			on:dragenter={handleDragEnter}
			on:dragleave={handleDragLeave}
			on:dragover={handleDragOver}
			on:drop={handleDrop}
			on:click={() => (isPasteActive = true)}
			role="button"
			tabindex="0"
			on:keydown={(e) => e.key === 'Enter' && openFilePicker()}
		>
			<input
				bind:this={fileInput}
				type="file"
				on:change={handleFileSelect}
				accept={acceptedTypes}
				class="hidden"
			/>

			<div class="flex flex-col items-center gap-2.5">
				{#if compact && !fill}
					<Icon name="upload" className="w-4 h-4 text-fg-subtle" strokeWidth={1.6} />
					<p class="text-xs text-fg-muted">Paste or drop {soleKind ?? 'media'}</p>
				{:else}
					<div
						class="w-9 h-9 inline-flex items-center justify-center rounded-md bg-surface-2 border border-line-strong text-fg-muted"
					>
						<Icon name="upload" className="w-4 h-4" strokeWidth={1.6} />
					</div>
					<div>
						<p class="text-sm text-fg">{emptyHint}</p>
						<p class="mt-0.5 font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">
							{isPasteActive ? 'Paste armed — ⌘V' : 'or paste from clipboard'}
						</p>
					</div>
				{/if}

				<div
					class="w-full {compact
						? fill
							? 'grid grid-cols-1 gap-1.5'
							: 'flex flex-wrap items-center justify-center gap-1'
						: 'grid grid-cols-2 gap-1.5'}"
				>
					{#if offersHistory}
						<button
							type="button"
							class="{compact && !fill
								? 'w-6 h-6 justify-center'
								: 'h-7 px-2 justify-center gap-1.5'} inline-flex items-center border border-line-strong bg-surface-2 rounded text-xs text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
							on:click|stopPropagation={() => openHistoryModal()}
							title="Pick from generation history"
						>
							<Icon name="clock" className="w-3.5 h-3.5" />
							{#if !compact || fill}<span>History</span>{/if}
						</button>
					{/if}
					<button
						type="button"
						class="{compact && !fill
							? 'w-6 h-6 justify-center'
							: 'h-7 px-2 justify-center gap-1.5'} inline-flex items-center border border-line-strong bg-surface-2 rounded text-xs text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
						on:click|stopPropagation={() => openFilePicker()}
						title="Browse files"
					>
						<Icon name="folder" className="w-3.5 h-3.5" />
						{#if !compact || fill}<span>Browse</span>{/if}
					</button>
					<button
						type="button"
						class="{compact && !fill
							? 'w-6 h-6 justify-center'
							: 'h-7 px-2 justify-center gap-1.5'} inline-flex items-center border border-line-strong bg-surface-2 rounded text-xs text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
						on:click|stopPropagation={() => (isPasteActive = true)}
						title="Paste from clipboard"
					>
						<Icon name="clipboard-list" className="w-3.5 h-3.5" />
						{#if !compact || fill}<span>Paste</span>{/if}
					</button>
					<button
						type="button"
						class="{compact && !fill
							? 'w-6 h-6 justify-center'
							: 'h-7 px-2 justify-center gap-1.5'} inline-flex items-center border border-line-strong bg-surface-2 rounded text-xs text-fg-muted hover:bg-surface-3 hover:text-fg hover:border-line-hover transition-colors"
						on:click|stopPropagation={() => openUploadLibraryModal()}
						title="Pick from the library"
					>
						<Icon name="grid" className="w-3.5 h-3.5" />
						{#if !compact || fill}<span>Library</span>{/if}
					</button>
				</div>
			</div>
		</div>

		{#if !compact}
			<div class="flex items-center justify-between mt-1.5 font-mono text-2xs tracking-[0.06em] text-fg-subtle">
				<span>{formatsHint}</span>
				{#if sizeHint}<span>{sizeHint}</span>{/if}
			</div>
		{/if}
	{/if}
</div>

<!-- Generation History Modal. No History entry point for an audio-only field
     above - GenerationCard now renders audio, but GenerationHistoryModal's
     `mediaType` prop (and the history-list API's `media_type` filter it
     forwards) is still image/video only, so audio has no server-filtered
     picker yet. -->
<GenerationHistoryModal
	isOpen={showHistoryModal}
	onClose={() => (showHistoryModal = false)}
	onSelect={handleSelectFromGeneration}
	mediaType={historyPickerKind}
	title="Select {historyPickerKind === 'image'
		? 'Image'
		: historyPickerKind === 'video'
			? 'Video'
			: 'Media'} from Generation History"
/>

<!-- Upload Library Modal -->
<UploadLibraryModal
	isOpen={showUploadLibraryModal}
	onClose={() => (showUploadLibraryModal = false)}
	onSelect={handleSelectFromUpload}
	mediaType={pickerKind ?? undefined}
	title="Select {pickerKind === 'image'
		? 'Image'
		: pickerKind === 'video'
			? 'Video'
			: pickerKind === 'audio'
				? 'Audio'
				: 'Media'} from Your Uploads"
/>

<!-- Crop & frame / trim in-out / extract a frame / inpainting mask. One mount
     for all four - see $lib/media/editors. Every edit is performed by the
     server and comes back a library resource; a value pointing at a generated
     file gets a copy made for it first, which the editor says out loud. -->
<MediaEditors
	request={editorRequest}
	{existingMaskUrl}
	onClose={() => (editorRequest = null)}
	onResult={handleEditorResult}
/>

<!-- Media Preview Modal - peek at a face's own preview or any multi tile,
     full size, image/video/audio alike. -->
<MediaPreviewModal
	isOpen={previewModal !== null}
	onClose={() => (previewModal = null)}
	kind={previewModal?.kind ?? 'image'}
	url={previewModal?.url ?? ''}
	label={previewModal?.label || 'Preview'}
/>

<style>
	.spinner-sm {
		border: 2px solid rgb(var(--line-strong));
		border-top-color: rgb(var(--signal));
		border-radius: 50%;
		width: 24px;
		height: 24px;
		animation: spin 0.9s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
