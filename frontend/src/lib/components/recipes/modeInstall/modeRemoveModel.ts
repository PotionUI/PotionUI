import type { ModeRemovalFile, ModeRemovalPreview } from '$lib/services/api/recipeModes';
import { joinLabels } from './modeInstallModel';

export interface KeptGroup {
	key: string;
	title: string;
	reason: string;
	bytes: number;
}

export function groupKept(keep: readonly ModeRemovalFile[]): KeptGroup[] {
	const groups = new Map<string, { labels: string[]; reason: string; bytes: number; single: string | null }>();
	for (const file of keep) {
		const standalone = file.reason_code === 'outside_models_folder' || file.reason_code === 'not_on_disk';
		const key = standalone ? `file:${file.model_id}` : `${file.reason_code ?? ''}:${file.reason ?? ''}`;
		const entry = groups.get(key) ?? {
			labels: [],
			reason: file.reason ?? '',
			bytes: 0,
			single: standalone ? file.filename : null
		};
		if (!entry.labels.includes(file.label)) entry.labels.push(file.label);
		entry.bytes += file.size_bytes ?? 0;
		groups.set(key, entry);
	}
	return [...groups.entries()].map(([key, entry]) => ({
		key,
		title: entry.single ?? joinLabels(entry.labels.map((label, index) => (index === 0 ? label : lowerFirst(label)))),
		reason: entry.reason,
		bytes: entry.bytes
	}));
}

function lowerFirst(text: string): string {
	return text ? text[0].toLowerCase() + text.slice(1) : text;
}

export function removalDeleteIds(preview: ModeRemovalPreview): string[] {
	return preview.delete.map((file) => file.model_id);
}

export function removeConfirmLabel(preview: ModeRemovalPreview | null): string {
	const count = preview?.delete.length ?? 0;
	if (count === 0) return 'Nothing to remove';
	return `Remove ${count} ${count === 1 ? 'file' : 'files'}`;
}
