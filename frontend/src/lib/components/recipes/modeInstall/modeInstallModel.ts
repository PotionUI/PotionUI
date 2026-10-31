import type {
	ModeConsentDisk,
	ModeConsentMode,
	ModeConsentRequest,
	ModeConsentSlot,
	ModeConsentVariant,
	ModeDownloadProgress,
	ModesGrantBody
} from '$lib/services/api/recipeModes';
import type { SetupStepAttempt } from '$lib/services/api/setup';
import { formatBytes, formatBytesShort } from '$lib/utils/format';

export interface ModeInstallState {
	modes: Record<string, boolean>;
	extras: Record<string, boolean>;
	variants: Record<string, string>;
	targetId: string;
}

export interface ModeInstallTotals {
	bytes: number;
	files: ModeConsentSlot[];
	bytesByDisk: Record<string, number>;
}

export interface DiskShortfall {
	disk: ModeConsentDisk;
	needed: number;
	usable: number;
	short: number;
}

export function extractModesConsent(
	attempt: Pick<SetupStepAttempt, 'safe_output'> | null | undefined
): ModeConsentRequest | null {
	const raw = attempt?.safe_output?.consent_request as Record<string, unknown> | undefined;
	if (!raw || typeof raw !== 'object' || raw.kind !== 'modes') return null;
	if (!Array.isArray(raw.modes) || !Array.isArray(raw.slots) || !raw.target) return null;
	return {
		...(raw as unknown as ModeConsentRequest),
		disk: Array.isArray(raw.disk) ? (raw.disk as ModeConsentDisk[]) : [],
		providers: Array.isArray(raw.providers) ? (raw.providers as ModeConsentRequest['providers']) : [],
		warnings: Array.isArray(raw.warnings) ? (raw.warnings as string[]) : [],
		estimate: (raw.estimate as ModeConsentRequest['estimate']) ?? null,
		gpu: (raw.gpu as ModeConsentRequest['gpu']) ?? null
	};
}

export function initialInstallState(
	request: ModeConsentRequest,
	preselect: readonly string[] = []
): ModeInstallState {
	const wanted = new Set(preselect);
	const payloadPicked = request.modes.some((mode) => !mode.ready && mode.selected);
	const tickMissing = wanted.size === 0 && !payloadPicked;
	const modes: Record<string, boolean> = {};
	for (const mode of request.modes) {
		if (mode.ready || mode.locked) modes[mode.id] = true;
		else if (wanted.size > 0) modes[mode.id] = wanted.has(mode.id) || mode.selected;
		else modes[mode.id] = tickMissing || mode.selected;
	}
	return { modes, extras: {}, variants: {}, targetId: request.target.backend_id };
}

export function chosenVariant(slot: ModeConsentSlot, state: ModeInstallState): ModeConsentVariant | null {
	const picked = state.variants[slot.id];
	const byPick = picked ? slot.variants.find((variant) => variant.id === picked) : undefined;
	if (byPick) return byPick;
	return (
		slot.variants.find((variant) => variant.id === slot.recommended_variant_id) ?? slot.variants[0] ?? null
	);
}

export function slotNeedsDownload(slot: ModeConsentSlot, state: ModeInstallState): boolean {
	if (slot.manual || slot.satisfied_by) return false;
	const variant = chosenVariant(slot, state);
	return !!variant && !variant.installed;
}

export function tickedModes(request: ModeConsentRequest, state: ModeInstallState): ModeConsentMode[] {
	return request.modes.filter((mode) => state.modes[mode.id]);
}

export function newModesCount(request: ModeConsentRequest, state: ModeInstallState): number {
	return request.modes.filter((mode) => !mode.ready && !mode.locked && state.modes[mode.id]).length;
}

export function selectedSlotIds(request: ModeConsentRequest, state: ModeInstallState): Set<string> {
	const ids = new Set<string>();
	const slotsById = new Map(request.slots.map((slot) => [slot.id, slot]));
	for (const mode of tickedModes(request, state)) {
		for (const id of mode.slot_ids) ids.add(id);
	}
	for (const [id, ticked] of Object.entries(state.extras)) {
		if (ticked && slotsById.get(id)?.extra) ids.add(id);
	}
	for (const mode of tickedModes(request, state)) {
		for (const group of mode.any_of ?? []) {
			const present = group.some((id) => {
				const slot = slotsById.get(id);
				if (!slot) return false;
				if (state.extras[id]) return true;
				const variant = chosenVariant(slot, state);
				return !!variant?.installed || !!slot.satisfied_by;
			});
			if (!present && group.length > 0) ids.add(group[0]);
		}
	}
	return ids;
}

export function computeTotals(request: ModeConsentRequest, state: ModeInstallState): ModeInstallTotals {
	const selected = selectedSlotIds(request, state);
	const files: ModeConsentSlot[] = [];
	const bytesByDisk: Record<string, number> = {};
	let bytes = 0;
	for (const slot of request.slots) {
		if (!selected.has(slot.id) || !slotNeedsDownload(slot, state)) continue;
		const variant = chosenVariant(slot, state);
		const size = variant?.size_bytes ?? 0;
		files.push(slot);
		bytes += size;
		const diskId = slot.disk_id ?? request.disk[0]?.id ?? '';
		bytesByDisk[diskId] = (bytesByDisk[diskId] ?? 0) + size;
	}
	return { bytes, files, bytesByDisk };
}

export function usableBytes(disk: ModeConsentDisk): number | null {
	if (disk.free_bytes == null) return null;
	return Math.max(0, disk.free_bytes - (disk.reserve_bytes ?? 0));
}

export function diskShortfall(request: ModeConsentRequest, totals: ModeInstallTotals): DiskShortfall | null {
	let worst: DiskShortfall | null = null;
	for (const disk of request.disk) {
		const usable = usableBytes(disk);
		if (usable == null) continue;
		const needed = totals.bytesByDisk[disk.id] ?? 0;
		const short = needed - usable;
		if (short > 0 && (!worst || short > worst.short)) worst = { disk, needed, usable, short };
	}
	return worst;
}

export function freeHere(request: ModeConsentRequest): number | null {
	const frees = request.disk.map((disk) => disk.free_bytes).filter((value): value is number => value != null);
	if (frees.length === 0) return request.target.free_bytes ?? null;
	return Math.min(...frees);
}

export function estimateSeconds(request: ModeConsentRequest, bytes: number): number | null {
	const speed = request.estimate?.bytes_per_sec;
	if (!speed || speed <= 0 || bytes <= 0) return null;
	return bytes / speed;
}

export function formatEta(seconds: number): string {
	if (seconds < 45) return 'About 1 min';
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `About ${Math.max(1, minutes)} min`;
	const hours = Math.floor(minutes / 60);
	const rest = minutes % 60;
	return rest > 0 ? `About ${hours} h ${rest} min` : `About ${hours} h`;
}

export function formatSpeed(bytesPerSec: number): string {
	return `${Math.max(1, Math.round(bytesPerSec / 1_000_000))} MB/s`;
}

export function sizeLabel(bytes: number): string {
	return formatBytesShort(bytes);
}

export function variantOverrides(request: ModeConsentRequest, state: ModeInstallState): ModeConsentVariant[] {
	const selected = selectedSlotIds(request, state);
	const out: ModeConsentVariant[] = [];
	for (const slot of request.slots) {
		const picked = state.variants[slot.id];
		if (!picked || picked === slot.recommended_variant_id || !selected.has(slot.id)) continue;
		const variant = slot.variants.find((entry) => entry.id === picked);
		if (variant && !variant.installed) out.push(variant);
	}
	return out;
}

export function installLabel(request: ModeConsentRequest, state: ModeInstallState): string {
	const count = newModesCount(request, state);
	const overrides = variantOverrides(request, state);
	const extrasCount = Object.entries(state.extras).filter(
		([id, ticked]) => ticked && request.slots.some((slot) => slot.id === id)
	).length;
	let label = count > 0 ? `Install ${count} ${count === 1 ? 'mode' : 'modes'}` : 'Install';
	if (overrides.length === 1) label += ` and ${overrides[0].label}`;
	else if (overrides.length > 1) label += ` and ${overrides.length} choices`;
	else if (count === 0 && extrasCount > 0) label = 'Install extras';
	return label;
}

export function installBlocker(request: ModeConsentRequest, state: ModeInstallState): string | null {
	if (request.target.kind === 'external') return 'This computer cannot download files for this server';
	const totals = computeTotals(request, state);
	const short = diskShortfall(request, totals);
	if (short) return `Deselect ${formatBytes(short.short, 1)} to continue`;
	const picked = newModesCount(request, state) > 0 || totals.files.length > 0;
	if (!picked) return 'Pick a mode to install';
	return null;
}

export interface GatedNotice {
	labels: string[];
	licenseUrl: string | null;
}

export function gatedNotice(request: ModeConsentRequest, state: ModeInstallState): GatedNotice | null {
	const totals = computeTotals(request, state);
	const labels: string[] = [];
	let licenseUrl: string | null = null;
	for (const slot of totals.files) {
		const variant = chosenVariant(slot, state);
		if (!variant?.gated) continue;
		const name = slot.used_by.length === 1 ? modeLabelFor(request, slot.used_by[0]) : slot.label;
		if (!labels.includes(name)) labels.push(name);
		licenseUrl = licenseUrl ?? variant.license_url;
	}
	return labels.length > 0 ? { labels, licenseUrl } : null;
}

export function modeLabelFor(request: ModeConsentRequest, modeId: string): string {
	return request.modes.find((mode) => mode.id === modeId)?.label ?? modeId;
}

export function joinLabels(labels: readonly string[]): string {
	if (labels.length <= 1) return labels[0] ?? '';
	return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

export function usedByLabel(request: ModeConsentRequest, slot: ModeConsentSlot): string {
	const total = request.modes.length;
	if (total > 1 && slot.used_by.length >= total) return 'Used by every mode';
	if (slot.used_by.length === 0) return '';
	if (slot.used_by.length <= 2) return slot.used_by.map((id) => modeLabelFor(request, id)).join(', ');
	return `Used by ${slot.used_by.length} modes`;
}

export function buildGrantBody(request: ModeConsentRequest, state: ModeInstallState): ModesGrantBody {
	const selected = selectedSlotIds(request, state);
	const selections: Record<string, string> = {};
	for (const slot of request.slots) {
		const picked = state.variants[slot.id];
		if (picked && selected.has(slot.id) && slot.variants.some((variant) => variant.id === picked)) {
			selections[slot.id] = picked;
		}
	}
	const extras = request.slots
		.filter((slot) => slot.extra && state.extras[slot.id])
		.map((slot) => slot.id);
	return {
		selections,
		modes: request.modes.filter((mode) => state.modes[mode.id]).map((mode) => mode.id),
		extras,
		target: state.targetId
	};
}

export function mergeReplan(
	current: ModeConsentRequest,
	next: ModeConsentRequest
): ModeConsentRequest {
	return {
		...current,
		target: next.target,
		gpu: next.gpu ?? current.gpu,
		modes: next.modes,
		slots: next.slots,
		disk: next.disk,
		estimate: next.estimate,
		providers: next.providers ?? current.providers,
		warnings: next.warnings ?? current.warnings
	};
}

export function extractDownloadProgress(
	attempt: Pick<SetupStepAttempt, 'safe_output'> | null | undefined
): ModeDownloadProgress | null {
	const raw = attempt?.safe_output as Record<string, unknown> | null | undefined;
	if (!raw || !Array.isArray(raw.files)) return null;
	return {
		files: raw.files as ModeDownloadProgress['files'],
		files_done: typeof raw.files_done === 'number' ? raw.files_done : 0,
		files_total: typeof raw.files_total === 'number' ? raw.files_total : (raw.files as unknown[]).length,
		speed_bytes_per_sec: typeof raw.speed_bytes_per_sec === 'number' ? raw.speed_bytes_per_sec : null,
		eta_seconds: typeof raw.eta_seconds === 'number' ? raw.eta_seconds : null
	};
}

export function fileProgressPercent(file: { size_bytes: number | null; downloaded_bytes: number }): number {
	if (!file.size_bytes || file.size_bytes <= 0) return 0;
	return Math.max(0, Math.min(100, Math.round((file.downloaded_bytes / file.size_bytes) * 100)));
}

export function formatEtaLeft(seconds: number): string {
	if (seconds < 60) return `about ${Math.max(1, Math.round(seconds))} s left`;
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `about ${minutes} min left`;
	return `about ${Math.floor(minutes / 60)} h ${minutes % 60} min left`;
}

export interface ApprovedModesPlan {
	presetId: string;
	modes: string[];
	bytes: number | null;
	files: number;
}

function allAttempts(run: { steps?: { attempts: SetupStepAttempt[] }[]; attempts?: SetupStepAttempt[] }) {
	const fromSteps = (run.steps ?? []).flatMap((step) => step.attempts ?? []);
	return fromSteps.length > 0 ? fromSteps : (run.attempts ?? []);
}

export function extractApprovedPlan(run: {
	steps?: { attempts: SetupStepAttempt[] }[];
	attempts?: SetupStepAttempt[];
}): ApprovedModesPlan | null {
	const attempts = allAttempts(run);
	for (let index = attempts.length - 1; index >= 0; index -= 1) {
		const plan = attempts[index].safe_output?.plan as Record<string, unknown> | undefined;
		if (!plan || typeof plan !== 'object' || !Array.isArray(plan.modes)) continue;
		return {
			presetId: typeof plan.preset_id === 'string' ? plan.preset_id : '',
			modes: (plan.modes as unknown[]).filter((id): id is string => typeof id === 'string'),
			bytes: typeof plan.bytes === 'number' ? plan.bytes : null,
			files: Array.isArray(plan.files) ? plan.files.length : 0
		};
	}
	return null;
}

export function isModesRun(run: {
	steps?: { kind: string }[];
	attempts?: SetupStepAttempt[];
}): boolean {
	if ((run.steps ?? []).some((step) => step.kind === 'modes.plan')) return true;
	return false;
}

export function aggregateDownloadBytes(progress: ModeDownloadProgress): { done: number; total: number } {
	let done = 0;
	let total = 0;
	for (const file of progress.files) {
		total += file.size_bytes ?? 0;
		done += file.status === 'completed' ? (file.size_bytes ?? file.downloaded_bytes) : file.downloaded_bytes;
	}
	return { done, total };
}

export function downloadFileLabel(file: ModeDownloadProgress['files'][number]): string {
	if (file.status === 'completed') return 'Done';
	if (file.status === 'pending') return 'Waiting';
	if (file.status === 'failed') return 'Failed';
	if (file.status === 'cancelled') return 'Cancelled';
	if (file.status === 'paused') return 'Paused';
	return `${fileProgressPercent(file)}%`;
}

export function isDerivedRecipeId(recipeId: string): boolean {
	return recipeId.startsWith('modes-');
}
