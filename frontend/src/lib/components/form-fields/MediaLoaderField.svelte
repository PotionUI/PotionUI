<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount, onDestroy, getContext } from 'svelte';
	import { storage } from '$lib/utils/storage';
	import { api } from '$lib/services/api/index';
	import type { EditedMediaItem, UploadFileInfo } from '$lib/services/api/media';
	import { formatBytes } from '$lib/utils/format';
	import { primaryModifier } from '$lib/utils/platform';
	import MediaPreviewModal from '$lib/components/modals/MediaPreviewModal.svelte';
	import type { MediaEditorKind, MediaEditorRequest, MediaEditorResult } from '$lib/media/editors';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ToolHost from '$lib/components/tools/ToolHost.svelte';
	import { IconButton } from '$lib/components/ui';
	import { buildFieldToolContext, type MediaTool, type MediaToolContext } from '$lib/tools/tools';
	import { loadPluginMediaTools, mediaToolRegistrations } from '$lib/tools/pluginTools';
	import { maskSubjectKey, resolveMaskBinding } from './mediaLoaderMask';
	import { buildUploadedMediaItem, postUpload, type UploadedMediaItem } from './mediaLoaderUpload';
	import { fetchMediaMetadata } from './mediaLoaderMetadata';
	import { readSingleValue, type SingleValue } from './mediaLoaderValue';
	import { pickFromGeneration, pickFromUpload } from './mediaLoaderPicks';
	import { clickIsOutside, fileDrop, readPastedImage } from './mediaFieldInput';
	import { itemLimitFor, readMediaLoaderConfig, type MediaKind } from './mediaLoaderConfig';
	import { kindFromMimeType, kindFromFilename, kindOfMediaItem } from './mediaLoaderKind';
	import {
		describeCandidate,
		evaluateCandidate,
		summarizeContents,
		type MediaCandidate
	} from './mediaLoaderAcceptance';
	import { probeMediaFile } from './mediaLoaderProbe';
	import type { MediaItemMetadata } from './mediaLoaderMeta';
	import { originFileIndex } from './mediaLoaderOrigin';
	import { buildEditedMediaItem } from './mediaLoaderEdited';
	import { chooseFace } from './mediaFieldFace';
	import {
		buildMediaGroups,
		clampSelection,
		describeHandle,
		formatCount,
		layoutGroups,
		nudgeInGroup
	} from './mediaFieldGroups';
	import { FIELD_TOOL_EDITORS, fieldToolGroupsFor, matchToolShortcut } from './mediaFieldTools';
	import { openFieldInEditor } from '$lib/tool-tabs/openShortcut';
	import { globalBindingFor } from '$lib/services/keyboard';
	import { currentSourceOf, type MediaSource } from './mediaFieldSources';
	import MediaDropzone from './MediaDropzone.svelte';
	import MediaFieldEditors from './MediaFieldEditors.svelte';
	import MediaFieldPickers from './MediaFieldPickers.svelte';
	import { historyTileLabel, type PickedMedia } from '$lib/components/modals/mediaPickerSelection';
	import MediaInspector from './MediaInspector.svelte';
	import MediaStrip from './MediaStrip.svelte';
	import KitLoadDialog from '$lib/components/kits/KitLoadDialog.svelte';
	import LoadedKitHost from '$lib/components/kits/LoadedKitHost.svelte';
	import { acceptedFrom, mediaCountOf } from '$lib/kits/kitCounts';
	import { addKitRow, removeKitRow, snapshotOf, splitKitRows, withKitRows } from '$lib/kits/kitMarker';
	import type { KitDetail, KitRow } from '$lib/kits/types';
	import { ensureKits, kitDirectory } from '$lib/stores/kits';
	import { KIT_ROWS_HOSTED_CONTEXT_KEY, NOT_HOSTED } from '$lib/kits/hostContext';
	import MediaRow from './MediaRow.svelte';
	import { findResourceSpec, mediaItemKey, resourceHandleLabel } from '$lib/utils/promptResources';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import {
		EMPTY_PROMPT_RESOURCE_USAGE,
		PROMPT_RESOURCE_USAGE_CONTEXT_KEY,
		resourceUseCount,
		type PromptResourceUsage
	} from '$lib/utils/promptResourceUsage';
	import type { Readable } from 'svelte/store';

	type Item = Record<string, unknown>;

	const modifier = primaryModifier();

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: { generation_id: string; file_index: number } | undefined) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let autoPaste: boolean = false;
	export let compact: boolean = false;
	export let compactFullWidth: boolean = false;
	export let fill: boolean = false;
	export let onOpenEditor: ((request: MediaEditorRequest) => void) | undefined = undefined;

	const MAX_LABEL_LENGTH = 64;
	const NO_LIMITS = { maxItems: null, maxItemsByKind: {} };

	$: label = config.title || name || '';
	$: description = config.description || '';

	$: limits = readMediaLoaderConfig(config);
	$: kinds = limits.kinds;
	$: soleKind = kinds.length === 1 ? kinds[0] : null;
	$: acceptedTypes = limits.accept;
	$: multiple = limits.multiple;
	$: byKindLimits = Object.keys(limits.maxItemsByKind).length > 0;
	$: rawMulti = multiple && Array.isArray(value) ? (value as Item[]) : [];
	$: split = splitKitRows(rawMulti);
	$: multiItems = split.media;
	$: kitRows = split.kits;
	$: kitIds = kitRows.map((row) => row.kit);
	$: void ensureKits(kitIds);
	$: kitSpecKinds = name ? $resourceUsage.specs.filter((spec) => spec.field === name).map((spec) => spec.kind) : [];
	$: kitAccepted = acceptedFrom(kitSpecKinds);
	$: kitMedia = kitRows.reduce((sum, row) => {
		const detail = $kitDirectory.get(row.kit);
		return sum + (detail ? mediaCountOf(detail.items, kitAccepted) : 0);
	}, 0);
	$: canKits = multiple && !!name && kitSpecKinds.length > 0;
	const kitRowsHosted = getContext<Readable<boolean> | undefined>(KIT_ROWS_HOSTED_CONTEXT_KEY) ?? NOT_HOSTED;
	let showKitDialog = false;
	onMount(() => {
		if (kitIds.length > 0) void ensureKits(kitIds, { refresh: true });
	});
	$: groupLimits = byKindLimits || kinds.length === 1 ? limits : NO_LIMITS;
	$: totalFull = !byKindLimits && kinds.length > 1 && limits.maxItems != null && multiItems.length >= limits.maxItems;
	$: groups = buildMediaGroups(multiItems, kinds, groupLimits, (item: Item) => kindOfMediaItem(item)).map((group) =>
		totalFull ? { ...group, full: true } : group
	);
	$: allFull = multiple && groups.length > 0 && groups.every((group) => group.full);
	$: countLimit = !multiple
		? null
		: byKindLimits
			? kinds.every((kind) => itemLimitFor(limits, kind) != null)
				? kinds.reduce((sum, kind) => sum + (itemLimitFor(limits, kind) as number), 0)
				: null
			: limits.maxItems;

	const resourceUsage =
		getContext<Readable<PromptResourceUsage> | undefined>(PROMPT_RESOURCE_USAGE_CONTEXT_KEY) ??
		EMPTY_PROMPT_RESOURCE_USAGE;

	let fileInput: HTMLInputElement;
	let uploadAreaRef: HTMLDivElement;
	let rootWidth: number | null = null;
	let isDragging = false;
	let isUploading = false;
	let uploadProgress = 0;
	let uploadingName: string | null = null;
	let uploadingSize: number | null = null;
	let uploadingKind: MediaKind | null = null;
	let previewUrl: string | null = null;
	let fileName: string | null = null;
	let fileType: MediaKind | null = null;
	let mediaMetadata: MediaItemMetadata | null = null;
	let rejection: { reasons: string[]; detail: string | null } | null = null;
	let selected: number | null = null;
	let inspectorBroken = false;
	let replaceTarget: number | null = null;
	let pendingKind: MediaKind | null = null;
	let replacing: { index: number | null } | null = null;
	let pasteReplace: { index: number | null } | null = null;

	function reject(reason: string, detail: string | null = null) {
		rejection = { reasons: [reason], detail };
	}

	const metadataCache = new Map<string, MediaItemMetadata | null>();

	function currentRawPath(): string | null {
		if (value && typeof value === 'object') return value.relative_path || value.path || null;
		if (typeof value === 'string') return value;
		return null;
	}

	async function fetchMissingMetadata() {
		const raw = currentRawPath();
		if (!raw || !fileType || mediaMetadata) return;
		if (metadataCache.has(raw)) {
			mediaMetadata = metadataCache.get(raw) ?? null;
			return;
		}
		metadataCache.set(raw, null);
		try {
			const fetched = await fetchMediaMetadata(raw, api);
			if (!fetched) return;
			metadataCache.set(raw, fetched);
			if (raw === currentRawPath()) mediaMetadata = fetched;
		} catch (error) {
			logger.error('Failed to fetch media metadata:', error);
		}
	}

	$: if (previewUrl && fileType && !mediaMetadata) {
		fetchMissingMetadata();
	}

	let isPasteActive = autoPaste;
	let showHistoryModal = false;
	let showUploadLibraryModal = false;

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

	let maskAdopt: string | null = null;

	$: {
		const binding = resolveMaskBinding(maskSubject, maskAdopt, value);
		maskAdopt = binding.adopt;
		if (binding.subject !== maskSubject) maskSubject = binding.subject;
		if (binding.clear) clearMask();
	}

	let previewModal: { kind: MediaKind; url: string; label: string } | null = null;
	let pluginRun: { tool: MediaTool; context: MediaToolContext } | null = null;

	$: if (autoPaste && !multiple && !previewUrl) {
		isPasteActive = true;
	}

	$: if (!multiple) applySingleValue(readSingleValue(value));

	function applySingleValue(read: SingleValue | null) {
		if (!read && previewUrl && previewUrl.startsWith('blob:')) URL.revokeObjectURL(previewUrl);
		previewUrl = read?.previewUrl ?? null;
		fileName = read?.fileName ?? null;
		fileType = read?.fileType ?? null;
		if (read || mediaMetadata) mediaMetadata = read?.metadata ?? null;
	}

	$: singleRecord = (
		!multiple && previewUrl && fileType
			? {
					...(value && typeof value === 'object' ? value : {}),
					url: previewUrl,
					name: fileName,
					type: fileType,
					metadata: mediaMetadata
				}
			: null
	) as Item | null;

	function itemMetadata(item: unknown): MediaItemMetadata | null {
		if (!item || typeof item !== 'object') return null;
		const meta = (item as Record<string, unknown>).metadata;
		return meta && typeof meta === 'object' ? (meta as MediaItemMetadata) : null;
	}

	function itemDuration(item: unknown): number | null {
		const duration = itemMetadata(item)?.duration_seconds;
		return typeof duration === 'number' ? duration : null;
	}

	function itemUrl(item: Item | null | undefined): string {
		return item && typeof item.url === 'string' ? item.url : '';
	}

	function itemName(item: Item | null | undefined): string {
		return item && typeof item.name === 'string' && item.name ? item.name : 'Media';
	}

	function currentContents() {
		const held = multiple ? multiItems : value ? [value] : [];
		return summarizeContents(held, (item) => kindOfMediaItem(item), itemDuration);
	}

	function contentsWithout(index: number | null) {
		const held = multiple ? multiItems.filter((_: Item, i: number) => i !== index) : [];
		return summarizeContents(held, (item) => kindOfMediaItem(item), itemDuration);
	}

	function admit(candidate: MediaCandidate, replace: { index: number | null } | null = null): boolean {
		const contents = replace ? contentsWithout(replace.index) : currentContents();
		const verdict = evaluateCandidate(candidate, limits, contents, { fieldName: name });
		if (!verdict.accepted) {
			rejection = { reasons: verdict.reasons, detail: describeCandidate(candidate) };
			return false;
		}
		rejection = null;
		return true;
	}

	function handlePaste(e: ClipboardEvent) {
		if (!isPasteActive) return;
		const swap = pasteReplace;
		if (!swap && ((!multiple && previewUrl) || allFull)) return;
		if (showHistoryModal || showUploadLibraryModal) return;

		e.preventDefault();
		e.stopPropagation();

		const outcome = readPastedImage(e.clipboardData?.items);
		if ('file' in outcome) {
			uploadFile(outcome.file, swap ? swap.index : null, swap !== null);
			isPasteActive = false;
		} else {
			reject(outcome.error, outcome.detail);
		}
	}

	function handleClickOutside(event: MouseEvent) {
		if (clickIsOutside(uploadAreaRef, event.target as Node)) isPasteActive = false;
	}

	$: if (!isPasteActive) pasteReplace = null;

	function selectItem(flatIndex: number) {
		selected = flatIndex;
	}

	let resizeObserver: ResizeObserver | null = null;

	onMount(() => {
		document.addEventListener('paste', handlePaste, true);
		document.addEventListener('mousedown', handleClickOutside);
		loadPluginMediaTools();
		if (typeof ResizeObserver !== 'undefined' && uploadAreaRef) {
			resizeObserver = new ResizeObserver((entries) => {
				const width = entries[0]?.contentRect.width;
				rootWidth = width ? Math.round(width) : null;
			});
			resizeObserver.observe(uploadAreaRef);
		}
	});

	onDestroy(() => {
		if (typeof document === 'undefined') return;
		document.removeEventListener('paste', handlePaste, true);
		document.removeEventListener('mousedown', handleClickOutside);
		resizeObserver?.disconnect();
	});

	async function handleFileSelect(event: Event) {
		const target = event.target as HTMLInputElement;
		const file = target.files?.[0];
		target.value = '';
		const slot = replaceTarget;
		const swap = replacing !== null;
		replaceTarget = null;
		replacing = null;
		if (file) {
			await uploadFile(file, slot, swap);
		}
	}

	function handleDroppedFiles(files: FileList) {
		const file = files[0];
		if (file) uploadFile(file);
	}

	async function uploadFile(file: File, replaceIndex: number | null = null, replace: boolean = replaceIndex !== null) {
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
		if (!admit(candidate, replace ? { index: replaceIndex } : null)) return;

		isUploading = true;
		uploadProgress = 0;
		uploadingName = file.name;
		uploadingSize = file.size;
		uploadingKind = kind;
		replaceTarget = replaceIndex;

		try {
			const result = await postUpload(file, storage.get('auth_token'), (percent) => (uploadProgress = percent));
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
			uploadingKind = null;
			uploadProgress = 0;
			replaceTarget = null;
		}
	}

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

	function openFilePicker(kind: MediaKind | null = null, replaceIndex: number | null = null) {
		pendingKind = kind;
		replaceTarget = replaceIndex;
		if (fileInput) fileInput.accept = kind ? `${kind}/*` : acceptedTypes;
		fileInput?.click();
	}

	let editors: MediaFieldEditors;
	$: canDraw = !multiple && limits.kinds.includes('image') && !compact;

	function openEditor(kind: MediaEditorKind, item: unknown, index: number | null) {
		editors?.open(kind, item, index, { url: previewUrl || '', name: fileName || 'Media' });
	}

	async function handleEditorResult(result: MediaEditorResult, request: MediaEditorRequest) {
		if (result.type === 'mask') {
			applyMaskPath(result.maskPath);
			return;
		}
		if (result.type === 'items') {
			applySplitItems(result.items, request.itemIndex);
			return;
		}
		applyEditedItem(buildEditedMediaItem(result.item), request.itemIndex, result.keepMask === true);
	}

	function applyEditedItem(
		mediaItem: ReturnType<typeof buildEditedMediaItem>,
		target: number | null,
		keepMask: boolean = false
	) {
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
		if (keepMask && existingMaskUrl !== null) maskAdopt = maskSubjectKey(mediaItem);
		else clearMask();
	}

	function applySplitItems(items: EditedMediaItem[], target: number | null) {
		if (!name || items.length === 0) return;
		const built = items.map(buildEditedMediaItem);

		if (multiple) {
			const next: (Item | UploadedMediaItem)[] = [...multiItems];
			if (target !== null && target >= 0 && target < next.length) {
				next.splice(target, 1, ...built);
			} else {
				next.push(...built);
			}
			emitMulti(next);
			return;
		}

		if (built.length > 1) {
			logger.warn(
				`Split produced ${built.length} parts; this field only holds one, so the rest were left in the library.`
			);
		}
		applyEditedItem(built[0], null);
	}

	function emitMulti(next: (Item | UploadedMediaItem)[]) {
		if (!name) return;
		onChange(name, withKitRows(next, kitRows));
	}

	function handleKitsLoaded(kits: KitDetail[]) {
		if (!name) return;
		const rows = kits.reduce<KitRow[]>((held, kit) => addKitRow(held, { kit: kit.id, snapshot: snapshotOf(kit) }), kitRows);
		onChange(name, withKitRows(multiItems, rows));
	}

	function removeKit(row: KitRow) {
		if (!name) return;
		onChange(name, withKitRows(multiItems, removeKitRow(kitRows, row.kit)));
	}

	function groupFor(kind: MediaKind | null) {
		return groups.find((group) => group.kind === kind);
	}

	function appendMultiItem(item: Item | UploadedMediaItem) {
		if (!name || groupFor(kindOfMediaItem(item))?.full || totalFull) return;
		selected = multiItems.length;
		emitMulti([...multiItems, item]);
	}

	function replaceMultiItem(index: number, item: Item | UploadedMediaItem) {
		if (!name) return;
		const next = multiItems.map((existing: Item, i: number) =>
			i === index ? { ...item, ...(existing.label ? { label: existing.label } : {}) } : existing
		);
		emitMulti(next);
	}

	function removeMultiItem(index: number) {
		if (!name) return;
		emitMulti(multiItems.filter((_: unknown, i: number) => i !== index));
	}

	function clearAllItems() {
		if (!name) return;
		emitMulti([]);
	}

	function updateMultiLabel(index: number, text: string) {
		if (!name) return;
		const cleaned = text.slice(0, MAX_LABEL_LENGTH);
		const next = multiItems.map((item: Item, i: number) => (i === index ? { ...item, label: cleaned } : item));
		emitMulti(next);
	}

	function reorderItems(next: Item[], flatIndex: number) {
		if (!name) return;
		selected = flatIndex;
		emitMulti(next);
	}

	let selectionSeeded = false;
	$: if (multiple && multiItems.length > 0 && !selectionSeeded) {
		selectionSeeded = true;
		selected = 0;
	} else if (multiItems.length === 0) {
		selectionSeeded = false;
	}
	$: selected = clampSelection(selected, multiItems.length);
	$: selectedItem = multiple && selected !== null ? (multiItems[selected] ?? null) : null;
	$: selectedKind = selectedItem ? kindOfMediaItem(selectedItem) : null;
	$: selectedEntry = groups
		.flatMap((group) => group.items.map((entry) => ({ group, entry })))
		.find((found) => found.entry.flatIndex === selected);

	function handleHistoryConfirm(items: PickedMedia[]) {
		if (!name) {
			showHistoryModal = false;
			return;
		}
		if (!multiple) {
			const first = items.find((picked) => picked.file && picked.generation);
			if (first) handleGeneration(first.generation, first.file);
			else showHistoryModal = false;
			return;
		}
		const accepted: Item[] = [];
		for (const picked of items) {
			if (!picked.file || !picked.generation) continue;
			const pick = pickFromGeneration(picked.file, historyTileLabel(picked.generation, picked.file).label);
			const verdict = evaluateCandidate(
				pick.candidate,
				limits,
				summarizeContents([...multiItems, ...accepted], (item) => kindOfMediaItem(item as Item), itemDuration),
				{ fieldName: name }
			);
			if (!verdict.accepted) {
				rejection = {
					reasons: verdict.reasons,
					detail: `${accepted.length} of ${items.length} added. ${describeCandidate(pick.candidate)}`
				};
				break;
			}
			accepted.push(pick.item as Item);
		}
		if (accepted.length > 0) {
			if (accepted.length === items.length) rejection = null;
			selected = multiItems.length + accepted.length - 1;
			emitMulti([...multiItems, ...accepted]);
		}
		showHistoryModal = false;
	}

	function handleGeneration(generation: any, file: any) {
		if (!file) return;
		const swap = replacing;
		const pick = pickFromGeneration(file, historyTileLabel(generation, file).label);
		if (!admit(pick.candidate, swap)) return;

		if (multiple) {
			if (swap && swap.index !== null) replaceMultiItem(swap.index, pick.item);
			else appendMultiItem(pick.item);
		} else if (name) {
			commitSingle(pick.item);
			setOriginKey(generation.id, originFileIndex(generation, file));
			clearMask();
		}
		showHistoryModal = false;
		replacing = null;
	}

	function commitSingle(item: Item) {
		if (!name) return;
		const read = readSingleValue(item);
		previewUrl = read?.previewUrl ?? null;
		fileName = read?.fileName ?? null;
		fileType = read?.fileType ?? null;
		mediaMetadata = read?.metadata ?? null;
		onChange(name, item);
	}

	function openHistoryModal(kind: MediaKind | null = null) {
		pendingKind = kind;
		showHistoryModal = true;
	}

	function handleSelectFromUpload(upload: UploadFileInfo) {
		const swap = replacing;
		const pick = pickFromUpload(upload);
		if (!admit(pick.candidate, swap)) return;

		if (multiple) {
			if (swap && swap.index !== null) replaceMultiItem(swap.index, pick.item);
			else appendMultiItem(pick.item);
		} else {
			commitSingle(pick.item);
			clearOriginKey();
			clearMask();
		}
		showUploadLibraryModal = false;
		replacing = null;
	}

	function openUploadLibraryModal(kind: MediaKind | null = null) {
		pendingKind = kind;
		showUploadLibraryModal = true;
	}

	function applyMaskPath(maskPath: string) {
		if (!name) return;
		onMaskChange?.(name, maskPath);
		existingMaskUrl = `/api/media/serve?path=${encodeURIComponent(maskPath)}`;
		maskSubject = maskSubjectKey(value);
	}

	$: pickerKind = pendingKind ?? soleKind;
	$: sizeHint = limits.maxFileSizeBytes ? `max ${formatBytes(limits.maxFileSizeBytes, 0)}` : null;

	$: face = chooseFace({
		multiple,
		compact,
		fill,
		compactFullWidth,
		width: rootWidth,
		itemCount: multiple ? multiItems.length : singleRecord ? 1 : 0,
		mixedKinds: kinds.length > 1,
		uploading: isUploading
	});

	$: addingKind = isUploading && replaceTarget === null ? (uploadingKind ?? kinds[0]) : null;
	$: layoutSource = multiple
		? groups
		: buildMediaGroups(
				singleRecord ? [singleRecord] : [],
				[fileType ?? uploadingKind ?? kinds[0]],
				NO_LIMITS,
				(item: Item) => kindOfMediaItem(item)
			);
	$: layout = layoutGroups(layoutSource, addingKind, false);
	$: rowLayout = layoutGroups(layoutSource, addingKind);

	$: inspectorKind = multiple ? selectedKind : (fileType ?? (isUploading ? uploadingKind : null));
	$: inspectorUrl = multiple ? itemUrl(selectedItem) : (previewUrl ?? '');
	$: inspectorName = multiple ? itemName(selectedItem) : (fileName ?? 'Uploaded file');
	$: inspectorMetadata = multiple ? itemMetadata(selectedItem) : mediaMetadata;
	$: inspectorUploading =
		isUploading && (!multiple || replaceTarget !== null)
			? { name: uploadingName ?? 'Uploading', size: uploadingSize, progress: uploadProgress }
			: null;
	$: showInspector = (face === 'inspector' || face === 'strip') && inspectorKind !== null && (inspectorUrl !== '' || inspectorUploading !== null);

	$: resourceSpec =
		name && !compact && inspectorKind ? (findResourceSpec($resourceUsage.specs, name, inspectorKind) ?? null) : null;
	$: inspectorHandle =
		multiple && inspectorKind && selectedEntry
			? describeHandle(
					inspectorKind,
					selectedEntry.entry.position,
					resourceSpec,
					resourceSpec && name && selectedItem ? resourceUseCount($resourceUsage.counts, name, mediaItemKey(selectedItem)) : 0
				)
			: null;

	$: toolEnv = {
		multiple,
		groups,
		allowInpaint: limits.allowInpaint && !multiple,
		canEmitMask: onMaskChange !== undefined,
		hasMask: !multiple && existingMaskUrl !== null,
		totalItems: multiItems.length,
		registrations: $mediaToolRegistrations
	};

	$: inspectorToolGroups = fieldToolGroupsFor(toolEnv, {
		kind: inspectorKind,
		item: multiple ? selectedItem : singleRecord,
		flatIndex: multiple ? (selected ?? 0) : 0,
		inRow: false,
		missing: inspectorBroken
	});

	function rowToolGroups(env: typeof toolEnv, flatIndex: number) {
		const item = multiple ? (multiItems[flatIndex] ?? null) : singleRecord;
		return fieldToolGroupsFor(env, {
			kind: item ? kindOfMediaItem(item) : null,
			item,
			flatIndex,
			inRow: true,
			missing: false
		});
	}

	function rowHandle(flatIndex: number): string | null {
		if (!name || !multiple) return null;
		const item = multiItems[flatIndex];
		const kind = item ? kindOfMediaItem(item) : null;
		const spec = kind ? findResourceSpec($resourceUsage.specs, name, kind) : null;
		const entry = groupFor(kind)?.items.find((candidate) => candidate.flatIndex === flatIndex);
		return spec && entry ? resourceHandleLabel(spec, entry.position) : null;
	}

	function openPreview(kind: MediaKind, item: Item | null) {
		const url = itemUrl(item);
		if (!url) return;
		previewModal = { kind, url, label: itemName(item) };
	}

	function runFieldTool(tool: MediaTool, flatIndex: number) {
		const item = multiple ? (multiItems[flatIndex] ?? null) : singleRecord;
		const kind = item ? kindOfMediaItem(item) : null;
		const editor = FIELD_TOOL_EDITORS[tool.id as keyof typeof FIELD_TOOL_EDITORS];
		if (editor) {
			openEditor(editor, item, multiple ? flatIndex : null);
			return;
		}
		switch (tool.id) {
			case 'open-in-editor':
				if (kind === 'image' || kind === 'video') openFieldInEditor(kind, item, name);
				break;
			case 'clear-mask':
				clearMask();
				break;
			case 'full':
				if (kind) openPreview(kind, item);
				break;
			case 'earlier':
			case 'later': {
				const group = groupFor(kind);
				const entry = group?.items.find((candidate) => candidate.flatIndex === flatIndex);
				if (!group || !entry) break;
				const moved = nudgeInGroup(multiItems, group, entry.kindIndex, tool.id === 'earlier' ? -1 : 1);
				if (moved) reorderItems(moved.items, moved.flatIndex);
				break;
			}
			case 'remove':
				if (multiple) removeMultiItem(flatIndex);
				else handleClear();
				break;
			case 'remove-all':
				clearAllItems();
				break;
			default:
				if (kind) pluginRun = { tool, context: buildFieldToolContext({ id: 'field', kind, url: itemUrl(item), filename: itemName(item) }) };
		}
	}

	function handleSource(kind: MediaKind | null, source: MediaSource, swap: { index: number | null } | null = null) {
		replacing = swap;
		switch (source) {
			case 'browse':
				openFilePicker(kind, swap ? swap.index : null);
				break;
			case 'paste':
				pasteReplace = swap;
				isPasteActive = true;
				break;
			case 'history':
				openHistoryModal(kind);
				break;
			case 'library':
				openUploadLibraryModal(kind);
				break;
			case 'kits':
				showKitDialog = true;
				break;
			case 'draw':
				editors?.draw();
				break;
		}
	}

	function replaceVia(flatIndex: number | null, source: MediaSource) {
		const item = multiple ? (flatIndex === null ? null : (multiItems[flatIndex] ?? null)) : singleRecord;
		handleSource(item ? kindOfMediaItem(item) : null, source, { index: multiple ? flatIndex : null });
	}

	let pointerInside = false;

	function handleWindowKeydown(event: KeyboardEvent) {
		if (!pointerInside || event.key === 'Delete') return;
		if (event.target !== document.body && event.target !== document.documentElement) return;
		const modal = document.querySelector('[aria-modal="true"]');
		if (modal && !modal.contains(uploadAreaRef)) return;
		if (globalBindingFor(event)) return;
		handleRootKeydown(event);
	}

	function handleRootKeydown(event: KeyboardEvent) {
		if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
		if (face !== 'inspector' && face !== 'strip') return;
		if (isTypingInField(event)) return;
		const flatIndex = multiple ? selected : 0;
		if (flatIndex === null) return;
		if (event.key === 'Delete') {
			event.preventDefault();
			if (multiple) removeMultiItem(flatIndex);
			else handleClear();
			return;
		}
		const tool = matchToolShortcut(inspectorToolGroups, event.key);
		if (tool) {
			event.preventDefault();
			event.stopPropagation();
			runFieldTool(tool, flatIndex);
		}
	}

	$: countText = multiple && countLimit !== null ? formatCount(multiItems.length + kitMedia, countLimit) : null;
	$: kitRoom = countLimit === null ? null : Math.max(0, countLimit - multiItems.length - kitMedia);
</script>

<svelte:window on:keydown|capture={handleWindowKeydown} />

<div
	bind:this={uploadAreaRef}
	class="relative {compact ? (fill ? 'h-full flex flex-col' : '') : 'field-card'}"
	role="presentation"
	data-media-field
	data-face={face}
	on:keydown={handleRootKeydown}
	on:mouseenter={() => (pointerInside = true)}
	on:mouseleave={() => (pointerInside = false)}
	use:fileDrop={{ onFiles: handleDroppedFiles, onActive: (active) => (isDragging = active) }}
	on:click={() => (isPasteActive = true)}
>
	{#if !compact}
		<div class="flex items-baseline gap-2">
			{#if label}
				<label class="label" for={name || undefined}>{label}</label>
			{/if}
			<div class="flex-1"></div>
			{#if isPasteActive && face === 'strip'}
				<span class="font-mono text-xs text-signal">Paste armed · {modifier} V</span>
			{/if}
			{#if countText}
				<span
					class="font-mono text-xs tabular-nums {allFull ? 'text-warning' : 'text-fg-subtle'}"
					data-media-count
				>
					{countText}
				</span>
			{/if}
		</div>
	{/if}

	{#if description && !compact}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mb-2">{description}</p>
	{/if}

	{#if rejection}
		<div
			class="flex items-start gap-2 mb-2 px-2.5 py-2 rounded bg-danger/10 ring-1 ring-inset ring-danger/30"
			data-media-rejection
		>
			<Icon name="warning" className="w-3.5 h-3.5 shrink-0 mt-0.5 text-danger" />
			<div class="min-w-0 flex-1">
				{#each rejection.reasons as reason (reason)}
					<p class="text-xs text-danger">{reason}</p>
				{/each}
				{#if rejection.detail}
					<Tooltip text={rejection.detail} position="bottom" wrapperClass="flex min-w-0">
						<p class="mt-0.5 font-mono text-xs text-danger/80 truncate">{rejection.detail}</p>
					</Tooltip>
				{/if}
			</div>
			<Tooltip text="Dismiss" position="top">
				<IconButton icon="close" label="Dismiss" size="xs" onclick={() => (rejection = null)} />
			</Tooltip>
		</div>
	{/if}

	{#if face === 'row'}
		<div
			class={compact
				? fill
					? 'w-full flex-1 min-h-0 flex flex-col justify-center'
					: compactFullWidth
						? 'w-full'
						: 'max-w-[240px]'
				: ''}
		>
			<MediaRow
				{multiple}
				{kinds}
				items={multiple ? multiItems : singleRecord ? [singleRecord] : []}
				layout={rowLayout}
				adding={addingKind}
				addingProgress={uploadProgress}
				addingName={uploadingName}
				dragging={isDragging}
				pasteArmed={isPasteActive}
				offersDraw={canDraw}
				offersKits={canKits}
				maxLabelLength={MAX_LABEL_LENGTH}
				toolGroupsFor={(flatIndex) => rowToolGroups(toolEnv, flatIndex)}
				handleFor={rowHandle}
				onTool={runFieldTool}
				onLabel={updateMultiLabel}
				onReorder={reorderItems}
				onSource={(kind, source) => handleSource(kind, source)}
				onChangeSource={replaceVia}
				onPreview={(flatIndex) => {
					const item = multiple ? (multiItems[flatIndex] ?? null) : singleRecord;
					const kind = item ? kindOfMediaItem(item) : null;
					if (kind) openPreview(kind, item);
				}}
			/>
		</div>
	{:else}
		{#if showInspector && inspectorKind}
			<MediaInspector
				kind={inspectorKind}
				url={inspectorUrl}
				name={inspectorName}
				metadata={inspectorMetadata}
				{multiple}
				handle={inspectorHandle}
				label={selectedItem && typeof selectedItem.label === 'string' ? selectedItem.label : ''}
				maxLabelLength={MAX_LABEL_LENGTH}
				hasMask={!multiple && existingMaskUrl !== null}
				maskUrl={multiple ? null : existingMaskUrl}
				fromGeneration={!multiple && value && typeof value === 'object' && Boolean(value.fromGeneration)}
				toolGroups={inspectorToolGroups}
				uploading={inspectorUploading}
				dropHint={isDragging ? (multiple ? 'Drop to add' : 'Drop to replace') : null}
				bind:broken={inspectorBroken}
				onLabel={(text) => selected !== null && updateMultiLabel(selected, text)}
				onTool={(tool) => runFieldTool(tool, multiple ? (selected ?? 0) : 0)}
				onClearMask={clearMask}
				current={currentSourceOf(multiple ? selectedItem : singleRecord)}
				offersDraw={canDraw}
				onSource={(source) => replaceVia(multiple ? selected : null, source)}
				onRemove={() => (multiple ? selected !== null && removeMultiItem(selected) : handleClear())}
				onPreview={() => inspectorKind && openPreview(inspectorKind, multiple ? selectedItem : singleRecord)}
			/>
		{/if}

		{#if face === 'strip'}
			<div class={showInspector ? 'mt-3' : ''}>
				<MediaStrip
					items={multiItems}
					{layout}
					{selected}
					adding={addingKind}
					addingProgress={uploadProgress}
					offersDraw={false}
					offersKits={canKits}
					onSelect={selectItem}
					onReorder={reorderItems}
					onRemove={removeMultiItem}
					onSource={handleSource}
				/>
			</div>
		{:else if face === 'dropzone'}
			<MediaDropzone
				{kinds}
				dragging={isDragging}
				pasteArmed={isPasteActive}
				offersDraw={canDraw}
				offersKits={canKits}
				{sizeHint}
				onSource={(source) => handleSource(null, source)}
			/>
		{/if}
	{/if}

	{#if multiple && kitRows.length > 0 && !$kitRowsHosted}
		<div class="space-y-2 {compact ? 'mt-2' : 'mt-3'}" data-loaded-kits>
			{#each kitRows as row (row.kit)}
				<LoadedKitHost {row} accepted={kitAccepted} {compact} onRemove={removeKit} onChoose={() => (showKitDialog = true)} />
			{/each}
		</div>
	{/if}

	<input bind:this={fileInput} type="file" on:change={handleFileSelect} accept={acceptedTypes} class="hidden" />
</div>

{#if showKitDialog}
	<KitLoadDialog
		loadedIds={kitIds}
		accepted={kitAccepted}
		mediaRoom={kitRoom}
		subtitle={label || 'Reference media'}
		onLoad={handleKitsLoaded}
		onClose={() => (showKitDialog = false)}
	/>
{/if}

<MediaFieldPickers
	historyOpen={showHistoryModal}
	libraryOpen={showUploadLibraryModal}
	kind={pickerKind}
	replacing={replacing !== null}
	onHistoryClose={() => {
		showHistoryModal = false;
		replacing = null;
	}}
	onLibraryClose={() => {
		showUploadLibraryModal = false;
		replacing = null;
	}}
	onHistorySelect={handleGeneration}
	historyMultiple={multiple && replacing === null}
	onHistoryConfirm={handleHistoryConfirm}
	onLibrarySelect={handleSelectFromUpload}
/>

<MediaFieldEditors
	bind:this={editors}
	{config}
	{existingMaskUrl}
	{onOpenEditor}
	onResult={handleEditorResult}
/>

<ToolHost tool={pluginRun?.tool ?? null} context={pluginRun?.context ?? null} onClose={() => (pluginRun = null)} onDone={() => (pluginRun = null)} />

<MediaPreviewModal
	isOpen={previewModal !== null}
	onClose={() => (previewModal = null)}
	kind={previewModal?.kind ?? 'image'}
	url={previewModal?.url ?? ''}
	label={previewModal?.label || 'Preview'}
/>
