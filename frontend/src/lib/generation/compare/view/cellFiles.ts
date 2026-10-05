import { api } from '$lib/services/api/index';
import { leadIndex } from '$lib/generation/leadFile';
import type { GenerationFile } from '$lib/types/history';
import type { ActiveGrid } from '../compareStore.svelte';
import type { CellMediaFile } from './stitchCompareSource';

export function pickLeadFile(
	files: GenerationFile[],
	kind: 'image' | 'video' = 'image'
): { file: GenerationFile; index: number } | null {
	const finals = files
		.map((file, index) => ({ file, index }))
		.filter(({ file }) => file.is_final !== false && (file.file_type ?? '').toLowerCase() === kind);
	if (finals.length === 0) return null;
	return finals[leadIndex(finals.map(({ file }) => file))];
}

export async function resolveCellFiles(
	grid: Pick<ActiveGrid, 'cells'>,
	kind: 'image' | 'video' = 'image'
): Promise<Record<string, CellMediaFile>> {
	const entries = await Promise.all(
		grid.cells
			.filter((cell) => cell.status === 'completed' && cell.generationId)
			.map(async (cell) => {
				const id = cell.generationId as string;
				try {
					const response = await api.getGenerationById(id, false, true);
					const files = (response.success ? response.data?.files : null) as GenerationFile[] | null;
					const lead = files ? pickLeadFile(files, kind) : null;
					if (!lead) return null;
					const filename = lead.file.file_path.split('/').pop() || lead.file.file_path;
					const entry: CellMediaFile = {
						url: api.getGenerationImageURL(id, filename),
						filename,
						width: lead.file.width,
						height: lead.file.height,
						paramIndex: lead.index
					};
					return [id, entry] as const;
				} catch {
					return null;
				}
			})
	);
	const files: Record<string, CellMediaFile> = {};
	for (const entry of entries) if (entry) files[entry[0]] = entry[1];
	return files;
}
