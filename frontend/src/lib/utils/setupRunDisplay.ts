import type {
	SetupConsentRequest,
	SetupRun,
	SetupRunStatus,
	SetupRunStepView,
	SetupStepAttempt,
	SetupStepStatus
} from '$lib/services/api/setup';
import { formatBytes, formatCount, formatDuration } from './format';

/** Badge variants the shared `Badge` component accepts. */
export type BadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

const TERMINAL_RUN_STATUSES: ReadonlySet<SetupRunStatus> = new Set([
	'completed',
	'failed',
	'cancelled'
]);

/** A run in one of these statuses is finished and will never change again. */
export function isRunTerminal(status: SetupRunStatus): boolean {
	return TERMINAL_RUN_STATUSES.has(status);
}

/** Poll only while the run can still change — stop the instant it's terminal. */
export function shouldPollRun(status: SetupRunStatus): boolean {
	return !isRunTerminal(status);
}

/** How often to re-check an in-progress run. */
export const RUN_POLL_INTERVAL_MS = 2500;

/** Only a failed run may be retried (mirrors `SetupRunManager`'s `retry_step`
 * gate — the state machine only allows it from FAILED). */
export function canRetryRun(status: SetupRunStatus): boolean {
	return status === 'failed';
}

/** A status (run- or step-level) that means "waiting on the person, not the
 * machine" — the consent gate. Written against the literal value so it
 * matches whichever DTO (run or step) is passed in without a name mismatch. */
export function isConsentStatus(status: SetupRunStatus | SetupStepStatus): boolean {
	return status === 'awaiting_consent';
}

export function runBadgeVariant(status: SetupRunStatus): BadgeVariant {
	switch (status) {
		case 'completed':
			return 'success';
		case 'failed':
			return 'danger';
		case 'cancelled':
			return 'neutral';
		case 'awaiting_consent':
			return 'signal';
		case 'paused':
			return 'warning';
		case 'pending':
		case 'running':
			return 'info';
	}
}

export function runStatusLabel(status: SetupRunStatus): string {
	switch (status) {
		case 'pending':
			return 'Getting ready';
		case 'running':
			return 'Working on it';
		case 'awaiting_consent':
			return 'Needs your go-ahead';
		case 'paused':
			return 'Paused';
		case 'completed':
			return 'All done';
		case 'failed':
			return "Couldn't finish";
		case 'cancelled':
			return 'Cancelled';
	}
}

export function stepBadgeVariant(status: SetupStepStatus): BadgeVariant {
	switch (status) {
		case 'succeeded':
			return 'success';
		case 'failed':
			return 'danger';
		case 'cancelled':
			return 'neutral';
		case 'awaiting_consent':
			return 'signal';
		case 'action_required':
			return 'warning';
		case 'running':
			return 'info';
	}
}

export function stepStatusLabel(status: SetupStepStatus): string {
	switch (status) {
		case 'running':
			return 'In progress';
		case 'succeeded':
			return 'Done';
		case 'action_required':
			return 'Needs attention';
		case 'awaiting_consent':
			return 'Needs your go-ahead';
		case 'failed':
			return "Couldn't finish";
		case 'cancelled':
			return 'Cancelled';
	}
}

/** Turn a `step_key` like `download_model` or `test-backend` into plain
 * words ("Download model", "Test backend"). No name is ever hardcoded here —
 * every title is derived from whatever key the run actually reports, so a
 * step this UI has never seen still gets a readable title. */
export function humanizeStepKey(stepKey: string): string {
	const trimmed = (stepKey || '').trim();
	if (!trimmed) return 'Step';
	return trimmed
		.replace(/[_-]+/g, ' ')
		.split(/\s+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/** A step's attempts collapsed to "one row per step", newest attempt first
 * for history but exposing the latest as the row people see. */
export interface SetupStepGroup {
	stepKey: string;
	title: string;
	/** The most recent attempt — what the step row displays. */
	latest: SetupStepAttempt;
	/** All attempts for this step, oldest to newest (retries append). */
	attempts: SetupStepAttempt[];
}

/**
 * Group step attempts by `step_key` and order the groups as a timeline.
 *
 * The backend returns attempts ordered `step_key ASC, attempt ASC` (see
 * `SetupRunRepository.list_attempts`) — alphabetical, not execution order.
 * Since there is no declared step manifest to fall back on, this sorts by
 * each step's earliest `started_at` as the closest available proxy for "the
 * order things actually happened in"; a step with no timestamp yet (should
 * not occur given the current backend, but handled defensively) sorts after
 * ones that have started, alphabetically among themselves.
 */
export function groupStepAttempts(attempts: SetupStepAttempt[]): SetupStepGroup[] {
	const byKey = new Map<string, SetupStepAttempt[]>();
	for (const attempt of attempts) {
		const list = byKey.get(attempt.step_key);
		if (list) list.push(attempt);
		else byKey.set(attempt.step_key, [attempt]);
	}

	const groups: SetupStepGroup[] = [];
	for (const [stepKey, list] of byKey) {
		const sorted = [...list].sort((a, b) => a.attempt - b.attempt);
		groups.push({
			stepKey,
			title: humanizeStepKey(stepKey),
			latest: sorted[sorted.length - 1],
			attempts: sorted
		});
	}

	groups.sort((a, b) => {
		const aTime = a.attempts[0]?.started_at ? Date.parse(a.attempts[0].started_at) : null;
		const bTime = b.attempts[0]?.started_at ? Date.parse(b.attempts[0].started_at) : null;
		if (aTime !== null && bTime !== null) return aTime - bTime;
		if (aTime !== null) return -1;
		if (bTime !== null) return 1;
		return a.stepKey.localeCompare(b.stepKey);
	});

	return groups;
}

/** Mono/tabular-nums-ready duration string for a step attempt, or `null` when
 * it hasn't started yet. Runs the clock against `now()` while still running. */
export function stepDuration(
	attempt: Pick<SetupStepAttempt, 'started_at' | 'finished_at'>,
	now: () => number = Date.now
): string | null {
	if (!attempt.started_at) return null;
	const start = Date.parse(attempt.started_at);
	if (Number.isNaN(start)) return null;
	const end = attempt.finished_at ? Date.parse(attempt.finished_at) : now();
	if (Number.isNaN(end) || end < start) return null;
	return formatDuration(end - start);
}

/**
 * A plain-language progress line built only from what's actually known.
 *
 * There is no step manifest, so the total step count is never claimed —
 * only counts of what has been observed so far ("2 done, 1 in progress"),
 * to avoid implying a denominator ("2 of 5") the frontend cannot verify.
 */
export function runProgressSummary(groups: SetupStepGroup[]): string {
	if (groups.length === 0) return 'No steps have started yet.';

	const succeeded = groups.filter((g) => g.latest.status === 'succeeded').length;
	const running = groups.filter((g) => g.latest.status === 'running').length;
	const waiting = groups.filter((g) => isConsentStatus(g.latest.status)).length;
	const needsAttention = groups.filter((g) => g.latest.status === 'action_required').length;
	const failed = groups.filter((g) => g.latest.status === 'failed').length;

	const parts: string[] = [];
	if (succeeded) parts.push(`${succeeded} done`);
	if (running) parts.push(`${running} in progress`);
	if (waiting) parts.push(`${waiting} waiting on you`);
	if (needsAttention) parts.push(`${needsAttention} need attention`);
	if (failed) parts.push(`${failed} couldn't finish`);

	if (parts.length === 0) {
		return `${groups.length} step${groups.length === 1 ? '' : 's'} queued`;
	}
	return parts.join(', ');
}

/** Whether a run (not just an individual step) is itself waiting on consent. */
export function runNeedsConsent(run: Pick<SetupRun, 'status'>): boolean {
	return isConsentStatus(run.status);
}

/**
 * What the /setup panel should show once it knows (a) whether
 * `GET /runs/active` found a run and (b) what (if anything) was cached
 * locally from a previous visit.
 *
 * `/runs/active` is always the authoritative answer to "is something in
 * progress right now" — a failed run is deliberately excluded from it (see
 * `ACTIVE_STATUSES`), which would otherwise make a failed run vanish (and
 * its "Try again" with it) the instant the page reloads. So on a 404, this
 * gives a failed *stored* run one more look before giving up on it; a
 * completed/cancelled stored run, or no stored run at all, has nothing left
 * to retry and clears normally.
 */
export type RunDiscoveryOutcome =
	| { show: 'active' }
	| { show: 'stored-failed' }
	| { show: 'none' };

/**
 * Decide what to show given whether the active-run check found something and
 * what (if anything) the locally-cached run id resolved to.
 *
 * @param activeResult `'found'` when `GET /runs/active` returned a run,
 *   `'not_found'` on its 404.
 * @param storedRun The run `GET /runs/{storedId}` resolved to (only its
 *   `status` matters here), or `null` when there was no stored id or it
 *   404'd/couldn't be resolved.
 */
export function decideRunDiscovery(
	activeResult: 'found' | 'not_found',
	storedRun: Pick<SetupRun, 'status'> | null
): RunDiscoveryOutcome {
	if (activeResult === 'found') return { show: 'active' };
	if (storedRun && storedRun.status === 'failed') return { show: 'stored-failed' };
	return { show: 'none' };
}

/** `stepBadgeVariant`/`stepStatusLabel` widened to also accept a manifest
 * row's synthetic "pending" status (a not-yet-attempted step — see
 * `SetupManifestStepGroup` below). */
export function manifestStepBadgeVariant(status: string): BadgeVariant {
	if (status === 'pending') return 'neutral';
	return stepBadgeVariant(status as SetupStepStatus);
}

export function manifestStepStatusLabel(status: string): string {
	if (status === 'pending') return 'Not started yet';
	return stepStatusLabel(status as SetupStepStatus);
}

// --- manifest-aware step display -----------------------------------
//
// `SetupRunView.steps` (see run_dto.py) already merges each recipe step with
// whatever attempts exist for it, in execution order, computing "pending" for
// a step with none — so this is a thin adapter to the page's display shape,
// not a re-implementation of that merge. It falls back to the legacy
// attempts-only grouping (`groupStepAttempts` above) for the rare case the
// run's recipe can no longer be resolved and `steps` comes back empty.

/** One step row as the guided-setup panel renders it — a manifest entry
 * (real or synthesized from a flat attempt) with its latest attempt broken
 * out for convenience. `latest` is `null` for a step that hasn't started. */
export interface SetupManifestStepGroup {
	stepKey: string;
	title: string;
	kind: string;
	ordinal: number;
	/** "pending", or a `SetupStepStatus` value. */
	status: string;
	latest: SetupStepAttempt | null;
	attempts: SetupStepAttempt[];
}

export interface ResolvedStepGroups {
	/** Ordered, ready-to-render step rows. */
	groups: SetupManifestStepGroup[];
	/** Whether `groups` reflects the recipe's real, ordered step count (true)
	 * or is a best-effort reconstruction from flat attempt history alone
	 * (false) — see `SetupRunView.steps`'s doc for when that happens. */
	hasManifest: boolean;
}

/**
 * Build the step rows the guided-setup panel renders, merging the recipe's
 * ordered step manifest with each step's attempts (or, lacking a manifest,
 * grouping the flat attempt history the old way).
 */
export function resolveStepGroups(run: Pick<SetupRun, 'steps' | 'attempts'>): ResolvedStepGroups {
	if (run.steps && run.steps.length > 0) {
		const groups = [...run.steps]
			.sort((a, b) => a.ordinal - b.ordinal)
			.map(
				(step): SetupManifestStepGroup => ({
					stepKey: step.step_key,
					title: step.title || humanizeStepKey(step.step_key),
					kind: step.kind,
					ordinal: step.ordinal,
					status: step.status,
					latest: step.attempts.length > 0 ? step.attempts[step.attempts.length - 1] : null,
					attempts: step.attempts
				})
			);
		return { groups, hasManifest: true };
	}

	const groups = groupStepAttempts(run.attempts).map(
		(g, index): SetupManifestStepGroup => ({
			stepKey: g.stepKey,
			title: g.title,
			kind: g.stepKey,
			ordinal: index,
			status: g.latest.status,
			latest: g.latest,
			attempts: g.attempts
		})
	);
	return { groups, hasManifest: false };
}

/**
 * A plain-language progress line built from the resolved step rows. States
 * the real "N of M steps done" once the recipe's step manifest is known;
 * falls back to the old observed-only phrasing (see `runProgressSummary`)
 * when it isn't.
 */
export function runManifestProgressSummary(resolved: ResolvedStepGroups): string {
	const { groups, hasManifest } = resolved;
	if (groups.length === 0) return 'No steps have started yet.';

	const succeeded = groups.filter((g) => g.status === 'succeeded').length;
	const running = groups.filter((g) => g.status === 'running').length;
	const waiting = groups.filter((g) => g.status === 'awaiting_consent').length;
	const needsAttention = groups.filter((g) => g.status === 'action_required').length;
	const failed = groups.filter((g) => g.status === 'failed').length;

	const parts: string[] = [];
	if (hasManifest) {
		parts.push(`${succeeded} of ${groups.length} step${groups.length === 1 ? '' : 's'} done`);
	} else if (succeeded) {
		parts.push(`${succeeded} done`);
	}
	if (running) parts.push(`${running} in progress`);
	if (waiting) parts.push(`${waiting} waiting on you`);
	if (needsAttention) parts.push(`${needsAttention} need attention`);
	if (failed) parts.push(`${failed} couldn't finish`);

	if (parts.length === 0) {
		return hasManifest
			? `${groups.length} step${groups.length === 1 ? '' : 's'} queued`
			: 'No steps have started yet.';
	}
	return parts.join(', ');
}

/**
 * A step-progress detail line from an attempt's raw progress fields (e.g.
 * "512 KB of 6.5 GB" for a download in flight, "3 of 12" for a countable
 * unit), or `null` when the attempt carries no progress info at all. Bytes
 * are formatted with `formatBytes`; any other unit is shown as a plain
 * suffix.
 */
export function stepProgressLabel(
	attempt: Pick<SetupStepAttempt, 'progress_current' | 'progress_total' | 'progress_unit'> | null | undefined
): string | null {
	if (!attempt) return null;
	const { progress_current, progress_total, progress_unit } = attempt;
	if (progress_current == null && progress_total == null) return null;

	const unit = (progress_unit || '').trim().toLowerCase();
	const isBytes = unit === 'bytes' || unit === 'byte';
	const fmt = (n: number) => (isBytes ? formatBytes(n) : formatCount(n));
	const suffix = !isBytes && progress_unit ? ` ${progress_unit}` : '';

	if (progress_current != null && progress_total != null && progress_total > 0) {
		return `${fmt(progress_current)} of ${fmt(progress_total)}${suffix}`;
	}
	if (progress_current != null) {
		return `${fmt(progress_current)}${suffix}`;
	}
	return null;
}

/**
 * A step's progress as a 0-100 percent, or `null` when there's no usable
 * total to divide by (an unknown-size download, or no progress at all yet).
 */
export function stepProgressPercent(
	attempt: Pick<SetupStepAttempt, 'progress_current' | 'progress_total'> | null | undefined
): number | null {
	if (!attempt) return null;
	const { progress_current, progress_total } = attempt;
	if (progress_current == null || progress_total == null || progress_total <= 0) return null;
	return Math.max(0, Math.min(100, Math.round((progress_current / progress_total) * 100)));
}

/** One progress reading at a point in time — the raw material for deriving
 * transfer speed/ETA across two consecutive polls. */
export interface ProgressSample {
	bytes: number;
	at: number;
}

export interface TransferStats {
	/** `null` when there isn't yet a valid prior sample to diff against. */
	bytesPerSecond: number | null;
	/** `null` whenever `bytesPerSecond` is (no rate) or the total is unknown. */
	etaMs: number | null;
}

/**
 * Derive transfer speed/ETA from two consecutive progress polls — the setup
 * run has no server-pushed speed field (unlike the downloader plugin's
 * WebSocket updates — see `DownloadItem.svelte`'s `speed_bytes_per_sec`), so
 * the frontend reconstructs it client-side from the plain byte counts
 * `GET /runs/{id}` already returns on each poll.
 *
 * Defensive against a stale/out-of-order/reset sample (clock went backwards,
 * bytes went down — e.g. a retried step) by returning nulls rather than a
 * nonsense negative rate.
 */
export function computeTransferStats(
	previous: ProgressSample | null,
	current: ProgressSample,
	totalBytes: number | null
): TransferStats {
	if (!previous || current.at <= previous.at || current.bytes < previous.bytes) {
		return { bytesPerSecond: null, etaMs: null };
	}
	const deltaBytes = current.bytes - previous.bytes;
	const deltaMs = current.at - previous.at;
	const bytesPerSecond = (deltaBytes / deltaMs) * 1000;
	if (bytesPerSecond <= 0) return { bytesPerSecond: null, etaMs: null };
	if (totalBytes == null || totalBytes <= current.bytes) {
		return { bytesPerSecond, etaMs: totalBytes != null ? 0 : null };
	}
	const remainingBytes = totalBytes - current.bytes;
	return { bytesPerSecond, etaMs: (remainingBytes / bytesPerSecond) * 1000 };
}

/**
 * Parse a paused step's `consent_request` out of its latest attempt's
 * `safe_output` (mirrors how `suggested_repair` is merged into `safe_output`
 * server-side — see `StepResult.to_safe_output`). Defensive: any shape that
 * doesn't look like a real consent request (missing/malformed `artifacts`)
 * returns `null` rather than throwing, since this reads an opaque JSON blob.
 */
export function extractConsentRequest(
	attempt: Pick<SetupStepAttempt, 'safe_output'> | null | undefined
): SetupConsentRequest | null {
	const raw = attempt?.safe_output?.consent_request as Record<string, unknown> | undefined;
	if (!raw || typeof raw !== 'object') return null;

	const artifactsRaw = raw.artifacts;
	if (!Array.isArray(artifactsRaw)) return null;

	const artifacts = artifactsRaw
		.filter((a): a is Record<string, unknown> => !!a && typeof a === 'object' && typeof a.id === 'string')
		.map((a) => ({
			id: a.id as string,
			display_name: typeof a.display_name === 'string' ? a.display_name : (a.id as string),
			size_bytes: typeof a.size_bytes === 'number' ? a.size_bytes : null,
			kind: typeof a.kind === 'string' ? a.kind : ''
		}));

	const totalBytes = typeof raw.total_bytes === 'number' ? raw.total_bytes : null;

	const providersRaw = raw.providers;
	const providers = Array.isArray(providersRaw)
		? providersRaw
				.filter(
					(p): p is Record<string, unknown> =>
						!!p && typeof p === 'object' && typeof p.id === 'string' && typeof p.field_name === 'string'
				)
				.map((p) => ({
					id: p.id as string,
					name: typeof p.name === 'string' ? p.name : (p.id as string),
					website: typeof p.website === 'string' ? p.website : '',
					field_name: p.field_name as string,
					configured: p.configured === true
				}))
		: undefined;

	return { artifacts, total_bytes: totalBytes, ...(providers ? { providers } : {}) };
}

// --- first-generation handoff ----------------------------------

/** Find a step's manifest entry by `kind` (preferred — stable across a
 * recipe author's choice of `step_key`) with a best-effort fallback to a
 * flat attempt whose `step_key` happens to equal `kind`, for the rare
 * unresolved-recipe case where `steps` is empty. Built-in step kinds
 * (`preset.ensure`, `pipeline.render`, `generation.smoke`, ...) are
 * conventionally also used as their own `step_key` — see `recipes/*.yml`. */
function findStepByKind(run: Pick<SetupRun, 'steps' | 'attempts'>, kind: string): SetupRunStepView | null {
	const fromManifest = run.steps?.find((s) => s.kind === kind);
	if (fromManifest) return fromManifest;

	const attempts = run.attempts.filter((a) => a.step_key === kind);
	if (attempts.length === 0) return null;
	const sorted = [...attempts].sort((a, b) => a.attempt - b.attempt);
	return {
		step_key: kind,
		title: humanizeStepKey(kind),
		kind,
		ordinal: -1,
		status: sorted[sorted.length - 1].status,
		attempts: sorted
	};
}

function latestSucceededAttempt(step: SetupRunStepView | null): SetupStepAttempt | null {
	if (!step || step.attempts.length === 0) return null;
	const last = step.attempts[step.attempts.length - 1];
	return last.status === 'succeeded' ? last : null;
}

/**
 * The preset (and mode) a completed run set the owner up with, so the
 * "Create your first image" handoff can preselect it — read from the
 * `preset.ensure` step's `safe_output.preset_id` (see
 * `PresetEnsureExecutor.execute`) and the `pipeline.render` step's
 * `safe_output.mode` (see `PipelineRenderExecutor.execute`), both real,
 * already-shipped executor contracts (not the pinned shapes). Returns
 * `null` when the preset step hasn't succeeded yet — there's nothing to hand
 * off to.
 */
export function extractGenerationHandoff(
	run: Pick<SetupRun, 'steps' | 'attempts'>
): { presetId: string; mode: string } | null {
	const presetAttempt = latestSucceededAttempt(findStepByKind(run, 'preset.ensure'));
	const presetId = presetAttempt?.safe_output?.preset_id;
	if (typeof presetId !== 'string' || !presetId) return null;

	const renderAttempt = latestSucceededAttempt(findStepByKind(run, 'pipeline.render'));
	const mode = renderAttempt?.safe_output?.mode;
	return { presetId, mode: typeof mode === 'string' && mode ? mode : 'txt2img' };
}

/**
 * A reference to the image `generation.smoke` produced, if that step
 * succeeded and its output actually names one — the shape of that output
 * isn't a shipped contract yet (the step itself is still a "coming later"
 * stub — see `DeferredStepExecutor`), so this reads defensively and returns
 * `null` rather than assuming a key exists.
 */
export function extractSmokeGeneration(
	run: Pick<SetupRun, 'steps' | 'attempts'>
): { generationId: string; filename: string | null } | null {
	const attempt = latestSucceededAttempt(findStepByKind(run, 'generation.smoke'));
	const out = attempt?.safe_output;
	const generationId = out?.generation_id;
	if (typeof generationId !== 'string' || !generationId) return null;

	const rawFilename = (out?.filename ?? out?.file_path) as unknown;
	const filename =
		typeof rawFilename === 'string' && rawFilename ? rawFilename.split('/').pop() || null : null;
	return { generationId, filename };
}
