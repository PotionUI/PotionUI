import type { MediaRef, Tab } from '$lib/types/tabs';
import type { DirectorMediaValue, VideoDirectorValue } from '$lib/types/videoDirector';
import { resolveDirectorMediaDisplay } from '$lib/utils/videoDirector';

export interface FormImageEntry {
	key: string;
	label: string;
	media: MediaRef;
	url: string;
}

const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'];

function filenameOf(raw: string): string {
	const segments = raw.split('/').filter((s) => s.length > 0);
	return segments[segments.length - 1] || raw;
}

function hasImageExtension(raw: string): boolean {
	const ext = filenameOf(raw).split('.').pop()?.toLowerCase();
	return !!ext && IMAGE_EXTENSIONS.includes(ext);
}

function durablePath(media: MediaRef | string): string {
	if (typeof media === 'string') return media;
	return media.relative_path || media.path || '';
}

function isImageMedia(media: MediaRef | string): boolean {
	if (typeof media === 'string') return hasImageExtension(media);
	if (media.type) return media.type === 'image';
	return hasImageExtension(durablePath(media));
}

function isMediaLike(value: unknown): value is MediaRef {
	return (
		typeof value === 'object' &&
		value !== null &&
		!Array.isArray(value) &&
		(typeof (value as MediaRef).path === 'string' || typeof (value as MediaRef).relative_path === 'string')
	);
}

function toMediaRef(media: MediaRef | string): MediaRef {
	if (typeof media === 'string') return { path: media };
	return {
		path: media.path || media.relative_path || '',
		relative_path: media.relative_path,
		url: media.url,
		name: media.name,
		type: media.type
	};
}

// Mirrors MediaLoaderField's own locator resolution (parseMediaLocator +
// legacy string branch): `value.url` is trusted only when it is already a
// durable `/api/...` URL — a `blob:` object URL is ephemeral and resolved
// from the path instead.
function resolveMediaUrl(media: MediaRef | string): string {
	if (typeof media === 'object' && media.url && media.url.startsWith('/api/')) {
		return media.url;
	}

	const raw = durablePath(media);
	if (!raw) return '';

	const filename = filenameOf(raw);
	const segments = raw.split('/').filter((s) => s.length > 0);

	if (raw.includes('/tmp/') || segments[0] === 'tmp') {
		return `/api/media/tmp/${filename}`;
	}
	if (raw.startsWith('/') || segments[0] === 'uploads') {
		return `/api/media/uploads/${filename}`;
	}
	if (segments.length >= 2) {
		const generationId = segments[segments.length - 2];
		return `/api/media/generations/${generationId}/${filename}`;
	}
	return `/api/media/uploads/${filename}`;
}

function humanizeKey(key: string): string {
	const spaced = key.replace(/_/g, ' ');
	return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

interface CollectState {
	entries: FormImageEntry[];
	seen: Set<string>;
}

function tryAdd(state: CollectState, key: string, label: string, media: MediaRef | string | null | undefined): void {
	if (!media) return;
	if (!isImageMedia(media)) return;
	const path = durablePath(media);
	if (!path || state.seen.has(path)) return;
	state.seen.add(path);
	state.entries.push({ key, label, media: toMediaRef(media), url: resolveMediaUrl(media) });
}

function collectFormDataImages(state: CollectState, formData: Record<string, unknown> | undefined | null): void {
	if (!formData) return;
	for (const [fieldKey, value] of Object.entries(formData)) {
		if (Array.isArray(value)) {
			// A `multi: true` MediaLoaderField's value - one entry per item,
			// each optionally carrying its own `label` (see MediaRef.label).
			value.forEach((item, i) => {
				if (isMediaLike(item)) {
					const itemLabel = item.label?.trim() || `${humanizeKey(fieldKey)} ${i + 1}`;
					tryAdd(state, `form:${fieldKey}:${i}`, itemLabel, item);
				} else if (typeof item === 'string' && item.length > 0 && hasImageExtension(item)) {
					tryAdd(state, `form:${fieldKey}:${i}`, `${humanizeKey(fieldKey)} ${i + 1}`, item);
				}
			});
		} else if (isMediaLike(value)) {
			tryAdd(state, `form:${fieldKey}`, humanizeKey(fieldKey), value);
		} else if (typeof value === 'string' && value.length > 0 && hasImageExtension(value)) {
			tryAdd(state, `form:${fieldKey}`, humanizeKey(fieldKey), value);
		}
	}
}

// A Director media value may be a `form_ref` pointer (Stage B reference
// media) rather than an embedded item -- resolve it live against the same
// form_data this module already scans before handing it to `tryAdd`, which
// only understands a plain `MediaRef`/path. An unresolvable (broken) or empty
// reference contributes nothing, same as a null value always did.
function directorMediaRef(
	value: DirectorMediaValue | null | undefined,
	formData: Record<string, unknown> | undefined | null
): MediaRef | null {
	const display = resolveDirectorMediaDisplay(value ?? null, formData);
	return display.kind === 'embedded' || display.kind === 'form_ref' ? display.media : null;
}

function collectDirectorImages(
	state: CollectState,
	director: VideoDirectorValue | undefined | null,
	formData: Record<string, unknown> | undefined | null
): void {
	if (!director) return;

	tryAdd(state, 'director:simple:start_image', 'Director · Start image', directorMediaRef(director.simple?.start_image, formData));
	tryAdd(state, 'director:simple:first_frame', 'Director · First frame', directorMediaRef(director.simple?.first_frame, formData));
	tryAdd(state, 'director:simple:last_frame', 'Director · Last frame', directorMediaRef(director.simple?.last_frame, formData));

	(director.timeline?.keyframes || []).forEach((kf, i) => {
		tryAdd(state, `director:timeline:keyframe:${kf.id ?? i}`, `Director · Keyframe ${i + 1}`, directorMediaRef(kf.media, formData));
	});

	const icLoraEntries = director.timeline?.ic_lora || [];
	icLoraEntries.forEach((entry, i) => {
		const label =
			icLoraEntries.length > 1 ? `Director · IC-LoRA reference ${i + 1}` : 'Director · IC-LoRA reference';
		tryAdd(state, `director:timeline:ic_lora:${entry.id ?? i}`, label, directorMediaRef(entry.ref_media, formData));
	});

	(director.chain?.segments || []).forEach((segment, i) => {
		tryAdd(state, `director:chain:${segment.id ?? i}`, `Director · Chain keyframe ${i + 1}`, directorMediaRef(segment.keyframe, formData));
	});

	(director.chain?.keyframes || []).forEach((kf, i) => {
		tryAdd(state, `director:chain:keyframe:${kf.id ?? i}`, `Director · Placed keyframe ${i + 1}`, directorMediaRef(kf.media, formData));
	});
}

export function collectFormImages(tab: Tab | null | undefined): FormImageEntry[] {
	const state: CollectState = { entries: [], seen: new Set() };
	if (!tab) return state.entries;

	collectFormDataImages(state, tab.formData);
	collectDirectorImages(state, tab.videoDirector, tab.formData);

	return state.entries;
}
