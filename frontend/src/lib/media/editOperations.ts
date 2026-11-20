import type { MediaToolStep } from '$lib/media-tools/types';
import type { CropOperationPayload } from './cropGeometry';
import type { TrimOperationPayload } from './editors/trimPoints';

export type ResizeQuality = 'high' | 'balanced' | 'small';

export interface ResizeOperationPayload {
	type: 'resize';
	width?: number;
	height?: number;
	quality?: ResizeQuality;
}

export interface RotateOperationPayload {
	type: 'rotate';
	degrees: 90 | 180 | 270;
}

export interface FlipOperationPayload {
	type: 'flip';
	axis: 'horizontal' | 'vertical';
}

export type EditOperation =
	| CropOperationPayload
	| ResizeOperationPayload
	| RotateOperationPayload
	| FlipOperationPayload
	| TrimOperationPayload;

export type EditableMediaKind = 'image' | 'video' | 'audio';

export type EditableActionType = EditOperation['type'] | 'split';

export const MAX_EDIT_OPERATIONS = 8;

export const OPERATIONS_FOR_KIND: Record<EditableMediaKind, readonly EditableActionType[]> = {
	image: ['crop', 'resize', 'rotate', 'flip'],
	video: ['trim', 'crop', 'resize', 'rotate', 'flip'],
	audio: ['trim', 'split']
};

const SUBJECT: Record<EditableMediaKind, string> = {
	image: 'An image',
	video: 'Video',
	audio: 'Audio'
};

const VERB: Record<EditableActionType, string> = {
	crop: 'cropped',
	resize: 'resized',
	rotate: 'rotated',
	flip: 'flipped',
	trim: 'trimmed',
	split: 'split'
};

/**
 * Why this list cannot be sent, or null when it can. Phrased for the user -
 * it is rendered next to the Apply button, not logged.
 */
export function describeRejection(
	operations: readonly EditOperation[],
	kind: EditableMediaKind
): string | null {
	if (operations.length === 0) return 'Nothing to apply yet.';
	if (operations.length > MAX_EDIT_OPERATIONS) {
		return `At most ${MAX_EDIT_OPERATIONS} changes can be applied at once.`;
	}

	const allowed = OPERATIONS_FOR_KIND[kind];
	if (!allowed) return `A ${kind} resource cannot be edited.`;

	for (const operation of operations) {
		if (!allowed.includes(operation.type)) {
			return `${SUBJECT[kind]} cannot be ${VERB[operation.type]}.`;
		}
	}

	if (operations.filter((operation) => operation.type === 'trim').length > 1) {
		return 'Only one trim can be applied at a time.';
	}

	return null;
}

export function operationsToSteps(operations: readonly EditOperation[]): MediaToolStep[] {
	return operations.map((operation): MediaToolStep => {
		switch (operation.type) {
			case 'trim':
				return {
					tool: 'trim',
					params: { start: operation.start_seconds, end: operation.end_seconds }
				};
			case 'crop':
				return {
					tool: 'crop',
					params: {
						x: operation.x,
						y: operation.y,
						width: operation.width,
						height: operation.height
					}
				};
			case 'resize': {
				const params: Record<string, unknown> = {};
				if (operation.width !== undefined) params.width = operation.width;
				if (operation.height !== undefined) params.height = operation.height;
				if (operation.quality !== undefined) params.quality = operation.quality;
				return { tool: 'resize', params };
			}
			case 'rotate':
				return { tool: 'rotate', params: { degrees: operation.degrees } };
			case 'flip':
				return { tool: 'flip', params: { axis: operation.axis } };
		}
	});
}
