const ILLEGAL = /[\\/:*?"<>|\u0000-\u001f]/g;

export function baseName(fileName: string): string {
	const trimmed = fileName.trim();
	const dot = trimmed.lastIndexOf('.');
	const stem = dot > 0 ? trimmed.slice(0, dot) : trimmed;
	return stem || 'image';
}

export function sanitizeFileStem(value: string): string {
	const cleaned = value.replace(ILLEGAL, '-').replace(/\s+/g, ' ').trim().replace(/^\.+/, '');
	return cleaned.slice(0, 80).trim();
}

export function defaultExportStem(sourceName: string | null): string {
	if (!sourceName) return 'drawing';
	const stem = sanitizeFileStem(baseName(sourceName));
	return `${stem || 'image'}-edit`;
}

export function exportFileName(stem: string, fallback: string = 'edit'): string {
	const cleaned = sanitizeFileStem(stem.replace(/\.png$/i, ''));
	return `${cleaned || fallback}.png`;
}
