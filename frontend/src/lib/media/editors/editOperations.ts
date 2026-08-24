/**
 * The operation list an edit is, and what each medium will accept.
 *
 * Mirrors `validate_operations` in `src/features/media/editing/operations.py`.
 * The server checks all of this again - what this buys is a refusal the user
 * can act on (a disabled Apply with a reason) instead of a round trip that
 * comes back 400.
 */

import type { CropOperationPayload } from './cropGeometry';
import type { TrimOperationPayload } from './trimPoints';

export interface ResizeOperationPayload {
	type: 'resize';
	width?: number;
	height?: number;
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

/**
 * Every `EditOperation` type, plus `'split'` - which is NOT one. Split hits
 * its own endpoint (`POST /api/media/split/{item_id}`, `{part_seconds}`) and
 * returns several items, never the single-item shape `/edit`'s operations
 * produce, so it can never appear inside an `EditOperation[]` sent to
 * `editMediaItem`. It still belongs in this table: this is the one place that
 * knows what a media kind supports, and split is one of those things.
 */
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
