import type {
	PresetAdminDetail,
	PresetAdminModeOption,
	PresetDisplayPayload,
	PresetMirrorModesPayload
} from '$lib/types/api';
import { assignFieldErrors, cleanFieldErrors, type PresetApiFailure } from './presetApiFailure';

export const DISPLAY_NAME_MAX = 80;
export const DISPLAY_DESCRIPTION_MAX = 4000;

export type DisplayField = 'name' | 'description';

export interface DisplayDraft {
	name: string;
	description: string;
}

export interface ModesDraft {
	offered: string[];
	first: string;
}

export function displayDraftFrom(detail: Pick<PresetAdminDetail, 'display'>): DisplayDraft {
	return {
		name: detail.display.name ?? '',
		description: detail.display.description ?? ''
	};
}

export function normalizedName(value: string): string | null {
	const trimmed = value.trim();
	return trimmed ? trimmed : null;
}

export function normalizedDescription(value: string): string | null {
	return value.trim() ? value : null;
}

export function displayDirtyKeys(draft: DisplayDraft, detail: Pick<PresetAdminDetail, 'display'>): DisplayField[] {
	const keys: DisplayField[] = [];
	if (normalizedName(draft.name) !== (detail.display.name ?? null)) keys.push('name');
	if (normalizedDescription(draft.description) !== (detail.display.description ?? null)) keys.push('description');
	return keys;
}

export function validateDisplayDraft(draft: DisplayDraft, isMirror: boolean): Partial<Record<DisplayField, string>> {
	const errors: Partial<Record<DisplayField, string>> = {};
	const name = normalizedName(draft.name);
	if (!name && isMirror) errors.name = 'A mirror needs a name.';
	else if (name && name.length > DISPLAY_NAME_MAX) errors.name = `Keep the name to ${DISPLAY_NAME_MAX} characters.`;
	if (draft.description.length > DISPLAY_DESCRIPTION_MAX) {
		errors.description = `Keep the description to ${DISPLAY_DESCRIPTION_MAX} characters.`;
	}
	return errors;
}

export function buildDisplayPayload(
	draft: DisplayDraft,
	cover: string | null
): PresetDisplayPayload {
	return {
		name: normalizedName(draft.name),
		description: normalizedDescription(draft.description),
		cover
	};
}

function availableNames(available: readonly PresetAdminModeOption[]): string[] {
	return available.map((mode) => mode.name);
}

export function orderedOffered(offered: readonly string[], available: readonly PresetAdminModeOption[]): string[] {
	const chosen = new Set(offered);
	return availableNames(available).filter((name) => chosen.has(name));
}

export function modesDraftFrom(modes: PresetAdminDetail['modes']): ModesDraft {
	const all = availableNames(modes.available);
	const offered = modes.offered === null ? all : orderedOffered(modes.offered, modes.available);
	const first = modes.first && offered.includes(modes.first) ? modes.first : (offered[0] ?? '');
	return { offered, first };
}

export function modesDirtyCount(draft: ModesDraft, modes: PresetAdminDetail['modes']): number {
	const saved = modesDraftFrom(modes);
	const ordered = orderedOffered(draft.offered, modes.available);
	let count = 0;
	if (ordered.join('\u0000') !== saved.offered.join('\u0000')) count += 1;
	if (draft.first !== saved.first) count += 1;
	return count;
}

export function validateModesDraft(draft: ModesDraft): string | null {
	if (draft.offered.length === 0) return 'Pick at least one mode.';
	if (!draft.first || !draft.offered.includes(draft.first)) return 'Pick which offered mode opens first.';
	return null;
}

export function withOfferedToggled(draft: ModesDraft, next: readonly string[], available: readonly PresetAdminModeOption[]): ModesDraft {
	const offered = orderedOffered(next, available);
	const first = offered.includes(draft.first) ? draft.first : (offered[0] ?? '');
	return { offered, first };
}

export function buildModesPayload(draft: ModesDraft, available: readonly PresetAdminModeOption[]): PresetMirrorModesPayload {
	const offered = orderedOffered(draft.offered, available);
	const everything = offered.length === available.length;
	return {
		offered: everything ? null : offered,
		first: draft.first || null
	};
}

export type DisplayServerField = 'name' | 'description' | 'cover' | 'modes';

export interface DisplayEditor {
	detail: PresetAdminDetail;
	draft: DisplayDraft;
	modes: ModesDraft;
	attempted: boolean;
	serverErrors: Partial<Record<DisplayServerField, string>>;
	generalErrors: string[];
}

export function displayEditorFor(detail: PresetAdminDetail): DisplayEditor {
	return {
		detail,
		draft: displayDraftFrom(detail),
		modes: modesDraftFrom(detail.modes),
		attempted: false,
		serverErrors: {},
		generalErrors: []
	};
}

export function displayEditorDirtyCount(editor: DisplayEditor): number {
	const modes = editor.detail.is_mirror ? modesDirtyCount(editor.modes, editor.detail.modes) : 0;
	return displayDirtyKeys(editor.draft, editor.detail).length + modes;
}

export function discardDisplayEditor(editor: DisplayEditor): DisplayEditor {
	return displayEditorFor(editor.detail);
}

export function displayEditorSaved(_editor: DisplayEditor, next: PresetAdminDetail): DisplayEditor {
	return displayEditorFor(next);
}

export function displayEditorFailed(
	editor: DisplayEditor,
	failure: Pick<PresetApiFailure, 'code' | 'message' | 'errors'>
): { editor: DisplayEditor; toast: string | null } {
	if (failure.code === 'invalid_modes') {
		return {
			editor: { ...editor, serverErrors: { ...editor.serverErrors, modes: cleanFieldErrors(failure.errors) || failure.message } },
			toast: null
		};
	}
	const assigned = assignFieldErrors(failure.errors, ['name', 'description', 'cover'] as const);
	const placed = Object.keys(assigned.byField).length > 0 || assigned.general.length > 0;
	return {
		editor: {
			...editor,
			serverErrors: { ...editor.serverErrors, ...assigned.byField },
			generalErrors: assigned.general
		},
		toast: placed ? null : failure.message
	};
}
