import type { Segment } from '$lib/types/segments';
import { segmentFooterActions, type SegmentFooterActionId } from './segmentFooter';

export type SegmentActionId =
	| SegmentFooterActionId
	| 'moveUp'
	| 'moveDown'
	| 'replaceFromSaved'
	| 'insertPhrasebook'
	| 'insertVariable'
	| 'insertChoice'
	| 'insertSyntax';

export interface SegmentAction {
	id: SegmentActionId;
	label: string;
	icon?: string;
	glyph?: string;
	glyphClass?: string;
	disabled?: boolean;
}

export interface SegmentActionContext {
	index: number;
	total: number;
	segmentDisabled: boolean;
	hasPromptSyntax?: boolean;
}

export function segmentMenuActions(segment: Segment, context: SegmentActionContext): SegmentAction[] {
	const { index, total, segmentDisabled, hasPromptSyntax = false } = context;
	const footer = segmentFooterActions(segment);
	const byId = (id: SegmentFooterActionId): SegmentAction => {
		const action = footer.find((a) => a.id === id);
		if (!action) throw new Error(`segmentFooterActions missing '${id}'`);
		return action;
	};

	const actions: SegmentAction[] = [
		{ id: 'moveUp', label: 'Move up', icon: 'chevron-up', disabled: index === 0 },
		{ id: 'moveDown', label: 'Move down', icon: 'chevron-down', disabled: index >= total - 1 },
		byId('editDetails'),
		byId('saveAsSegment'),
		{ id: 'replaceFromSaved', label: 'Replace from saved', icon: 'library' },
		byId('duplicate'),
		byId('toggleDisabled'),
		{ id: 'insertPhrasebook', label: 'Insert a phrasebook value', glyph: '#', glyphClass: 'phrasebook', disabled: segmentDisabled },
		{ id: 'insertVariable', label: 'Insert a variable', glyph: '$', glyphClass: 'variable', disabled: segmentDisabled },
		{ id: 'insertChoice', label: 'Insert a choice group', glyph: '{}', glyphClass: 'choice', disabled: segmentDisabled }
	];

	if (hasPromptSyntax) {
		actions.push({ id: 'insertSyntax', label: 'Insert syntax…', glyph: '/', glyphClass: 'syntax', disabled: segmentDisabled });
	}

	return actions;
}

export const SEGMENT_ACTION_GROUPS: SegmentActionId[][] = [
	['moveUp', 'moveDown'],
	['editDetails', 'saveAsSegment', 'replaceFromSaved'],
	['duplicate', 'toggleDisabled'],
	['insertPhrasebook', 'insertVariable', 'insertChoice', 'insertSyntax']
];
