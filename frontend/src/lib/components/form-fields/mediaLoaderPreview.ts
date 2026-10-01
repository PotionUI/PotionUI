export type MediaPathLocation =
	| { kind: 'upload'; filename: string }
	| { kind: 'tmp'; filename: string }
	| { kind: 'generation'; generationId: string; filename: string };

export function locateMediaPath(rawPath: string): MediaPathLocation | null {
	const segments = rawPath.split('/').filter((s) => s.length > 0);
	if (segments.length === 0) return null;
	const filename = segments[segments.length - 1];
	const parent = segments.length >= 2 ? segments[segments.length - 2] : null;

	if (parent === 'uploads') return { kind: 'upload', filename };
	if (parent === 'tmp') return { kind: 'tmp', filename };
	if (rawPath.startsWith('/') && !segments.includes('generations')) return { kind: 'upload', filename };
	if (parent === null) return null;
	return { kind: 'generation', generationId: parent, filename };
}

export function mediaPathPreviewUrl(rawPath: string): string | null {
	const location = locateMediaPath(rawPath);
	if (!location) return null;
	if (location.kind === 'upload') return `/api/media/uploads/${location.filename}`;
	if (location.kind === 'tmp') return `/api/media/tmp/${location.filename}`;
	return `/api/media/generations/${location.generationId}/${location.filename}`;
}
