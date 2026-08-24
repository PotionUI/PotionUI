/**
 * Provenance index for a media value picked out of generation history.
 *
 * The picker addresses a file by identity, never by position: the card's
 * carousel is filtered (`is_final`, nsfw) and re-ordered
 * (images→videos→audio→mesh), so a position in it is not a position in
 * `generation.files`. The `<field>__origin` sibling the backend stores
 * (`{generation_id, file_index}`) does want a position, so it is derived here
 * from the file's own identity against the UNFILTERED files array.
 */

interface FileLike {
	id?: number | string;
	file_path?: string;
}

/**
 * Index of `file` within `generation.files`, or -1 when it cannot be located.
 * `id` is the identity; `file_path` is the fallback for payloads that carry no
 * id. A -1 means "no provenance", not "index 0" - attributing the wrong file's
 * params is worse than attributing none.
 */
export function originFileIndex(
	generation: { files?: FileLike[] | null } | null | undefined,
	file: FileLike | null | undefined
): number {
	const files = generation?.files;
	if (!files || !file) return -1;

	if (file.id !== undefined && file.id !== null) {
		const byId = files.findIndex((candidate) => candidate?.id === file.id);
		if (byId !== -1) return byId;
	}
	if (file.file_path) {
		return files.findIndex((candidate) => candidate?.file_path === file.file_path);
	}
	return -1;
}
