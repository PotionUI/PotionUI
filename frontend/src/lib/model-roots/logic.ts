import type {
	ModelRoot,
	ModelRootBinding,
	ModelRootDetection,
	ModelRootDetectionSuggestion
} from '$lib/services/api/models';

export function orderedRoots(roots: ModelRoot[]): ModelRoot[] {
	return [...roots].sort((a, b) => (a.kind === 'home' ? -1 : b.kind === 'home' ? 1 : 0));
}

export function bindingsSummary(root: ModelRoot): { files: number; bytes: number } {
	return root.bindings.reduce(
		(acc, b) => ({ files: acc.files + b.indexed_files, bytes: acc.bytes + b.size_bytes }),
		{ files: 0, bytes: 0 }
	);
}

export function mergeDetectionSuggestions(
	detection: ModelRootDetection | null
): ModelRootDetectionSuggestion[] {
	if (!detection) return [];
	if (detection.layout === 'typed') return detection.suggestions;
	if (detection.layout === 'single' && detection.single_type_guess) {
		return [
			{
				model_type: detection.single_type_guess,
				subdir: '',
				matched_by: 'canonical',
				file_count: 0,
				file_count_truncated: false
			}
		];
	}
	return [];
}

export const HEADER_SCAN_TYPES: readonly string[] = ['checkpoint', 'diffusion_model', 'unet'];

export function bindingSupportsHeaderScan(binding: Pick<ModelRootBinding, 'model_type'>): boolean {
	return HEADER_SCAN_TYPES.includes(binding.model_type);
}

export function bindingScanKey(rootId: string, binding: Pick<ModelRootBinding, 'model_type' | 'subdir'>): string {
	return `${rootId}:${binding.model_type}:${binding.subdir}`;
}
