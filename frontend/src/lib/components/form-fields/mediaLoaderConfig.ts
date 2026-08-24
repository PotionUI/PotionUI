/**
 * Reads a media field's declarative configuration into one normalized shape.
 *
 * Three things make this worth a module of its own:
 *
 * 1. The same setting arrives at two different depths. `accept`, `multiple`
 *    and `max_items` are emitted at the TOP level of the field schema by
 *    Image/Video/Audio `.output()`; `allow_inpaint` is nested under
 *    `configuration`; size/resolution/duration limits arrive under
 *    `validation` in camelCase. A reader that looks in one place silently
 *    loses the restriction and falls through to a permissive default.
 * 2. A field may accept SEVERAL kinds at once (images *and* videos), so the
 *    accepted set is a list, not a mode flag.
 * 3. The UI has to enforce these limits BEFORE upload, which means the same
 *    numbers the backend validates with have to be readable client-side.
 */

export type MediaKind = 'image' | 'video' | 'audio';

export const MEDIA_KINDS: readonly MediaKind[] = ['image', 'video', 'audio'];

export interface MediaLoaderLimits {
	/** Accepted kinds, in canonical image → video → audio order. Never empty. */
	kinds: MediaKind[];
	/** Value for `input[accept]`, as authored where possible. */
	accept: string;
	multiple: boolean;
	maxItems: number | null;
	/**
	 * `max_resolution` — ONE number, the cap on each axis of an image or a
	 * video, not a width×height pair (`media_input._check_media_constraints`
	 * compares it against width and height separately).
	 */
	maxResolution: number | null;
	/** The older per-axis caps from the `validation` block. */
	maxWidth: number | null;
	maxHeight: number | null;
	maxFileSizeBytes: number | null;
	/** Longest a single video item may be, in seconds. */
	maxVideoDurationSeconds: number | null;
	/** Longest a single audio item may be, in seconds. */
	maxAudioDurationSeconds: number | null;
	/** Combined length of all video items, in seconds. */
	maxTotalVideoDurationSeconds: number | null;
	/** Combined length of all audio items, in seconds. */
	maxTotalAudioDurationSeconds: number | null;
	allowInpaint: boolean;
}

const KIND_ALIASES: Record<string, MediaKind> = {
	image: 'image',
	images: 'image',
	picture: 'image',
	photo: 'image',
	video: 'video',
	videos: 'video',
	movie: 'video',
	audio: 'audio',
	sound: 'audio'
};

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

/**
 * Looks a key up at the top level of the field schema, then inside
 * `configuration`, then inside `validation` — the three depths a media field's
 * settings are emitted at. First hit wins; `undefined`/`null` never counts as
 * a hit, so an explicitly-absent key falls through to the next depth.
 */
function pick(config: Record<string, unknown>, ...keys: string[]): unknown {
	const scopes = [config, asRecord(config.configuration), asRecord(config.validation)];
	const nestedValidation = asRecord(asRecord(config.configuration).validation);
	scopes.push(nestedValidation);
	for (const key of keys) {
		for (const scope of scopes) {
			const found = scope[key];
			if (found !== undefined && found !== null) return found;
		}
	}
	return undefined;
}

function asPositiveNumber(value: unknown): number | null {
	const n = typeof value === 'string' ? Number(value) : value;
	if (typeof n !== 'number' || !Number.isFinite(n) || n <= 0) return null;
	return n;
}

function kindFromToken(token: string): MediaKind | null {
	const cleaned = token.trim().toLowerCase();
	if (!cleaned) return null;
	if (cleaned.startsWith('image/')) return 'image';
	if (cleaned.startsWith('video/')) return 'video';
	if (cleaned.startsWith('audio/')) return 'audio';
	return KIND_ALIASES[cleaned] ?? null;
}

function orderKinds(kinds: Set<MediaKind>): MediaKind[] {
	return MEDIA_KINDS.filter((k) => kinds.has(k));
}

/**
 * The accepted set, from either a list (`accepted_types: [image, video]`) or
 * the legacy `accept` MIME string. A field that declares neither takes the
 * permissive image+video default this component has always had.
 */
export function readAcceptedKinds(config: unknown): MediaKind[] {
	const raw = asRecord(config);
	const declared = pick(raw, 'accepted_types', 'accepted_kinds', 'media_types', 'accept', 'file_type');
	const found = new Set<MediaKind>();

	if (Array.isArray(declared)) {
		for (const entry of declared) {
			if (typeof entry !== 'string') continue;
			const kind = kindFromToken(entry);
			if (kind) found.add(kind);
		}
	} else if (typeof declared === 'string') {
		for (const token of declared.split(',')) {
			const kind = kindFromToken(token);
			if (kind) found.add(kind);
		}
	}

	if (found.size === 0) return ['image', 'video'];
	return orderKinds(found);
}

/**
 * `input[accept]` for a kind set.
 *
 * A `media` field's `accept` is the union of all three kinds' MIME types
 * whatever `accepted_types` says, so a field narrowed to images and video
 * would otherwise open a picker that still offers audio — pickable, then
 * refused. Whenever `accepted_types` narrows the set, the attribute is
 * synthesized from the kinds instead of echoing the broader authored string.
 */
export function acceptAttribute(config: unknown, kinds: MediaKind[]): string {
	const raw = asRecord(config);
	const narrowed = pick(raw, 'accepted_types', 'accepted_kinds', 'media_types') !== undefined;
	const declared = pick(raw, 'accept');
	if (!narrowed && typeof declared === 'string' && declared.trim()) return declared;
	return kinds.map((k) => `${k}/*`).join(',');
}

export function readMediaLoaderConfig(config: unknown): MediaLoaderLimits {
	const raw = asRecord(config);
	const kinds = readAcceptedKinds(raw);

	return {
		kinds,
		accept: acceptAttribute(raw, kinds),
		multiple: Boolean(pick(raw, 'multiple', 'multi')),
		maxItems: asPositiveNumber(pick(raw, 'max_items', 'maxItems')),
		maxResolution: asPositiveNumber(pick(raw, 'max_resolution')),
		maxWidth: asPositiveNumber(pick(raw, 'max_width', 'maxWidth')),
		maxHeight: asPositiveNumber(pick(raw, 'max_height', 'maxHeight')),
		maxFileSizeBytes: asPositiveNumber(pick(raw, 'max_size', 'maxSize')),
		// `maxDuration` is the older per-item key from the `validation` block,
		// which Video and Audio still emit; the `*_seconds` keys are the ones
		// a preset author writes today.
		maxVideoDurationSeconds: asPositiveNumber(pick(raw, 'max_video_duration_seconds', 'maxDuration')),
		maxAudioDurationSeconds: asPositiveNumber(pick(raw, 'max_audio_duration_seconds', 'maxDuration')),
		maxTotalVideoDurationSeconds: asPositiveNumber(pick(raw, 'max_total_video_duration_seconds')),
		maxTotalAudioDurationSeconds: asPositiveNumber(pick(raw, 'max_total_audio_duration_seconds')),
		allowInpaint: Boolean(pick(raw, 'allow_inpaint', 'allowInpaint'))
	};
}

const KIND_NOUNS: Record<MediaKind, { singular: string; plural: string }> = {
	image: { singular: 'an image', plural: 'images' },
	video: { singular: 'a video', plural: 'videos' },
	audio: { singular: 'an audio file', plural: 'audio' }
};

/** "images", "images and videos", "images, videos and audio". */
export function describeKinds(kinds: MediaKind[]): string {
	const nouns = kinds.map((k) => KIND_NOUNS[k].plural);
	if (nouns.length <= 1) return nouns[0] ?? '';
	return `${nouns.slice(0, -1).join(', ')} and ${nouns[nouns.length - 1]}`;
}

/** "Drop an image here" / "Drop an image or a video here". */
export function describeDropTarget(kinds: MediaKind[]): string {
	const nouns = kinds.map((k) => KIND_NOUNS[k].singular);
	if (nouns.length <= 1) return nouns[0] ?? 'a file';
	return `${nouns.slice(0, -1).join(', ')} or ${nouns[nouns.length - 1]}`;
}

const KIND_FORMATS: Record<MediaKind, string> = {
	image: 'PNG · JPG · WEBP',
	video: 'MP4 · WEBM · MOV',
	audio: 'WAV · MP3 · FLAC'
};

export function describeFormats(kinds: MediaKind[]): string {
	return kinds.map((k) => KIND_FORMATS[k]).join(' · ');
}
