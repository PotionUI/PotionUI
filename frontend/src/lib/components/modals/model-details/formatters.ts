// Small formatting helpers shared by the model-details modal pieces (user + admin).

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
