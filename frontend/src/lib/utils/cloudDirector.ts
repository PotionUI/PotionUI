import type { CloudCapabilities } from '$lib/form/capabilityBinder';
import { cloudModelId } from '$lib/form/capabilityBinder';
import type { DirectorCapabilities, DirectorMediaValue, VideoDirectorValue } from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';
import { formatUsd, parseAmount } from '$lib/utils/cloudCost';
import { deriveChainSegmentSubType, mergeRawCapabilities, modelShotDurationReasons } from '$lib/utils/videoDirector';

export interface DirectorModelOverlay {
	label: string | null;
	raw: Record<string, unknown>;
}

export interface CloudEstimate {
	shots: number;
	total_usd: string | null;
	known: boolean;
}

export interface EstimateShot {
	task: 'txt2video' | 'img2video';
	params: Record<string, unknown>;
}

type Raw = Record<string, unknown>;

function isRaw(value: unknown): value is Raw {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function selectedCloudModelId(formData: Record<string, unknown> | null | undefined): string | null {
	if (!formData) return null;
	const preferred = cloudModelId(formData.model);
	if (preferred) return preferred;
	for (const value of Object.values(formData)) {
		const id = cloudModelId(value);
		if (id) return id;
	}
	return null;
}

export function directorOverlayFrom(caps: CloudCapabilities | null | undefined): DirectorModelOverlay | null {
	const raw = (caps as { video_director?: unknown } | null | undefined)?.video_director;
	if (!isRaw(raw)) return null;
	const label = typeof caps?.label === 'string' && caps.label.trim() ? caps.label.trim() : null;
	return { label, raw };
}

export function applyModelOverlay(raw: unknown, overlay: DirectorModelOverlay | null): unknown {
	if (!isRaw(raw) || !overlay) return raw;
	const baseModes = isRaw(raw.modes) ? raw.modes : {};
	const overlayModes = isRaw(overlay.raw.modes) ? overlay.raw.modes : {};
	const tuned = Object.fromEntries(Object.entries(overlayModes).filter(([name, value]) => value === null || isRaw(baseModes[name])));
	const merged = mergeRawCapabilities(raw, { ...overlay.raw, modes: tuned });
	if (overlay.label && typeof merged.model_label !== 'string') merged.model_label = overlay.label;
	return merged;
}

function joinWords(items: string[]): string {
	if (items.length <= 1) return items.join('');
	return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function shotNumbers(indexes: number[]): string {
	return indexes.length === 1 ? `shot ${indexes[0] + 1}` : `shots ${joinWords(indexes.map((i) => String(i + 1)))}`;
}

function capitalized(text: string): string {
	return text.charAt(0).toUpperCase() + text.slice(1);
}

function edgeAllowed(caps: DirectorCapabilities): { start: boolean; end: boolean } {
	const keyframes = caps.modes.director?.keyframes;
	return {
		start: caps.enabledModes.includes('i2v') || caps.enabledModes.includes('flf') || keyframes === 'first_only' || keyframes === 'anywhere',
		end: caps.enabledModes.includes('flf') || keyframes === 'anywhere'
	};
}

export function modelFitNotices(doc: VideoDirectorValue, caps: DirectorCapabilities): string[] {
	const model = caps.modelLabel;
	if (!model || !caps.segmentRouting) return [];
	const segments = doc.chain.segments;
	const director = caps.modes.director;
	const edges = edgeAllowed(caps);
	const notices: string[] = [];

	if (director?.maxSegments != null && segments.length > director.maxSegments) {
		notices.push(
			`${model} makes up to ${director.maxSegments} shots in one film. Your film has ${segments.length}, so ${shotNumbers(segments.map((_, i) => i).slice(director.maxSegments))} would not be made.`
		);
	}
	if (!edges.start) {
		const withStart = segments.flatMap((s, i) => (s.keyframe ? [i] : []));
		if (withStart.length > 0) notices.push(`${model} cannot start a shot from a picture, so the start picture on ${shotNumbers(withStart)} would not be used.`);
	}
	if (!edges.end) {
		const withEnd = segments.flatMap((s, i) => (s.last_keyframe ? [i] : []));
		if (withEnd.length > 0) notices.push(`${model} cannot end a shot on a picture, so the end picture on ${shotNumbers(withEnd)} would not be used.`);
	}
	if (!director?.audio && doc.chain.audio.length > 0) {
		notices.push(`${model} does not take an audio track, so the audio you added would not be used.`);
	}
	const badLength = segments.flatMap((s, i) => (modelShotDurationReasons([s.duration], caps).length > 0 ? [i] : []));
	if (badLength.length > 0) {
		const rule = caps.durations
			? `${model} renders shots of ${joinWords(caps.durations.map((d) => String(Number(d.toFixed(2)))))} s.`
			: `${model} renders up to ${secondsText(caps.maxDuration ?? 0)} a shot.`;
		notices.push(`${rule} ${capitalized(shotNumbers(badLength))} ${badLength.length === 1 ? 'is' : 'are'} a different length.`);
	}
	return notices;
}

function secondsText(value: number): string {
	return `${Number(value.toFixed(2))} s`;
}

export function modelFacts(caps: DirectorCapabilities): string[] {
	if (!caps.modelLabel) return [];
	const director = caps.modes.director;
	const facts: string[] = [];
	const edges = edgeAllowed(caps);
	facts.push(edges.start ? 'Starts from a picture' : 'No start picture');
	facts.push(edges.end ? 'Ends on a picture' : 'No end picture');
	if (caps.durations) facts.push(`Shots of ${caps.durations.map((d) => Number(d.toFixed(2))).join(', ')} s`);
	else if (caps.maxDuration != null) facts.push(`Up to ${secondsText(caps.maxDuration)} a shot`);
	if (director?.maxSegments != null) facts.push(`Up to ${director.maxSegments} shots`);
	else if (!director) facts.push('One shot per film');
	facts.push(director?.audio ? 'Takes audio' : 'No audio');
	return facts;
}

export function parseCloudEstimate(raw: unknown): CloudEstimate | null {
	const data = isRaw(raw) && isRaw(raw.data) ? raw.data : raw;
	if (!isRaw(data)) return null;
	const shots = typeof data.shot_count === 'number' ? data.shot_count : Number(data.shot_count);
	if (!Number.isFinite(shots) || shots <= 0) return null;
	const total = data.total_usd == null ? null : String(data.total_usd);
	return { shots, total_usd: parseAmount(total) === null ? null : total, known: data.known === true };
}

export function describeEstimate(estimate: CloudEstimate | null | undefined): string | null {
	if (!estimate || estimate.shots < 2) return null;
	const shots = `${estimate.shots} shots`;
	if (estimate.total_usd === null) return `Price unknown for ${shots}`;
	const text = `About ${formatUsd(estimate.total_usd)} for ${shots}`;
	return estimate.known ? text : `${text}, some not priced`;
}

const ESTIMATE_FORM_PARAMS: Record<string, string> = {
	resolution: 'resolution',
	aspect_ratio: 'aspect_ratio',
	generate_audio: 'generate_audio'
};

export function estimateShotsFor(
	doc: VideoDirectorValue,
	formData: Record<string, unknown> | null | undefined,
	shotIds: Iterable<string>
): EstimateShot[] {
	const wanted = new Set(shotIds);
	const shared: Record<string, unknown> = {};
	for (const [field, param] of Object.entries(ESTIMATE_FORM_PARAMS)) {
		const value = formData?.[field];
		if (value !== undefined && value !== null && value !== '') shared[param] = value;
	}
	return doc.chain.segments.flatMap((segment, index) =>
		wanted.size > 0 && !wanted.has(segment.id)
			? []
			: [
					{
						task: deriveChainSegmentSubType(segment, index) === 't2v' ? 'txt2video' : 'img2video',
						params: { ...shared, duration_s: segment.duration }
					}
				]
	);
}

export type HostedRetryKind = 'plain' | 'handoff' | 'restart';

export interface HostedRetryPlan {
	doc: VideoDirectorValue;
	shotIds: string[];
	handoffFrom: string | null;
	kind: HostedRetryKind;
}

function continuesPrevious(doc: VideoDirectorValue, caps: DirectorCapabilities, index: number): boolean {
	if (index <= 0 || caps.modes.director?.continuationDisabled === true) return false;
	return deriveChainSegmentSubType(doc.chain.segments[index], index) === 'chain';
}

function retryStartIndex(
	doc: VideoDirectorValue,
	runs: Record<string, DirectorRunState> | null | undefined,
	index: number
): number {
	let start = index;
	while (start > 0 && runs?.[doc.chain.segments[start - 1].id]?.status === 'failed') start -= 1;
	return start;
}

export function hostedRetryKind(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	runs: Record<string, DirectorRunState> | null | undefined,
	shotId: string
): HostedRetryKind {
	if (!caps.modelLabel || !caps.segmentRouting) return 'plain';
	const found = doc.chain.segments.findIndex((s) => s.id === shotId);
	if (found < 0) return 'plain';
	const index = retryStartIndex(doc, runs, found);
	if (!continuesPrevious(doc, caps, index)) return 'plain';
	const previous = runs?.[doc.chain.segments[index - 1].id];
	return caps.modes.director?.continueFromVideo === true && previous?.status === 'done' && !!previous.outputPath ? 'handoff' : 'restart';
}

export function hostedRetryNotice(caps: DirectorCapabilities): string {
	const model = caps.modelLabel ?? 'This model';
	return `${model} cannot carry on from a finished shot, so the whole film is made again from the first shot.`;
}

export function planHostedRetry(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	runs: Record<string, DirectorRunState> | null | undefined,
	shotIds: string[]
): HostedRetryPlan {
	const unchanged: HostedRetryPlan = { doc, shotIds, handoffFrom: null, kind: 'plain' };
	if (!caps.modelLabel || !caps.segmentRouting) return unchanged;
	const segments = doc.chain.segments;
	const requested = segments.flatMap((s, i) => (shotIds.includes(s.id) ? [i] : []));
	if (requested.length === 0) return unchanged;
	const start = retryStartIndex(doc, runs, requested[0]);
	const kind = hostedRetryKind(doc, caps, runs, segments[start].id);
	if (kind === 'restart') return { doc, shotIds: segments.map((s) => s.id), handoffFrom: null, kind };
	let end = requested[requested.length - 1];
	while (end + 1 < segments.length && runs?.[segments[end + 1].id]?.status === 'failed') end += 1;
	const ids = segments.slice(start, end + 1).map((s) => s.id);
	if (kind === 'plain') return { doc, shotIds: ids, handoffFrom: null, kind };
	const previous = segments[start - 1];
	const previousRun = runs![previous.id];
	const path = previousRun.outputPath!;
	const frame: DirectorMediaValue = { path, relative_path: path, url: previousRun.posterUrl ?? path, type: 'video' };
	const nextSegments = segments.map((s, i) => (i === start ? { ...s, keyframe: frame, keyframe_strength: 1 } : s));
	return { doc: { ...doc, chain: { ...doc.chain, segments: nextSegments } }, shotIds: ids, handoffFrom: previous.id, kind };
}

export function hasLengthProblems(doc: VideoDirectorValue, caps: DirectorCapabilities): boolean {
	return doc.chain.segments.some((segment) => modelShotDurationReasons([segment.duration], caps).length > 0);
}

function nearestLength(duration: number, caps: DirectorCapabilities): number {
	if (caps.durations) {
		return caps.durations.reduce((best, candidate) => (Math.abs(candidate - duration) < Math.abs(best - duration) ? candidate : best));
	}
	return caps.maxDuration != null ? Math.min(duration, caps.maxDuration) : duration;
}

export function snapShotLengths(doc: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	if (!hasLengthProblems(doc, caps)) return doc;
	const segments = doc.chain.segments.map((segment) =>
		modelShotDurationReasons([segment.duration], caps).length > 0 ? { ...segment, duration: nearestLength(segment.duration, caps) } : segment
	);
	return { ...doc, chain: { ...doc.chain, segments } };
}

export function modelDefaultLength(caps: DirectorCapabilities): number {
	const wanted = caps.modes.director?.defaultSegmentDuration ?? caps.defaultDuration;
	if (caps.durations) {
		return caps.durations.reduce((best, candidate) => (Math.abs(candidate - wanted) < Math.abs(best - wanted) ? candidate : best));
	}
	return caps.maxDuration != null ? Math.min(wanted, caps.maxDuration) : wanted;
}

function isBlankFilm(doc: VideoDirectorValue): boolean {
	const [only] = doc.chain.segments;
	return (
		doc.chain.segments.length === 1 &&
		!only.prompt.trim() &&
		!only.keyframe &&
		!only.last_keyframe &&
		doc.chain.audio.length === 0 &&
		doc.chain.keyframes.length === 0
	);
}

function withDefaultLengths(doc: VideoDirectorValue, ids: string[]): VideoDirectorValue {
	return { ...doc, ui: { ...doc.ui, defaultLengths: ids } };
}

export function followModelDefaultLengths(doc: VideoDirectorValue, caps: DirectorCapabilities): VideoDirectorValue {
	if (!caps.modelLabel || !caps.segmentRouting) return doc;
	let marked = doc.ui?.defaultLengths;
	if (marked === undefined) {
		if (!isBlankFilm(doc)) return doc;
		marked = doc.chain.segments.map((s) => s.id);
	}
	const target = modelDefaultLength(caps);
	const ids = new Set(marked);
	let moved = false;
	const segments = doc.chain.segments.map((segment) => {
		if (!ids.has(segment.id) || Math.abs(segment.duration - target) < 1e-6) return segment;
		moved = true;
		return { ...segment, duration: target };
	});
	const kept = marked.filter((id) => doc.chain.segments.some((s) => s.id === id));
	const sameMarks = doc.ui?.defaultLengths !== undefined && kept.length === marked.length;
	if (!moved && sameMarks) return doc;
	return withDefaultLengths(moved ? { ...doc, chain: { ...doc.chain, segments } } : doc, kept);
}

export function withDefaultLengthShot(doc: VideoDirectorValue, caps: DirectorCapabilities, shotId: string): VideoDirectorValue {
	if (!caps.modelLabel || !caps.segmentRouting) return doc;
	const target = modelDefaultLength(caps);
	const segments = doc.chain.segments.map((s) => (s.id === shotId ? { ...s, duration: target } : s));
	return withDefaultLengths({ ...doc, chain: { ...doc.chain, segments } }, [...(doc.ui?.defaultLengths ?? []), shotId]);
}

export function withoutDefaultLength(doc: VideoDirectorValue, shotId: string): VideoDirectorValue {
	const marks = doc.ui?.defaultLengths;
	if (!marks || !marks.includes(shotId)) return doc;
	return withDefaultLengths(doc, marks.filter((id) => id !== shotId));
}
