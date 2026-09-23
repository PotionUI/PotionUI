import type { GenerationFile } from '$lib/types/history';

export function showAlphaCheckerboard(file: Pick<GenerationFile, 'file_type' | 'has_alpha'>): boolean {
	return !!file.has_alpha && file.file_type.toLowerCase() === 'image';
}
