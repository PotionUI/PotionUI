<script lang="ts">
	import { setContext } from 'svelte';
	import { derived, writable } from 'svelte/store';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import CropEditor from './CropEditor.svelte';
	import TrimEditor from './TrimEditor.svelte';
	import FrameEditor from './FrameEditor.svelte';
	import MaskEditor from './MaskEditor.svelte';
	import SplitEditor from './SplitEditor.svelte';
	import ResizeEditor from './ResizeEditor.svelte';
	import ToolDialogHost from './ToolDialogHost.svelte';
	import { decideMaskFate } from '$lib/components/imageEditor/maskPolicy';
	import { resolveEditableResource, type ResourceOrigin } from './editorSource';
	import { describeMediaToolFailure } from '$lib/media-tools/errors';
	import { executeMediaTool } from '$lib/media-tools/execute';
	import type { AnnounceSetting } from '$lib/media-tools/announce';
	import { mediaToolRuns, type MediaToolRunOutcome, type RunHandle } from '$lib/media-tools/runs';
	import type { MediaToolInputRef, MediaToolStep } from '$lib/media-tools/types';
	import { blockedUploadMessage } from '$lib/plans/uploadLimit';
	import { dataUrlToFile } from './maskFile';
	import { operationsToSteps } from './editOperations';
	import { EDITOR_RUN_CONTEXT_KEY, type EditorRunContext } from './editorRunContext';
	import {
		editsTheResource,
		isToolDialogKind,
		type EditorCommitRequest,
		type MediaEditorKind,
		type MediaEditorRequest,
		type MediaEditorResult,
		type MediaEditorSaveMode
	} from './types';

	export let request: MediaEditorRequest | null = null;
	export let existingMaskUrl: string | null = null;
	export let detach: boolean = false;
	export let announce: AnnounceSetting | undefined = undefined;
	export let scope: 'library' | 'history' | 'field' = 'library';
	export let onClose: () => void;
	export let onResult: (
		result: MediaEditorResult,
		request: MediaEditorRequest
	) => void | Promise<void>;

	interface RunPlan {
		steps: MediaToolStep[];
		mode: MediaEditorSaveMode;
		label: string;
		shape: 'item' | 'items';
	}

	const DETACHED = Symbol('detached');
	const LABELS: Record<string, string> = {
		crop: 'Crop',
		trim: 'Trim',
		resize: 'Resize',
		frame: 'Frame',
		split: 'Split'
	};

	let busy = false;
	let failure: string | null = null;
	let resolvedItemId: string | null = null;
	let pendingFileId: string | null = null;
	let resourceOrigin: ResourceOrigin | null = null;
	let resourceReason: string | null = null;
	let resolving = false;
	let frameHandoff: { time: number } | null = null;
	let lastRequest: MediaEditorRequest | null = null;
	let activeRun: RunHandle | null = null;

	const runKey = writable<string | null>(null);
	const runContext: EditorRunContext = {
		progress: derived([mediaToolRuns, runKey], ([$runs, $key]) =>
			$key ? ($runs[$key]?.progress ?? null) : null
		),
		cancelling: derived([mediaToolRuns, runKey], ([$runs, $key]) =>
			$key ? ($runs[$key]?.cancelling ?? false) : false
		),
		cancel: () => {
			if (activeRun) void mediaToolRuns.cancelRun(activeRun.runId);
		}
	};
	setContext(EDITOR_RUN_CONTEXT_KEY, runContext);

	$: kind = (frameHandoff ? 'frame' : (request?.kind ?? null)) as MediaEditorKind | null;

	$: if (request) resetFor(request);

	function resetFor(next: MediaEditorRequest) {
		if (next === lastRequest) return;
		lastRequest = next;
		busy = false;
		failure = null;
		frameHandoff = null;
		resolvedItemId = null;
		pendingFileId = null;
		resourceOrigin = null;
		resourceReason = null;
		resolving = false;
		activeRun = null;
		runKey.set(null);

		if (editsTheResource(next.kind) && !isToolDialogKind(next.kind)) void resolveResource(next);
	}

	async function resolveResource(next: MediaEditorRequest) {
		resolving = true;
		try {
			const resolved = await resolveEditableResource(
				next.source.itemId,
				next.source.storedPath,
				next.source.kind,
				next.source.generationFileId
			);
			if (lastRequest !== next) return;
			resolvedItemId = resolved.itemId;
			pendingFileId = resolved.generationFileId;
			resourceOrigin = resolved.origin;
			resourceReason = resolved.reason;
		} finally {
			if (lastRequest === next) resolving = false;
		}
	}

	$: blockedReason = !kind || !editsTheResource(kind) || isToolDialogKind(kind)
		? null
		: resolvedItemId || pendingFileId
			? null
			: resolving
				? 'Preparing this media…'
				: resourceReason;

	$: inField = scope === 'field';
	$: kindWord = request?.source.kind === 'audio' ? 'clip' : (request?.source.kind ?? 'item');

	$: resourceNote = inField
		? `The result replaces this ${kindWord} in the field. The library copy stays as it is.`
		: resourceOrigin === 'pending'
			? 'Saves a copy to your Library. The generation is not changed.'
			: null;

	$: allowReplace = !inField && resourceOrigin !== 'pending';

	function closeFrameEditor() {
		if (frameHandoff) {
			frameHandoff = null;
			return;
		}
		onClose();
	}

	async function commit(commitRequest: EditorCommitRequest) {
		if (!request) return;
		const current = request;

		busy = true;
		failure = null;
		try {
			const result = await produce(commitRequest, current);
			if (result === DETACHED) {
				onClose();
				return;
			}
			if (result) {
				await onResult(result, current);
				onClose();
			}
		} catch (error) {
			logger.error('Media edit failed:', error);
			failure = describeMediaToolFailure(error);
		} finally {
			busy = false;
			activeRun = null;
			runKey.set(null);
		}
	}

	async function produce(
		commitRequest: EditorCommitRequest,
		current: MediaEditorRequest
	): Promise<MediaEditorResult | typeof DETACHED | null> {
		if (commitRequest.via === 'mask') {
			const stored = await api.uploadMedia(
				dataUrlToFile(commitRequest.dataUrl, `mask-${Date.now()}.png`),
				'derived_artifact'
			);
			if (!stored.success || !stored.data) {
				throw new Error(stored.message || 'The mask could not be saved');
			}
			return { type: 'mask', maskPath: stored.data.path };
		}

		if (commitRequest.via === 'paint') {
			const blocked = blockedUploadMessage([commitRequest.file]);
			if (blocked) throw new Error(blocked);
			const stored = await api.uploadMedia(commitRequest.file, 'user_upload');
			if (!stored.success || !stored.data) {
				throw new Error(stored.message || 'The image could not be saved');
			}
			const upload = stored.data;
			const resolved = await resolveEditableResource(
				null,
				upload.relative_path || `uploads/${upload.filename}`,
				'image'
			);
			const fate = decideMaskFate({
				hasMask: existingMaskUrl !== null,
				geometryChanged: commitRequest.geometryChanged,
				sourceReplaced: commitRequest.sourceReplaced
			}).fate;
			return {
				type: 'item',
				item: {
					id: resolved.itemId ?? upload.filename,
					filename: upload.filename,
					original_filename: commitRequest.file.name,
					media_type: 'image',
					mime_type: 'image/png',
					url: upload.url,
					width: upload.width,
					height: upload.height,
					size: upload.size
				},
				replaced: false,
				keepMask: fate === 'keep'
			};
		}

		return produceRun(planFor(commitRequest), current);
	}

	function planFor(commitRequest: EditorCommitRequest): RunPlan {
		const label = LABELS[kind ?? ''] ?? 'Edit';
		if (commitRequest.via === 'split') {
			return {
				steps: [{ tool: 'split', params: { part_seconds: commitRequest.partSeconds } }],
				mode: 'new',
				label,
				shape: 'items'
			};
		}
		if (commitRequest.via === 'frame') {
			return {
				steps: [{ tool: 'frame', params: { time: commitRequest.timeSeconds } }],
				mode: 'new',
				label,
				shape: 'item'
			};
		}
		if (commitRequest.via !== 'operations') throw new Error('There is nothing to edit yet');
		return {
			steps: operationsToSteps(commitRequest.operations),
			mode: commitRequest.mode,
			label,
			shape: 'item'
		};
	}

	function resultOf(plan: RunPlan, outcome: Extract<MediaToolRunOutcome, { status: 'completed' }>): MediaEditorResult {
		if (plan.shape === 'items') return { type: 'items', items: outcome.items };
		if (outcome.items.length === 0) throw new Error('The edit produced no result');
		return { type: 'item', item: outcome.items[0], replaced: outcome.replaced };
	}

	async function produceRun(
		plan: RunPlan,
		current: MediaEditorRequest
	): Promise<MediaEditorResult | typeof DETACHED | null> {
		let ref: MediaToolInputRef | null = null;
		if (resolvedItemId) ref = { item_id: resolvedItemId };
		else if (pendingFileId) ref = { generation_file_id: pendingFileId };
		if (!ref) throw new Error(resourceReason || 'There is nothing to edit yet');

		const source = current.source;
		const key = source.key ?? source.itemId ?? source.generationFileId ?? source.storedPath ?? source.url;
		const handle = await executeMediaTool({
			label: plan.label,
			steps: plan.steps,
			sources: [{ id: key, kind: source.kind, url: source.url, filename: source.fileName }],
			refs: [ref],
			mode: plan.mode,
			announce: announce ?? detach
		});

		if (detach) {
			void handle.done.then((outcome) => {
				if (outcome.status === 'completed') void onResult(resultOf(plan, outcome), current);
			});
			return DETACHED;
		}

		activeRun = handle;
		runKey.set(key);
		const outcome = await handle.done;
		if (outcome.status === 'failed') throw new Error(outcome.message);
		if (outcome.status === 'cancelled') return null;
		return resultOf(plan, outcome);
	}
</script>

{#if request && kind}
	{#if isToolDialogKind(kind)}
		<ToolDialogHost {request} {detach} {onClose} {onResult} />
	{:else if kind === 'crop'}
		<CropEditor
			source={request.source}
			{busy}
			{allowReplace}
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			{onClose}
			{commit}
		/>
	{:else if kind === 'trim'}
		<TrimEditor
			source={request.source}
			{busy}
			{allowReplace}
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			{onClose}
			{commit}
			onExtractFrame={request.source.kind === 'video'
				? (time) => (frameHandoff = { time })
				: null}
		/>
	{:else if kind === 'resize'}
		<ResizeEditor
			source={request.source}
			{busy}
			{allowReplace}
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			{onClose}
			{commit}
		/>
	{:else if kind === 'split'}
		<SplitEditor
			source={request.source}
			{busy}
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			{onClose}
			{commit}
		/>
	{:else if kind === 'frame'}
		<FrameEditor
			source={request.source}
			{busy}
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			startTime={frameHandoff?.time ?? 0}
			onClose={closeFrameEditor}
			{commit}
		/>
	{:else if kind === 'paint'}
		{#await import('$lib/components/imageEditor/PaintEditor.svelte') then paint}
			<paint.default
				source={request.source}
				draw={request.draw ?? null}
				hasMask={existingMaskUrl !== null}
				{busy}
				failureMessage={failure}
				{onClose}
				{commit}
			/>
		{/await}
	{:else if kind === 'mask'}
		<MaskEditor
			source={request.source}
			{existingMaskUrl}
			{busy}
			failureMessage={failure}
			{onClose}
			{commit}
		/>
	{/if}
{/if}
