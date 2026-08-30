export interface AttachedMediaThumb {
	url: string;
	name?: string;
}

/**
 * First attached media-loader value in a preset's form data, used for the
 * Studio dock's "media attached" thumbnail chip (img2img source, a
 * ControlNet reference, ...). Field NAMES vary per preset — there's no fixed
 * "source image" key — so this scans values by SHAPE instead: the one
 * contract every MediaLoaderField value shares (see MediaLoaderField.svelte's
 * `mediaItem` construction), single or `multi: true`, is an object (or array
 * of objects) carrying a `url` and/or `path`.
 */
export function findAttachedMediaThumb(
	formData: Record<string, unknown> | null | undefined
): AttachedMediaThumb | null {
	if (!formData) return null;
	for (const raw of Object.values(formData)) {
		const value = Array.isArray(raw) ? raw[0] : raw;
		if (value && typeof value === 'object') {
			const ref = value as { url?: unknown; path?: unknown; name?: unknown };
			const url =
				typeof ref.url === 'string' && ref.url ? ref.url : typeof ref.path === 'string' ? ref.path : '';
			if (url) return { url, name: typeof ref.name === 'string' ? ref.name : undefined };
		}
	}
	return null;
}
