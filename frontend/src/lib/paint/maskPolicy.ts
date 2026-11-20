export interface MaskFateInput {
	hasMask: boolean;
	geometryChanged: boolean;
	sourceReplaced: boolean;
}

export type MaskFate = 'none' | 'keep' | 'clear';

export interface MaskDecision {
	fate: MaskFate;
	notice: string | null;
}

export function decideMaskFate(input: MaskFateInput): MaskDecision {
	if (!input.hasMask) return { fate: 'none', notice: null };
	if (input.sourceReplaced) {
		return {
			fate: 'clear',
			notice: 'Inpaint mask will be cleared: a different image was opened in the editor.'
		};
	}
	if (input.geometryChanged) {
		return {
			fate: 'clear',
			notice: 'Inpaint mask will be cleared: the image was cropped, resized, rotated or flipped.'
		};
	}
	return {
		fate: 'keep',
		notice: 'Inpaint mask is kept: the image geometry is unchanged.'
	};
}
