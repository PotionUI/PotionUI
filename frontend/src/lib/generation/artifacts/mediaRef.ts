/**
 * A saved run report keeps a reference to an artifact's bytes instead of the
 * bytes themselves; a live artifact still arrives over the WebSocket with its
 * payload inline. Both reach the same renderer, so image resolution accepts
 * either shape.
 */

/** Mirrors `RunReportArtifactStore.save()` (src/features/generation/run_report_artifacts.py). */
export interface RunReportMediaRef {
	$media: 'run_report_artifact';
	name: string;
	url: string;
	path: string;
	bytes: number;
	mime: string;
	width?: number;
	height?: number;
}

export type ArtifactImageValue = string | RunReportMediaRef | undefined | null;

export function isMediaRef(value: unknown): value is RunReportMediaRef {
	return (
		typeof value === 'object' &&
		value !== null &&
		(value as RunReportMediaRef).$media === 'run_report_artifact' &&
		typeof (value as RunReportMediaRef).url === 'string'
	);
}

export function resolveArtifactImageSrc(value: ArtifactImageValue, baseURL = ''): string {
	if (!value) return '';
	if (isMediaRef(value)) return `${baseURL}${value.url}`;
	if (value.startsWith('/api/')) return `${baseURL}${value}`;
	if (value.startsWith('http') || value.startsWith('data:')) return value;
	return `data:image/png;base64,${value}`;
}

/** Whether an artifact has an image to show at all - a saved report can carry
 * a reference where the live message carried a payload, and either can be
 * absent when the recorder dropped it. */
export function hasArtifactImage(value: ArtifactImageValue): boolean {
	return Boolean(value) && (isMediaRef(value) || typeof value === 'string');
}
