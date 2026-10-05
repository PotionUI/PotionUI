import { get } from 'svelte/store';
import type { LimitRow } from './meApi';
import { UPLOAD_FILE_SIZE_KIND, uploadSizeMessage } from './refusal';
import { limits, limitsMeta } from './store';

export function uploadSizeBlock(
	fileBytes: number,
	rows: readonly LimitRow[],
	contactLine?: string | null
): string | null {
	const row = rows.find((candidate) => candidate.kind === UPLOAD_FILE_SIZE_KIND);
	if (!row || !row.enforced) return null;
	return fileBytes > row.limit ? uploadSizeMessage(fileBytes, row.limit, contactLine) : null;
}

export function blockedUploadMessage(files: readonly Pick<File, 'size'>[]): string | null {
	const rows = get(limits);
	const contactLine = get(limitsMeta)?.contactLine ?? null;
	for (const file of files) {
		const message = uploadSizeBlock(file.size, rows, contactLine);
		if (message) return message;
	}
	return null;
}
