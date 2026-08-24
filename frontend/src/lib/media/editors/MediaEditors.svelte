<script lang="ts">
	/**
	 * The one place the media editors are mounted.
	 *
	 * Both consumers - the MediaLoader form field and the Library page - open
	 * the same four surfaces on the same source shape, and neither of them
	 * should know how an edit is actually performed. That is decided here, once:
	 *
	 * - crop, trim and frame send GEOMETRY to the edit API and get a real
	 *   library resource back. There is no in-browser re-encode of a video or an
	 *   audio container, and a rendered file re-uploaded would be a new resource
	 *   that lost the original's tags and collections - which is the whole point
	 *   of the replace-in-place the API supports;
	 * - a mask is painted over the media rather than applied to it, so it is
	 *   stored as its own file and travels as a PATH on the sibling channel.
	 *
	 * Because every edit is server-side, every source needs a library resource
	 * behind it. A Library row already is one; a field value pointing into
	 * `uploads/` is one that has to be found; a generated file is not one at all
	 * and is copied into the library first, which the editor says out loud
	 * rather than doing silently.
	 *
	 * Nothing that leaves here is a blob URL - `URL.createObjectURL` handles die
	 * with the document, and a persisted one is a reference that 404s after a
	 * refresh. Results carry the served URL the API returned.
	 */
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import CropEditor from './CropEditor.svelte';
	import TrimEditor from './TrimEditor.svelte';
	import FrameEditor from './FrameEditor.svelte';
	import MaskEditor from './MaskEditor.svelte';
	import SplitEditor from './SplitEditor.svelte';
	import { resolveEditableResource, type ResourceOrigin } from './editorSource';
	import { describeEditFailure } from './editErrors';
	import { dataUrlToFile } from './maskFile';
	import {
		editsTheResource,
		type EditorCommitRequest,
		type MediaEditorKind,
		type MediaEditorRequest,
		type MediaEditorResult
	} from './types';

	/** The open editor, or null. Setting it to null is how a host closes one. */
	export let request: MediaEditorRequest | null = null;
	/** A mask already held for this image, so the mask editor resumes it. */
	export let existingMaskUrl: string | null = null;
	export let onClose: () => void;
	export let onResult: (
		result: MediaEditorResult,
		request: MediaEditorRequest
	) => void | Promise<void>;

	let busy = false;
	let failure: string | null = null;
	let resolvedItemId: string | null = null;
	let resourceOrigin: ResourceOrigin | null = null;
	let resourceReason: string | null = null;
	let resolving = false;
	/** Set when the trim editor hands the clip over at a playhead. */
	let frameHandoff: { time: number } | null = null;
	let lastRequest: MediaEditorRequest | null = null;

	$: kind = (frameHandoff ? 'frame' : (request?.kind ?? null)) as MediaEditorKind | null;

	// A new request is a new editor: nothing from the previous one survives it.
	$: if (request) resetFor(request);

	function resetFor(next: MediaEditorRequest) {
		if (next === lastRequest) return;
		lastRequest = next;
		busy = false;
		failure = null;
		frameHandoff = null;
		resolvedItemId = null;
		resourceOrigin = null;
		resourceReason = null;
		resolving = false;

		// A mask changes no resource, so it needs no row and pays for no lookup.
		if (editsTheResource(next.kind)) void resolveResource(next);
	}

	async function resolveResource(next: MediaEditorRequest) {
		resolving = true;
		try {
			const resolved = await resolveEditableResource(
				next.source.itemId,
				next.source.storedPath,
				next.source.kind
			);
			// A slow lookup that lands after the user moved on must not attach
			// this resource to whatever is open now.
			if (lastRequest !== next) return;
			resolvedItemId = resolved.itemId;
			resourceOrigin = resolved.origin;
			resourceReason = resolved.reason;
		} finally {
			if (lastRequest === next) resolving = false;
		}
	}

	// Held up only while there is nothing to edit yet. `resolving` is a state,
	// not a failure - saying "could not be added" while the copy is still in
	// flight would be wrong for the second it takes.
	$: blockedReason = !kind || !editsTheResource(kind)
		? null
		: resolvedItemId
			? null
			: resolving
				? 'Preparing this media…'
				: resourceReason;

	/**
	 * Named when the editors made the resource themselves. Editing a generated
	 * file means adding it to the library, and a user who did not ask for that
	 * should still be told it happened.
	 */
	$: resourceNote =
		resourceOrigin === 'copied'
			? 'This was generated, so a copy was added to your library — the generation is untouched.'
			: null;

	/**
	 * Cancelling a frame grab the trim editor handed over goes BACK to the trim
	 * editor, not out of editing altogether - the user never left it.
	 */
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
			const result = await produce(commitRequest);
			await onResult(result, current);
			onClose();
		} catch (error) {
			logger.error('Media edit failed:', error);
			failure = describeEditFailure(error);
		} finally {
			busy = false;
		}
	}

	async function produce(commitRequest: EditorCommitRequest): Promise<MediaEditorResult> {
		if (commitRequest.via === 'mask') {
			// Stored as its own file so the sibling channel can carry a path -
			// the mask's identity is keyed on a path, never on a blob handle.
			const stored = await api.uploadMedia(
				dataUrlToFile(commitRequest.dataUrl, `mask-${Date.now()}.png`),
				'derived_artifact'
			);
			if (!stored.success || !stored.data) {
				throw new Error(stored.message || 'The mask could not be saved');
			}
			return { type: 'mask', maskPath: stored.data.path };
		}

		if (!resolvedItemId) throw new Error(resourceReason || 'There is nothing to edit yet');

		if (commitRequest.via === 'split') {
			const split = await api.splitMediaItem(resolvedItemId, commitRequest.partSeconds);
			if (!split.success || !split.data) {
				throw new Error(split.message || 'The split could not be applied');
			}
			return { type: 'items', items: split.data.items };
		}

		const response =
			commitRequest.via === 'operations'
				? await api.editMediaItem(resolvedItemId, commitRequest.operations, commitRequest.mode)
				: await api.extractMediaFrame(resolvedItemId, commitRequest.timeSeconds);

		if (!response.success || !response.data) {
			throw new Error(response.message || 'The edit could not be applied');
		}
		return { type: 'item', item: response.data.item, replaced: response.data.replaced };
	}
</script>

{#if request && kind}
	{#if kind === 'crop'}
		<CropEditor
			source={request.source}
			{busy}
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
			{blockedReason}
			{resourceNote}
			failureMessage={failure}
			{onClose}
			{commit}
			onExtractFrame={request.source.kind === 'video'
				? (time) => (frameHandoff = { time })
				: null}
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
	{:else}
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
