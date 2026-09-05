// Canonical shot input identity for the Video Director console's staleness
// detection (consoleModel.ts's dependentBadge) and for what gets stamped
// onto `DirectorRunState.inputsHash` at submit time (routes/generate/
// +page.svelte's buildDirectorRunEntries). Supersedes the retired
// `directorShotFingerprint` (videoDirector.ts), which hashed only a chain
// segment/timeline shot's OWN object -- missing every FILM-level input the
// submission builders (buildChainDirectorSubmission/buildTimelineShotWireDoc)
// actually fold into every shot's wire document (global_prompt,
// negative_prompt, fps, chain-wide "anywhere" keyframes/audio, a 'whole'
// reference pool), and never resolved a `form_ref` pointer to what it
// currently points at -- a film-level prompt edit, or a replaced file under
// an unchanged pointer, changed what got rendered without ever changing the
// old fingerprint.
//
// `directorShotInputIdentity` is the ONE function both call sites use: a
// run's `inputsHash` is this value captured AT SUBMIT TIME, and a later
// freshness check recomputes it against the LIVE document and compares
// strings. Neither caller ever inspects the string's shape -- only ever
// equality -- so the exact serialization is an implementation detail as long
// as it's deterministic and every input that changes what the shot renders
// changes the string.
import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';
import { collectFormMediaOptions, deriveChainSegmentSubType, isFormMediaRef, resolveFormMediaItem } from './videoDirector';

export interface DirectorShotIdentityContext {
	caps: DirectorCapabilities;
	formData: Record<string, unknown> | null | undefined;
}

/** The generation/output a dependent shot's run actually consumed as its
 * predecessor input, captured at submit time and retained on
 * `DirectorRunState.predecessorRef` (types/tabs.ts). `outputKey` is the
 * PREDECESSOR'S SHOT id, not a URL -- one chain generation can cover several
 * shots at once (`DirectorRunLinks`), so the shot id is what disambiguates
 * which of that shared generation's per-segment outputs this run consumed.
 * Comparing this against the predecessor's CURRENT run at check time catches
 * "the predecessor was regenerated since" even when both rows read 'done'
 * and neither's own document changed -- something a `finishedAt` ordering
 * heuristic can get wrong (clock skew, a resubmission that finishes out of
 * order) and a bare content diff can't see at all (a regenerate with
 * identical inputs still produces a different output). */
export interface DirectorPredecessorRef {
	generationId: string;
	outputKey: string;
}

// Bumped whenever the identity's shape changes in a way that could make an
// old string compare equal (or unequal) for the wrong reason. Prefixed onto
// the serialized string (not embedded as a JSON field) so a stored value from
// the retired `directorShotFingerprint` -- a bare `JSON.stringify` of the
// segment/shot, no prefix at all -- is trivially distinguishable by a plain
// substring check, no JSON.parse or try/catch required.
const IDENTITY_PREFIX = 'div1:';

/** True when `inputsHash` was produced by `directorShotInputIdentity` --
 * false for a value the retired `directorShotFingerprint` produced, or no
 * value at all. A run whose `inputsHash` fails this (or whose
 * `predecessorRef` is missing where one is expected) can't be trusted to
 * answer "did this change since" -- see consoleModel.ts's dependentBadge,
 * which reports `unverified` rather than guessing. */
export function hasVersionedShotIdentity(inputsHash: string | null | undefined): inputsHash is string {
	return typeof inputsHash === 'string' && inputsHash.startsWith(IDENTITY_PREFIX);
}

function ownShotValue(value: VideoDirectorValue, shotId: string): unknown {
	const chainSegment = value.chain.segments.find((s) => s.id === shotId);
	if (chainSegment) return chainSegment;
	const timelineShot = value.timeline.shots.find((s) => s.id === shotId);
	return timelineShot ?? null;
}

/** Every media/reference input shared across EVERY shot in the document --
 * folded into a chain generation's wire doc for every segment regardless of
 * which shot is actually being (re)submitted (`buildChainDirectorSubmission`
 * always sends the whole chain), or consumed independent of segment routing
 * (a 'whole' reference pool applies to a timeline film just as much as a
 * chain one). A timeline shot's own keyframes/audio/ic_lora are already
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

/** JSON.stringify replacer: a `form_ref` pointer is replaced by the CURRENT
 * item it resolves to (by stable `path`, never an index -- same resolution
 * `resolveFormMediaItem` gives the submission builders), not the pointer
 * literal. A pointer whose {field, path} never changes but now resolves to
 * different bytes (replaced file) or nothing at all (removed/reordered out)
 * changes the identity either way; never embeds the file's own bytes. */
function formRefReplacer(formData: Record<string, unknown> | null | undefined) {
	return (_key: string, value: unknown): unknown => {
		if (isFormMediaRef(value)) {
			const resolved = resolveFormMediaItem(value.form_ref.field, value.form_ref.path, formData);
			return { formRefResolvedPath: resolved?.path ?? null };
		}
		return value;
	};
}

/**
 * Canonical, order-stable identity of shot `shotId`'s complete generation-
 * defining input: its own chain segment/timeline shot object, every
 * form_ref inside it resolved to what it currently points at, plus the
 * film-level fields the submission builders fold into that same shot's wire
 * document (global/negative prompt, fps, chain-wide shared media, a 'whole'
 * reference pool). `null` when the document has no shot with that id (a
 * stale reference -- the shot was removed since).
 *
 * Two calls with an unchanged (value, shotId, ctx) tuple are byte-identical;
 * that's the only property either caller relies on. Not a real hash (no
 * algorithm, just a canonical, versioned string) -- every caller only ever
 * compares two identities for equality, so a cheap deterministic string
 * serves exactly as well as a real hash.
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
	const referencePool =
		caps.references === 'whole'
			? collectFormMediaOptions(formData)
					.filter((o) => caps.referenceFields.length === 0 || caps.referenceFields.includes(o.field))
					.map((o) => ({ field: o.field, path: o.item.path }))
			: null;
	const payload = {
		own,
		film: { globalPrompt: value.global_prompt, negativePrompt: value.negative_prompt, fps },
		sharedMedia: sharedFilmMedia(value, caps),
		referencePool
	};
	return IDENTITY_PREFIX + JSON.stringify(payload, formRefReplacer(formData));
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
