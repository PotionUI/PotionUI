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

/** A `generations/<date>/<generation id>/<file>` storage key as the media
 * route serves it - the same last-two-segments convention `formMedia.ts` and
 * `generationOrchestrator.ts` use to turn a `file_path` into a URL. */
export function storageKeyToMediaUrl(value: string): string | null {
	const segments = value.split('/').filter((segment) => segment.length > 0);
	if (segments[0] !== 'generations' || segments.length < 3) return null;
	const filename = segments[segments.length - 1];
	const generationId = segments[segments.length - 2];
	return `/api/media/generations/${generationId}/${filename}`;
}

export function resolveArtifactImageSrc(value: ArtifactImageValue, baseURL = ''): string {
	if (!value) return '';
	if (isMediaRef(value)) return `${baseURL}${value.url}`;
	if (value.startsWith('/api/')) return `${baseURL}${value}`;
	if (value.startsWith('http') || value.startsWith('data:')) return value;
	const storageUrl = storageKeyToMediaUrl(value);
	if (storageUrl) return `${baseURL}${storageUrl}`;
	return `data:image/png;base64,${value}`;
}

/** Whether an artifact has an image to show at all - a saved report can carry
 * a reference where the live message carried a payload, and either can be
 * absent when the recorder dropped it. */
export function hasArtifactImage(value: ArtifactImageValue): boolean {
	return Boolean(value) && (isMediaRef(value) || typeof value === 'string');
}
