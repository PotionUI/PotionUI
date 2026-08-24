/**
 * The media editors — one surface, two consumers.
 *
 * Mount `MediaEditors` and hand it a request; everything else here is the
 * contract and the arithmetic behind it, exported so a host can build a source
 * or decide which tools to offer without reaching into a component.
 */

export { default as MediaEditors } from './MediaEditors.svelte';

export type {
	EditorCommitFn,
	EditorCommitRequest,
	EditorMediaKind,
	MediaEditorKind,
	MediaEditorRequest,
	MediaEditorResult,
	MediaEditorSaveMode,
	MediaEditorSource
} from './types';
export {
	editorTitle,
	editsTheResource,
	hasEditor,
	MEDIA_EDITOR_KINDS,
	RESOURCE_EDIT_TOOLS
} from './types';

export { resolveEditableResource, uploadFilenameFromPath } from './editorSource';
export { describeEditFailure } from './editErrors';
export { formatClipLength, formatPreciseTime, formatTimecode } from './timecode';
export { computeSplitPlan, describeSplitPlan, describeSplitRejection } from './splitPlan';
