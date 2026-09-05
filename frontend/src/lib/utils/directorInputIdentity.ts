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

/** The active generation context a shot's request would actually be
 * submitted under. Required scope, not an optional nicety: a preset/variant/
 * mode switch changes what the SAME `formData` keys even MEAN (a different
 * preset's form can reuse a field name like `steps` for something else
 * entirely, or route to a wholly different pipeline) without necessarily
 * changing any individual formData value or the director document itself --
 * missing this, a preset switch back and forth could read back as
 * "unchanged". `null` for a field that genuinely isn't known yet (never
 * silently coerced to a placeholder string) still participates in the
 * identity -- unknown-vs-known is itself a real difference. */
export interface DirectorGenerationContext {
	presetId: string | null;
	variant: string | null;
	mode: string | null;
}

/** The identity's neutral generation context when a caller has none yet
 * (e.g. `deriveConsoleModel` invoked without one) -- every field `null`,
 * still a real, comparable value rather than an omitted one. */
export const EMPTY_GENERATION_CONTEXT: DirectorGenerationContext = { presetId: null, variant: null, mode: null };

export interface DirectorShotIdentityContext {
	caps: DirectorCapabilities;
	formData: Record<string, unknown> | null | undefined;
	/** Optional at the TYPE level only so a caller that predates this field
	 * (+page.svelte's submit-time stamping is mid-migration as of this
	 * writing -- see this module's header note) still compiles; functionally
	 * it is REQUIRED scope -- `directorShotInputIdentity` always folds SOME
	 * value in, defaulting to `EMPTY_GENERATION_CONTEXT` rather than skipping
	 * it, so every caller either supplies the real context or explicitly gets
	 * the neutral one (never silently omitted from the identity). */
	generationContext?: DirectorGenerationContext | null;
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
// `JSON.stringify`, `div1:` (missing continuation geometry/form settings/
// media revisions), or `div2:` (unsorted object keys -- `{steps:20,cfg:7}`
// and `{cfg:7,steps:20}` hashed differently; width/height/duration/fps
// wrongly accepted as revision evidence; no preset/variant/mode) -- is
// trivially distinguishable by a plain substring check, no JSON.parse or
// try/catch required.
//
// Two prefixes share the same payload version: `div3:` means every media
// entry folded into this identity carried enough revision evidence to trust
// an equality match; `div3u:` means at least one didn't (see
// `mediaSourceIdentity`) -- the string still changes when the content
// actually does, but a caller must not read an unchanged `div3u:` string as
// proof nothing changed (`isUnverifiedShotIdentity`), only as "no evidence
// either way".
const IDENTITY_PREFIX = 'div3:';
const IDENTITY_PREFIX_UNVERIFIED = 'div3u:';

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

/** Known revision-signal keys -- proof two items at the same path are (or
 * aren't) the same BYTES, checked on the item itself and (uploads carry
 * their own metadata alongside the path -- `buildUploadedMediaItem`,
 * mediaLoaderUpload.ts) a nested `metadata` object.
 *
 * Deliberately EXCLUDES `width`/`height`/`duration_seconds`/`fps`: those are
 * descriptive/dimensional metadata, not revision evidence -- two DIFFERENT
 * photos, or a photo re-exported after an edit, routinely share the exact
 * same dimensions, so a shape-only match must never be read as "same file".
 * Only a byte-identity signal counts: a size in bytes, a modification time,
 * a hash/etag, or an explicit revision/version id/tag. Not an exhaustive
 * contract, just every revision-ish field this codebase's media items are
 * ever seen carrying. */
const REVISION_KEYS = ['size', 'mtime', 'updated_at', 'modified_at', 'hash', 'content_hash', 'etag', 'revision', 'version'] as const;

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
	// Deliberately NOT `path`/`url` -- `canonicalizeValue` recurses into
	// whatever this returns (to sort ITS keys too), so a result shaped like
	// `{path, ...}` would get re-matched by `isMediaLike` and reprocessed as
	// if IT were a raw media item (losing the revision this call already
	// extracted). `mediaPath`/`mediaUrl` sidestep that.
	return { mediaPath: path, ...(url !== undefined ? { mediaUrl: url } : {}), revision, unverified: !hasRevisionEvidence };
}

function isMediaLike(v: unknown): v is Record<string, unknown> {
	return isRecord(v) && (typeof v.path === 'string' || typeof v.url === 'string');
}

/**
 * Deep-walks the WHOLE identity payload (own shot, film-level fields, shared
 * media, the rest of the generation form) into a canonical, JSON.stringify-
 * ready structure:
 *   - a `form_ref` pointer resolves to the CURRENT item it points at (by
 *     stable `path`, never an index) and that item's source identity, never
 *     the pointer literal or the file's bytes;
 *   - anything else that already structurally looks like media (`{path}`/
 *     `{url}`, embedded directly rather than via a `form_ref`) gets the same
 *     source-identity treatment;
 *   - every PLAIN OBJECT's keys are emitted in SORTED order at every nesting
 *     level -- `{steps:20,cfg:7}` and `{cfg:7,steps:20}` must be the same
 *     shot input, so key order can never be load-bearing;
 *   - every ARRAY keeps its own order untouched -- `chain.segments`,
 *     keyframe lists, prompt segments etc. are semantically ORDERED
 *     sequences (segment 1 then segment 2 is a different film from segment 2
 *     then segment 1), never a set;
 *   - every other string gets bounded so a raw inline payload can never blow
 *     up the identity's size.
 * `JSON.stringify` on the RESULT needs no replacer of its own -- every plain
 * object built here already has its keys inserted in the order they should
 * serialize in, which `JSON.stringify` preserves faithfully.
 */
function canonicalizeValue(value: unknown, formData: Record<string, unknown> | null | undefined, markUnverified: () => void): unknown {
	if (isFormMediaRef(value)) {
		const resolved = resolveFormMediaItem(value.form_ref.field, value.form_ref.path, formData);
		return resolved
			? { formRefResolved: canonicalizeValue(mediaSourceIdentity(resolved, markUnverified), formData, markUnverified) }
			: { formRefBroken: true };
	}
	if (isMediaLike(value)) {
		return canonicalizeValue(mediaSourceIdentity(value, markUnverified), formData, markUnverified);
	}
	if (Array.isArray(value)) {
		return value.map((item) => canonicalizeValue(item, formData, markUnverified));
	}
	if (isRecord(value)) {
		const out: Record<string, unknown> = {};
		for (const key of Object.keys(value).sort()) {
			out[key] = canonicalizeValue(value[key], formData, markUnverified);
		}
		return out;
	}
	return boundedValue(value);
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
 *     does (`request.form_data = {...tab.formData, video_director: ...}`);
 *   - `ctx.generationContext` -- the preset/variant/mode this shot's request
 *     would actually submit under (required, not optional: see
 *     `DirectorGenerationContext`'s own doc comment).
 * Object keys are emitted in SORTED order at every nesting level (array
 * order is left untouched -- see `canonicalizeValue`), so `{steps:20,cfg:7}`
 * and `{cfg:7,steps:20}` are the same identity.
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
	const { caps, formData, generationContext } = ctx;
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
		effectiveFormSettings: withoutKey(formData, 'video_director'),
		generationContext: generationContext ?? EMPTY_GENERATION_CONTEXT
	};
	let unverified = false;
	const canonical = canonicalizeValue(payload, formData, () => (unverified = true));
	const serialized = JSON.stringify(canonical);
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
