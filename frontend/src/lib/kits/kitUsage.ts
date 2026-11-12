import type { KitUploadUsage } from './types';

export function kitsUsingUploads(usage: Record<string, KitUploadUsage> | null | undefined): number {
	const kits = new Set<string>();
	for (const entry of Object.values(usage ?? {})) for (const item of entry.items ?? []) kits.add(item.kit_id);
	return kits.size;
}

export function kitUseSentence(count: number, many = false): string {
	if (count <= 0) return '';
	const where = `${count} ${count === 1 ? 'kit' : 'kits'}`;
	return many
		? ` Some of them are used in ${where}; those kit items will show "File deleted".`
		: ` It is used in ${where}; that kit item will show "File deleted".`;
}
