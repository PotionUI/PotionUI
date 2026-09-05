// Canonical shot input identity for the Video Director console's staleness
// detection (consoleModel.ts's dependentBadge) and for what gets stamped
// onto `DirectorRunState.inputsHash` at submit time (routes/generate/
// +page.svelte's buildDirectorRunEntries). Supersedes the retired
// `directorShotFingerprint` (videoDirector.ts), which hashed only a chain
// segment/timeline shot's OWN object -- missing every FILM-level and
// FORM-level input the submission builders actually fold into every shot's
// wire request:
//   - global_prompt/negative_prompt/fps, chain-wide "anywhere" keyframes/
//     audio, a 'whole' reference pool (the original DIR-03 gap);
//   - chain continuation geometry (`chain.continuation.overlap_frames`/
//     `stitch`) and the REST of the generation form (steps/cfg/seed/sampler/
//     model/LoRA/resolution/...) -- `request.form_data` is literally
//     `{...tab.formData, video_director: ...}` (routes/generate/+page.svelte),
//     so every OTHER key in `formData` reaches the request just as much as
//     the director document does, under whatever field names this preset's
//     form happens to use (there is no fixed "steps"/"cfg" key across
//     engines) -- folding in the whole object (minus `video_director` itself,
//     replaced separately) is the only representation that doesn't have to
//     know a preset's field names;
//   - a `form_ref` pointer resolved to what it currently points at, not the
//     pointer literal -- a replaced file under an unchanged pointer changed
//     what got rendered without ever changing the old fingerprint or a
//     path-only resolution.
//
// `directorShotInputIdentity` is the ONE function both call sites use: a
// run's `inputsHash` is this value captured AT SUBMIT TIME, and a later
// freshness check recomputes it against the LIVE document/form and compares
// strings. Neither caller ever inspects the string's shape beyond the
// version/unverified prefix -- only ever equality -- so the exact
// serialization is an implementation detail as long as it's deterministic,
// bounded regardless of payload size, and every input that changes what the
// shot renders changes the string.
import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
import { collectFormMediaOptions, deriveChainSegmentSubType, isFormMediaRef, resolveFormMediaItem } from './videoDirector';

export interface DirectorShotIdentityContext {
	caps: DirectorCapabilities;
	formData: Record<string, unknown> | null | undefined;
}

/** Chain routing has no discrete "consumed predecessor output" at all: every
 * resubmission sends the WHOLE chain document (`buildChainDirectorSubmission`)
 * in one wire doc, and the backend re-derives a `continue` segment's
 * continuity NATIVELY, from the live document, inside that one generation --
 * never by reading back an earlier generation's rendered file the way LTX's
 * timeline `continue_from_previous` does (see `directorContinuation.ts`'s
 * `resolvePredecessorFrame`, which only ever exists for timeline shots).
 * Stamped as `outputKey` on a chain shot's `predecessorRef` instead of a shot
 * id, so nothing downstream mistakes it for an addressable output -- the
 * `generationId` half still carries real signal (a resubmission mints a new
 * one), just not this half. */
export const NATIVE_CONTINUATION_OUTPUT_KEY = '__native_chain_continuation__';

/** The generation/output a dependent shot's run actually consumed as its
 * predecessor input, captured at submit time and retained on
 * `DirectorRunState.predecessorRef` (types/tabs.ts). `outputKey` is the
 * PREDECESSOR'S SHOT id for a timeline continuation (one generation per shot,
 * matching `directorContinuation.ts`'s `resolvePredecessorFrame` output --
 * one chain generation can cover several shots at once (`DirectorRunLinks`),
 * so the shot id is what would disambiguate which of that shared
 * generation's per-segment outputs a run consumed, if chain routing ever
 * consumed a discrete output at all), or `NATIVE_CONTINUATION_OUTPUT_KEY` for
 * a chain shot (see that constant's doc comment). Comparing `generationId`
 * against the predecessor's CURRENT run at check time catches "the
 * predecessor was regenerated since" even when both rows read 'done' and
 * neither's own document changed -- something a `finishedAt` ordering
 * heuristic can get wrong and a bare content diff can't see at all (a
 * regenerate with identical inputs still produces a different output). */
export interface DirectorPredecessorRef {
	generationId: string;
	outputKey: string;
}

// Bumped whenever the identity's shape changes in a way that could make an
// old string compare equal (or unequal) for the wrong reason. Prefixed onto
// the serialized string (not embedded as a JSON field) so a stored value from
// an earlier format -- the retired `directorShotFingerprint`'s bare
// `JSON.stringify`, or `div1:`'s narrower payload (missing continuation
// geometry/form settings/media revisions) -- is trivially distinguishable by
// a plain substring check, no JSON.parse or try/catch required.
//
// Two prefixes share the same payload version: `div2:` means every media
// entry folded into this identity carried enough revision evidence to trust
// an equality match; `div2u:` means at least one didn't (see
// `mediaSourceIdentity`) -- the string still changes when the content
// actually does, but a caller must not read an unchanged `div2u:` string as
// proof nothing changed (`isUnverifiedShotIdentity`), only as "no evidence
// either way".
const IDENTITY_PREFIX = 'div2:';
const IDENTITY_PREFIX_UNVERIFIED = 'div2u:';

/** True when `inputsHash` was produced by `directorShotInputIdentity` (either
 * prefix) -- false for a value the retired `directorShotFingerprint` or the
 * narrower `div1:` format produced, or no value at all. A run whose
 * `inputsHash` fails this (or whose `predecessorRef` is missing where one is
 * expected) can't be trusted to answer "did this change since" -- see
 * consoleModel.ts's dependentBadge, which reports `unverified` rather than
 * guessing. */
export function hasVersionedShotIdentity(inputsHash: string | null | undefined): inputsHash is string {
	return typeof inputsHash === 'string' && (inputsHash.startsWith(IDENTITY_PREFIX) || inputsHash.startsWith(IDENTITY_PREFIX_UNVERIFIED));
}

/** True when `inputsHash` carries the `div2u:` prefix -- at least one media
 * entry folded into it had no revision evidence (no size/dimensions/
 * timestamp/hash beyond a bare path) to tell "same file" apart from "replaced
 * file at the same path". A caller comparing two identities where either
 * side is unverified must not conclude 'continuous' from a string match --
 * see consoleModel.ts's dependentBadge. */
export function isUnverifiedShotIdentity(inputsHash: string | null | undefined): boolean {
	return typeof inputsHash === 'string' && inputsHash.startsWith(IDENTITY_PREFIX_UNVERIFIED);
}

function isRecord(v: unknown): v is Record<string, unknown> {
	return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function ownShotValue(value: VideoDirectorValue, shotId: string): unknown {
	const chainSegment = value.chain.segments.find((s) => s.id === shotId);
	if (chainSegment) return chainSegment;
	const timelineShot = value.timeline.shots.find((s) => s.id === shotId);
	return timelineShot ?? null;
}

// ─── Bounding: no raw payload (embedded media bytes, a huge form field)
// ever reaches the identity string verbatim ─────────────────────────────────

// A real upload path/url/label never gets remotely close to this; only a
// raw inline payload (a `data:` URI, base64 bytes some caller embedded
// directly instead of uploading) does.
const INLINE_VALUE_LIMIT = 200;

// Hard ceiling on the whole serialized identity, regardless of how many
// segments/keyframes/form fields the document carries -- `boundedValue`
// alone bounds any ONE string, not the total shape.
const MAX_IDENTITY_LENGTH = 2048;

/** Deterministic, synchronous 32-bit string digest (FNV-1a) -- NOT a
 * security hash, and not meant to be one: every caller only ever compares
 * two identity strings for byte equality, so collision resistance beyond
 * "good enough to change when the input does" buys nothing. Synchronous is
 * the actual requirement -- `directorShotInputIdentity` is called from a
 * pure, synchronous derivation (`deriveConsoleModel`, Svelte reactivity), so
 * `crypto.subtle.digest` (async-only in the browser) is not an option here. */
function hashString(s: string): string {
	let h = 0x811c9dc5;
	for (let i = 0; i < s.length; i++) {
		h ^= s.charCodeAt(i);
		h = Math.imul(h, 0x01000193);
	}
	return (h >>> 0).toString(16);
}

// A `data:` URI is inline bytes at ANY length -- a test fixture's toy 20-char
// one is exactly as much "the payload" as a real multi-MB one, so this
// triggers regardless of `INLINE_VALUE_LIMIT`.
const DATA_URI_RE = /^data:/i;

function looksLikeInlinePayload(v: string): boolean {
	return v.length > INLINE_VALUE_LIMIT || DATA_URI_RE.test(v);
}

function boundedValue(v: unknown): unknown {
	if (typeof v === 'string' && looksLikeInlinePayload(v)) {
		return `~digest:${hashString(v)}:${v.length}`;
	}
	return v;
}

/** Known revision-signal keys, checked on the item itself and (uploads carry
 * their own metadata alongside the path -- `buildUploadedMediaItem`,
 * mediaLoaderUpload.ts) a nested `metadata` object. Not an exhaustive
 * contract, just every revision-ish field this codebase's media items are
 * ever seen carrying. */
const REVISION_KEYS = ['size', 'width', 'height', 'duration_seconds', 'fps', 'mtime', 'updated_at', 'hash', 'content_hash', 'etag'] as const;

// `item` is typed `object`, not `Record<string, unknown>`, so a caller can
// pass a resolved `MediaRef` (types/tabs.ts) or any other named interface
// straight through -- a nominal interface has no index signature, so TS
// refuses to assign it to an index-signature parameter type directly
// ("Index signature for type 'string' is missing"). `asRecord` is the one
// narrow, explicit cast that re-enables dynamic property reads on an object
// already known (by the caller's own guard, `isMediaLike`/`isFormMediaRef`)
// to be a plain data object -- not an `any` escape hatch, just recovering
// indexability TS's structural rule doesn't grant nominal types.
function asRecord(item: object): Record<string, unknown> {
	return item as Record<string, unknown>;
}

function extractRevisionSignals(item: object): Record<string, unknown> {
	const rec = asRecord(item);
	const meta = isRecord(rec.metadata) ? rec.metadata : rec;
	const found: Record<string, unknown> = {};
	for (const key of REVISION_KEYS) {
		const v = meta[key];
		if (v !== undefined && v !== null) found[key] = v;
	}
	// A raw inline payload sitting in some OTHER field (a not-yet-uploaded
	// clipboard paste, a preview blob a caller stashed alongside a stable
	// `path`) is real byte evidence too -- digested via `boundedValue`, never
	// the bytes themselves, but it's still a value that changes when the
	// content does even though `path`/`url` didn't.
	for (const [key, v] of Object.entries(rec)) {
		if (key === 'path' || key === 'url' || key === 'metadata') continue;
		if (typeof v === 'string' && looksLikeInlinePayload(v)) found[key] = boundedValue(v);
	}
	return found;
}

/** A media item's identity is its stable path/url plus whatever revision
 * evidence it carries (see `REVISION_KEYS`) -- never the item wholesale
 * (which could be carrying an inline `data:` payload as `path`/`url` before
 * upload finishes) and never its bytes. Two items at the SAME path with
 * DIFFERENT revision evidence are a real change (a replaced file); two items
 * at the same path with NO revision evidence on either side can't be told
 * apart, so `unverified: true` marks that rather than asserting a match --
 * `markUnverified` bubbles that up to the whole identity's prefix (see
 * `directorShotInputIdentity`). */
function mediaSourceIdentity(item: object, markUnverified: () => void): unknown {
	const rec = asRecord(item);
	const path = typeof rec.path === 'string' ? boundedValue(rec.path) : null;
	const url = typeof rec.url === 'string' && rec.url !== rec.path ? boundedValue(rec.url) : undefined;
	const revision = extractRevisionSignals(item);
	const hasRevisionEvidence = Object.keys(revision).length > 0;
	if (!hasRevisionEvidence) markUnverified();
	// Deliberately NOT `path`/`url` -- JSON.stringify's replacer keeps
	// descending into whatever a replaced value returns, so a result shaped
	// like `{path, ...}` would get re-matched by `isMediaLike` and
	// reprocessed as if IT were a raw media item (losing the revision this
	// call already extracted). `mediaPath`/`mediaUrl` sidestep that.
	return { mediaPath: path, ...(url !== undefined ? { mediaUrl: url } : {}), revision, unverified: !hasRevisionEvidence };
}

function isMediaLike(v: unknown): v is Record<string, unknown> {
	return isRecord(v) && (typeof v.path === 'string' || typeof v.url === 'string');
}

/** JSON.stringify replacer applied to the WHOLE identity payload (own shot,
 * film-level fields, shared media, the rest of the generation form): a
 * `form_ref` pointer resolves to the CURRENT item it points at (by stable
 * `path`, never an index) and that item's source identity, not the pointer
 * literal or the file's bytes; anything else that already structurally looks
 * like media (`{path}`/`{url}`, embedded directly rather than via a
 * `form_ref`) gets the same source-identity treatment; every other string
 * gets bounded so a raw inline payload can never blow up the identity's
 * size. */
function identityReplacer(formData: Record<string, unknown> | null | undefined, markUnverified: () => void) {
	return (_key: string, value: unknown): unknown => {
		if (isFormMediaRef(value)) {
			const resolved = resolveFormMediaItem(value.form_ref.field, value.form_ref.path, formData);
			return resolved ? { formRefResolved: mediaSourceIdentity(resolved, markUnverified) } : { formRefBroken: true };
		}
		if (isMediaLike(value)) {
			return mediaSourceIdentity(value, markUnverified);
		}
		return boundedValue(value);
	};
}

/** Every media/reference input shared across EVERY shot in a CHAIN document
 * -- folded into the wire doc for every segment regardless of which shot is
 * actually being (re)submitted (`buildChainDirectorSubmission` always sends
 * the whole chain). A timeline shot's own keyframes/audio/ic_lora are already
 * inside `ownShotValue` (`DirectorTimelineShot` owns them outright), so
 * there's nothing chain-shaped to add there. */
function sharedFilmMedia(value: VideoDirectorValue, caps: DirectorCapabilities): unknown[] {
	const shared: unknown[] = [];
	if (caps.segmentRouting) {
		const dc = caps.modes.director;
		if (dc?.keyframes === 'anywhere') {
			for (const kf of value.chain.keyframes) {
				if (kf.media) shared.push({ kind: 'keyframe', at: kf.at, strength: kf.strength, media: kf.media });
			}
		}
		if (dc?.audio) {
			for (const a of value.chain.audio) {
				if (a.media) {
					shared.push({
						kind: 'audio',
						id: a.id,
						role: a.role ?? null,
						start: a.start,
						trim_start: a.trim_start,
						length: a.length,
						media: a.media
					});
				}
			}
		}
	}
	return shared;
}

function withoutKey(obj: Record<string, unknown> | null | undefined, key: string): Record<string, unknown> | null {
	if (!obj) return null;
	if (!(key in obj)) return obj;
	const { [key]: _omit, ...rest } = obj;
	return rest;
}

/**
 * Canonical, order-stable, BOUNDED identity of shot `shotId`'s complete
 * generation-defining input:
 *   - its own chain segment/timeline shot object, every media reference
 *     inside it (embedded or `form_ref`) reduced to a source/revision
 *     identity;
 *   - the film-level fields the submission builders fold into that same
 *     shot's wire document (global/negative prompt, fps, chain continuation
 *     geometry, chain-wide shared media);
 *   - the REST of the generation form (`ctx.formData`, minus the
 *     `video_director` key itself) -- steps/cfg/seed/sampler/model/LoRA/
 *     resolution/... under whatever field names this preset's form uses, all
 *     of which reach the request exactly as much as the director document
 *     does (`request.form_data = {...tab.formData, video_director: ...}`).
 *
 * `null` when the document has no shot with that id (a stale reference --
 * the shot was removed since). Two calls with an unchanged (value, shotId,
 * ctx) tuple are byte-identical; that's the only property either caller
 * relies on. Not a real hash of the WHOLE payload (no algorithm, just a
 * canonical, versioned, length-capped string) -- every caller only ever
 * compares two identities for equality, so a cheap deterministic string
 * serves exactly as well as a real hash. See `isUnverifiedShotIdentity` for
 * why a caller must not read an unchanged string alone as proof of
 * freshness.
 */
export function directorShotInputIdentity(
	value: VideoDirectorValue,
	shotId: string,
	ctx: DirectorShotIdentityContext
): string | null {
	const own = ownShotValue(value, shotId);
	if (own == null) return null;
	const { caps, formData } = ctx;
	const fps = caps.segmentRouting ? value.chain.fps : value.timeline.fps;
	const payload = {
		own,
		film: {
			globalPrompt: value.global_prompt,
			negativePrompt: value.negative_prompt,
			fps,
			// Chain-only: LTX's timeline continuation carries no equivalent
			// document-level geometry (a timeline shot's own `continue_from_previous`
			// is already inside `own`).
			continuation: caps.segmentRouting ? value.chain.continuation : null
		},
		sharedMedia: sharedFilmMedia(value, caps),
		effectiveFormSettings: withoutKey(formData, 'video_director')
	};
	let unverified = false;
	const serialized = JSON.stringify(payload, identityReplacer(formData, () => (unverified = true)));
	const bounded = serialized.length > MAX_IDENTITY_LENGTH ? `~digest:${hashString(serialized)}:${serialized.length}` : serialized;
	return (unverified ? IDENTITY_PREFIX_UNVERIFIED : IDENTITY_PREFIX) + bounded;
}

/**
 * The shot id `shotId` depends on via a live continuation join, or null when
 * it opens fresh (independent, or continuation itself is disabled/
 * unavailable). Mirrors railModel.ts's own chain-seam derivation
 * (`deriveChainSegmentSubType(...) === 'chain'`) and the timeline's
 * `continue_from_previous` flag exactly, without needing a `RailModel` --
 * the submit-time caller (+page.svelte) has no rail, only the document and
 * its capabilities.
 */
export function directorPredecessorShotId(value: VideoDirectorValue, caps: DirectorCapabilities, shotId: string): string | null {
	if (caps.segmentRouting) {
		const index = value.chain.segments.findIndex((s) => s.id === shotId);
		if (index <= 0) return null;
		if (caps.modes.director?.continuationDisabled === true) return null;
		const segment = value.chain.segments[index];
		return deriveChainSegmentSubType(segment, index) === 'chain' ? value.chain.segments[index - 1].id : null;
	}
	const index = value.timeline.shots.findIndex((s) => s.id === shotId);
	if (index <= 0) return null;
	return value.timeline.shots[index].continue_from_previous ? value.timeline.shots[index - 1].id : null;
}

/** The `outputKey` a predecessor reference for `shotId` should carry --
 * `NATIVE_CONTINUATION_OUTPUT_KEY` for chain routing (no discrete consumed
 * output, see that constant), the predecessor's own shot id for a timeline
 * continuation (matches `directorContinuation.ts`'s `resolvePredecessorFrame`
 * output exactly -- one generation per timeline shot, so the shot id alone
 * already disambiguates). Shared by the submit-time writer (+page.svelte) and
 * the freshness check (consoleModel.ts) so both agree on what "unchanged"
 * means. */
export function directorPredecessorOutputKey(caps: DirectorCapabilities, predecessorId: string): string {
	return caps.segmentRouting ? NATIVE_CONTINUATION_OUTPUT_KEY : predecessorId;
}
