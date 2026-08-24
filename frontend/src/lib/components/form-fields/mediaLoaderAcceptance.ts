/**
 * Whether a candidate may enter the field, decided BEFORE it is uploaded.
 *
 * The backend validates the same limits on submit
 * (`media_input._check_media_constraints`), but a field that only learns "too
 * long" after a 400 MB upload finished has already wasted the upload and can
 * only report a generic failure.
 *
 * Because both sides check, both sides must agree — a client-side rule that is
 * even slightly stricter produces "the UI wouldn't let me but the API would
 * have taken it". Three behaviours are mirrored deliberately:
 *
 * - **Every violation is reported**, not just the first, so a file that is both
 *   too big and too long says so once.
 * - **Unknown metadata fails OPEN.** A check whose input could not be measured
 *   is skipped, never treated as zero.
 * - **A total is skipped entirely** when any item of that category has an
 *   unknown duration: a partial sum can pass a budget the full one would fail,
 *   so a partial sum must not be enforced at all.
 *
 * The wording follows the server's clauses (minus its `item N:` prefix, which
 * names a position the candidate does not occupy yet) so a user who trips a
 * limit here and a user who trips it on submit read the same sentence.
 */

import type { MediaKind, MediaLoaderLimits } from './mediaLoaderConfig';

export interface MediaCandidate {
	name: string;
	kind: MediaKind | null;
	/** The MIME type when the source had one — shown verbatim in a rejection. */
	mimeType?: string | null;
	sizeBytes?: number | null;
	width?: number | null;
	height?: number | null;
	durationSeconds?: number | null;
}

/** What is already in the field, so count and total-duration limits can see it. */
export interface MediaLoaderContents {
	count: number;
	/** Item count per kind — a mixed field holds one lane per kind. */
	countByKind: Partial<Record<MediaKind, number>>;
	/** Seconds already committed per kind, summing only the items that reported one. */
	durationByKind: Partial<Record<MediaKind, number>>;
	/**
	 * Kinds where at least one held item reported no duration. The category's
	 * total is unknowable, so its budget is not enforced at all.
	 */
	durationUnknownByKind: Partial<Record<MediaKind, boolean>>;
}

export type MediaAcceptance = { accepted: true } | { accepted: false; reasons: string[] };

export const EMPTY_CONTENTS: MediaLoaderContents = {
	count: 0,
	countByKind: {},
	durationByKind: {},
	durationUnknownByKind: {}
};

/** Matches the server's `%g`: 8.4s, 5s, 26s — never 5.0s. */
function seconds(value: number): string {
	return `${Math.round(value * 1000) / 1000}s`;
}

function megabytes(bytes: number): string {
	if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
	const mb = bytes / (1024 * 1024);
	return `${mb >= 10 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`;
}

function perItemDurationLimit(limits: MediaLoaderLimits, kind: MediaKind): number | null {
	if (kind === 'video') return limits.maxVideoDurationSeconds;
	if (kind === 'audio') return limits.maxAudioDurationSeconds;
	return null;
}

function totalDurationLimit(limits: MediaLoaderLimits, kind: MediaKind): number | null {
	if (kind === 'video') return limits.maxTotalVideoDurationSeconds;
	if (kind === 'audio') return limits.maxTotalAudioDurationSeconds;
	return null;
}

/** "for 'reference_media'" — omitted when the caller has no field name. */
function forField(fieldName: string | null | undefined): string {
	return fieldName ? ` for '${fieldName}'` : '';
}

function capitalize(text: string): string {
	return text.charAt(0).toUpperCase() + text.slice(1);
}

export interface AcceptanceOptions {
	/** The field's own name, so a message reads like the server's. */
	fieldName?: string | null;
}

/**
 * Checks one candidate against every declared limit.
 *
 * Dimensions and duration are optional: a plain `File` has neither until it is
 * probed, and a limit that cannot be measured is not enforced — the backend
 * still has the last word.
 */
export function evaluateCandidate(
	candidate: MediaCandidate,
	limits: MediaLoaderLimits,
	contents: MediaLoaderContents = EMPTY_CONTENTS,
	options: AcceptanceOptions = {}
): MediaAcceptance {
	const kind = candidate.kind;
	const field = options.fieldName;

	// Not one of the declared limits: a file whose kind cannot be read at all
	// has no media item to build. The server fails open on an unreadable
	// category because by then the item already exists on disk; here there is
	// nothing yet to accept.
	if (!kind) {
		return { accepted: false, reasons: [`Unsupported file — '${candidate.name}' is not an image, a video or audio`] };
	}

	const reasons: string[] = [];

	if (!limits.kinds.includes(kind)) {
		const accepted = [...limits.kinds].sort().join(', ');
		reasons.push(capitalize(`type '${kind}' is not accepted${forField(field)} (accepted: ${accepted})`));
	}

	if (limits.multiple && limits.maxItems != null && contents.count >= limits.maxItems) {
		reasons.push(capitalize(`too many items${forField(field)}: maximum is ${limits.maxItems}`));
	}

	if (limits.maxFileSizeBytes != null && (candidate.sizeBytes ?? 0) > limits.maxFileSizeBytes) {
		reasons.push(
			capitalize(
				`file size ${megabytes(candidate.sizeBytes as number)} exceeds the maximum of ${megabytes(limits.maxFileSizeBytes)}`
			)
		);
	}

	const { width, height } = candidate;

	// `max_resolution` caps each axis on its own — the server compares width
	// and height against it separately and reports each — so a 4096×512 image
	// fails a 2048 cap even though it fits inside a 2048×2048 box. Audio has no
	// resolution, and the server skips it there too.
	if (limits.maxResolution != null && kind !== 'audio') {
		if (width && width > limits.maxResolution) {
			reasons.push(capitalize(`width ${width}px exceeds the maximum resolution of ${limits.maxResolution}px`));
		}
		if (height && height > limits.maxResolution) {
			reasons.push(capitalize(`height ${height}px exceeds the maximum resolution of ${limits.maxResolution}px`));
		}
	}

	// The older per-axis caps from the `validation` block.
	if (limits.maxWidth != null && width && width > limits.maxWidth) {
		reasons.push(capitalize(`width ${width}px exceeds the maximum of ${limits.maxWidth}px`));
	}
	if (limits.maxHeight != null && height && height > limits.maxHeight) {
		reasons.push(capitalize(`height ${height}px exceeds the maximum of ${limits.maxHeight}px`));
	}

	const duration = candidate.durationSeconds;
	if (typeof duration === 'number' && duration > 0) {
		const perItem = perItemDurationLimit(limits, kind);
		if (perItem != null && duration > perItem) {
			reasons.push(
				capitalize(`${kind} duration ${seconds(duration)} exceeds the per-${kind} maximum of ${seconds(perItem)}`)
			);
		}

		const total = totalDurationLimit(limits, kind);
		// Skipped outright when any held item of this kind reported no
		// duration: the sum below would be partial, and a partial sum can pass
		// a budget the full one would fail.
		if (total != null && !contents.durationUnknownByKind[kind]) {
			const combined = (contents.durationByKind[kind] ?? 0) + duration;
			if (combined > total) {
				reasons.push(
					capitalize(
						`${kind} items total ${seconds(combined)} of duration${forField(field)}, exceeding the maximum of ${seconds(total)}`
					)
				);
			}
		}
	}

	return reasons.length > 0 ? { accepted: false, reasons } : { accepted: true };
}

/** The identity line under a rejection: "take_04.mov · video/quicktime". */
export function describeCandidate(candidate: MediaCandidate): string {
	return [candidate.name, candidate.mimeType].filter(Boolean).join(' · ');
}

/**
 * Tallies what the field already holds, for `evaluateCandidate`.
 *
 * A timed item that reported no duration marks its whole category unknown
 * rather than contributing zero — see `durationUnknownByKind`.
 */
export function summarizeContents(
	items: readonly unknown[],
	kindOf: (item: unknown) => MediaKind | null,
	durationOf: (item: unknown) => number | null
): MediaLoaderContents {
	const countByKind: Partial<Record<MediaKind, number>> = {};
	const durationByKind: Partial<Record<MediaKind, number>> = {};
	const durationUnknownByKind: Partial<Record<MediaKind, boolean>> = {};

	for (const item of items) {
		const kind = kindOf(item);
		if (!kind) continue;
		countByKind[kind] = (countByKind[kind] ?? 0) + 1;
		if (kind === 'image') continue;

		const duration = durationOf(item);
		if (typeof duration === 'number' && duration > 0) {
			durationByKind[kind] = (durationByKind[kind] ?? 0) + duration;
		} else {
			durationUnknownByKind[kind] = true;
		}
	}

	return { count: items.length, countByKind, durationByKind, durationUnknownByKind };
}
