/**
 * Which face the field is showing, and which tools that face's toolbar carries.
 *
 * The field has nine of them (empty full / empty compact / image / video /
 * audio / multi one-kind / multi mixed / uploading / rejected) and they were
 * previously decided by a chain of `{#if}`s reading five loosely-related
 * variables, so "compact + multi + uploading" had never actually been looked
 * at. Deciding it here means each combination can be asserted without a
 * browser.
 */

import type { MediaKind, MediaLoaderLimits } from './mediaLoaderConfig';

export type MediaLoaderFace =
	| 'empty-full'
	| 'empty-compact'
	| 'uploading'
	| 'image'
	| 'video'
	| 'audio'
	| 'multi';

export interface FaceState {
	multiple: boolean;
	uploading: boolean;
	compact: boolean;
	/** The single-mode preview, once one resolved. */
	previewUrl: string | null;
	fileType: MediaKind | null;
}

/**
 * Multi mode keeps its own face while uploading — the spinner belongs to the
 * tile being added, not to the whole field, and the items already there must
 * stay visible and reorderable while it runs.
 */
export function selectFace(state: FaceState): MediaLoaderFace {
	if (state.multiple) return 'multi';
	if (state.uploading) return 'uploading';
	if (state.previewUrl && state.fileType) return state.fileType;
	return state.compact ? 'empty-compact' : 'empty-full';
}

export type MediaToolKey = 'crop' | 'mask' | 'trim' | 'frame' | 'full' | 'swap' | 'remove';

export interface MediaTool {
	key: MediaToolKey;
	label: string;
	title: string;
	tone: 'default' | 'danger';
	/** Icon-only tools keep their `title` as the only affordance. */
	showLabel: boolean;
	/** Pushed to the far end of the toolbar, away from the constructive tools. */
	pushRight: boolean;
}

export interface ToolbarOptions {
	allowInpaint: boolean;
	/** Compact hosts have no room for text; the toolbar collapses to icons. */
	compact: boolean;
	/**
	 * Mask needs the `${name}_inpaint_mask` sibling channel, which only
	 * preset-driven hosts wire — see MediaLoaderField's `onMaskChange`.
	 */
	canEmitMask: boolean;
}

function tool(
	key: MediaToolKey,
	label: string,
	title: string,
	options: { iconOnly?: boolean; tone?: 'danger'; right?: boolean; showLabels: boolean }
): MediaTool {
	return {
		key,
		label,
		title,
		tone: options.tone ?? 'default',
		showLabel: options.showLabels && !options.iconOnly,
		pushRight: options.right ?? false
	};
}

/**
 * The toolbar that sits UNDER the preview. Nothing here is overlay chrome on
 * the media itself: an overlay button covers the pixels the user is trying to
 * judge, and on a 340px sidebar it covers a meaningful fraction of them.
 */
export function toolbarTools(kind: MediaKind, options: ToolbarOptions): MediaTool[] {
	const showLabels = !options.compact;
	const tools: MediaTool[] = [];

	if (kind === 'image') {
		tools.push(tool('crop', 'Crop', 'Crop & frame', { showLabels }));
		if (options.allowInpaint && options.canEmitMask) {
			tools.push(tool('mask', 'Mask', 'Create inpainting mask', { showLabels }));
		}
		tools.push(tool('full', '', 'View full size', { iconOnly: true, showLabels }));
		tools.push(tool('swap', '', 'Replace media', { iconOnly: true, showLabels }));
	} else if (kind === 'video') {
		tools.push(tool('trim', 'Trim', 'Trim in / out', { showLabels }));
		tools.push(tool('frame', 'Frame', 'Extract a frame', { showLabels }));
		tools.push(tool('full', '', 'View full size', { iconOnly: true, showLabels }));
		tools.push(tool('swap', '', 'Replace media', { iconOnly: true, showLabels }));
	} else {
		tools.push(tool('trim', 'Trim', 'Trim on waveform', { showLabels }));
		tools.push(tool('swap', '', 'Replace audio', { iconOnly: true, showLabels }));
	}

	tools.push(tool('remove', '', 'Remove', { iconOnly: true, tone: 'danger', right: true, showLabels }));
	return tools;
}

/** The editor a per-item edit button opens, by kind. */
export function itemEditorFor(kind: MediaKind | null): { key: 'crop' | 'trim'; title: string } | null {
	if (kind === 'image') return { key: 'crop', title: 'Crop & frame' };
	if (kind === 'video') return { key: 'trim', title: 'Trim in / out' };
	if (kind === 'audio') return { key: 'trim', title: 'Trim on waveform' };
	return null;
}

/**
 * A multi field with more than one accepted kind renders one lane per kind
 * (face 07); a single-kind field is a plain grid (face 06). The distinction is
 * the accepted SET, not what happens to be loaded — otherwise the field
 * reshuffles itself the moment a second kind is added.
 */
export function usesLanes(limits: MediaLoaderLimits): boolean {
	return limits.multiple && limits.kinds.length > 1;
}
