/**
 * Moving generated media into the Library.
 *
 * "Move" is a copy: the backend writes fresh bytes into the user's uploads and
 * drops every generation field on the way
 * (`LibraryManager.copy_generation_file`), so the original generation is left
 * exactly as it was. The UI has to say so - a user who reads "move" and then
 * finds the generation still in their history has been told the wrong thing.
 */

export interface CopyableGenerationFile {
	id: number | string;
	file_type: string;
	is_final?: boolean;
}

export interface CopyableGeneration {
	files?: CopyableGenerationFile[];
}

/** The media kinds the library holds - mirrors `LIBRARY_MEDIA_TYPES` server-side. */
const COPYABLE_FILE_TYPES = new Set(['image', 'video', 'audio']);

/**
 * The files of the given generations that the library will accept, in the order
 * they appear. Intermediate (`is_final === false`) files and kinds the library
 * has no place for - a mesh, say - are dropped here rather than sent and
 * refused one 400 at a time.
 */
export function collectCopyableFileIds(generations: CopyableGeneration[]): string[] {
	const ids: string[] = [];
	const seen = new Set<string>();

	for (const generation of generations) {
		for (const file of generation.files ?? []) {
			if (file.is_final === false) continue;
			if (!COPYABLE_FILE_TYPES.has((file.file_type ?? '').toLowerCase())) continue;
			const id = String(file.id);
			if (seen.has(id)) continue;
			seen.add(id);
			ids.push(id);
		}
	}

	return ids;
}

/**
 * Feedback for a batch copy. Always says "copied", never "moved", and reports a
 * partial failure rather than rounding it up to success.
 */
export function summarizeCopyOutcome(copied: number, failed: number): string {
	if (copied === 0 && failed === 0) return 'Nothing to copy to Library';
	if (copied === 0) return `Failed to copy ${failed} file${failed === 1 ? '' : 's'} to Library`;
	const noun = `${copied} file${copied === 1 ? '' : 's'}`;
	if (failed > 0) return `Copied ${noun} to Library, ${failed} failed`;
	return `Copied ${noun} to Library`;
}
