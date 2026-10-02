export type MediaFieldFace = 'dropzone' | 'inspector' | 'strip' | 'row';

export const NARROW_FIELD_WIDTH = 240;

export interface FaceInput {
	multiple: boolean;
	compact: boolean;
	fill: boolean;
	compactFullWidth: boolean;
	width: number | null;
	itemCount: number;
	mixedKinds: boolean;
	uploading: boolean;
}

export function usesRowFace(input: Pick<FaceInput, 'compact' | 'fill' | 'compactFullWidth' | 'width'>): boolean {
	if (input.compact || input.fill || input.compactFullWidth) return true;
	return input.width !== null && input.width > 0 && input.width < NARROW_FIELD_WIDTH;
}

export function chooseFace(input: FaceInput): MediaFieldFace {
	if (usesRowFace(input)) return 'row';
	if (input.multiple && input.mixedKinds) return 'strip';
	if (input.itemCount > 0 || input.uploading) return input.multiple ? 'strip' : 'inspector';
	return 'dropzone';
}
