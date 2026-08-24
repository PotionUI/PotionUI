// Small formatting helpers shared by the model-details modal pieces (user + admin).

import { copyText } from '$lib/utils/clipboard';

export function formatBytes(bytes?: number | null): string {
	if (!bytes) return 'Unknown';
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDate(dateString?: string | null): string {
	if (!dateString) return 'N/A';
	return new Date(dateString).toLocaleString();
}

export function copyToClipboard(text: string): void {
	void copyText(text);
}
