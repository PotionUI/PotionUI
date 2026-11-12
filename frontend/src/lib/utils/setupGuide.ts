import type { SetupMachine, SetupRecipe, SetupRun, SetupRunStatus } from '$lib/services/api/setup';
import { formatBytes } from './format';

export type SetupStepId =
	| 'welcome'
	| 'account'
	| 'computer'
	| 'starter'
	| 'install'
	| 'ready';

export const SETUP_STEPS: ReadonlyArray<{ id: SetupStepId; label: string }> = [
	{ id: 'welcome', label: 'Welcome' },
	{ id: 'account', label: 'Account' },
	{ id: 'computer', label: 'Computer' },
	{ id: 'starter', label: 'Starter' },
	{ id: 'install', label: 'Install' },
	{ id: 'ready', label: 'Ready' }
];

export const SETUP_STEP_COUNT = SETUP_STEPS.length;

export const GUIDE_LOCKED_STEP_COUNT = 2;

export const NO_CARD_SENTENCE =
	'This computer has no NVIDIA graphics card, so pictures are made somewhere else.';

export const TRUST_LINE =
	'Runs on your computer. Your account and images stay here. Setup downloads from Hugging Face only after you approve.';

export function stepIndex(id: SetupStepId): number {
	return SETUP_STEPS.findIndex((s) => s.id === id);
}

export function stepLabel(id: SetupStepId): string {
	return SETUP_STEPS[stepIndex(id)].label;
}

export type RunPhase = 'none' | 'consent' | 'active' | 'failed' | 'completed';

export function runPhase(run: Pick<SetupRun, 'status'> | null | undefined): RunPhase {
	if (!run) return 'none';
	switch (run.status) {
		case 'awaiting_consent':
			return 'consent';
		case 'pending':
		case 'running':
		case 'paused':
			return 'active';
		case 'failed':
			return 'failed';
		case 'completed':
			return 'completed';
		case 'cancelled':
			return 'none';
	}
}

export interface GuideFacts {
	ownerExists: boolean;
	welcomeSeen: boolean;
	computerSeen: boolean;
	run: Pick<SetupRun, 'status'> | null;
	instanceReady: boolean;
}

export function deriveCurrentStep(facts: GuideFacts): SetupStepId {
	if (!facts.ownerExists) return facts.welcomeSeen ? 'account' : 'welcome';
	const phase = runPhase(facts.run);
	if (phase === 'consent' || phase === 'active' || phase === 'failed') return 'install';
	if (phase === 'completed') return 'ready';
	if (facts.instanceReady) return 'ready';
	if (!facts.computerSeen) return 'computer';
	return 'starter';
}

export function canNavigateToStep(target: SetupStepId, current: SetupStepId, ownerExists: boolean): boolean {
	const index = stepIndex(target);
	if (index >= stepIndex(current)) return false;
	if (ownerExists && index < GUIDE_LOCKED_STEP_COUNT) return false;
	return true;
}

export type StatusTone = 'signal' | 'success' | 'warning';

export interface StepStatus {
	text: string;
	tone: StatusTone;
}

export function stepStatus(id: SetupStepId, run: Pick<SetupRun, 'status'> | null = null): StepStatus | null {
	switch (id) {
		case 'welcome':
			return null;
		case 'account':
			return { text: 'Required', tone: 'signal' };
		case 'computer':
			return { text: 'Nothing to do', tone: 'signal' };
		case 'starter':
			return { text: 'Optional, skip any time', tone: 'signal' };
		case 'install': {
			const phase = runPhase(run);
			if (phase === 'consent') return { text: 'Needs your go-ahead', tone: 'signal' };
			if (phase === 'active') return { text: 'Running', tone: 'signal' };
			if (phase === 'failed') return { text: 'Needs attention', tone: 'warning' };
			return { text: 'Required for the starter', tone: 'signal' };
		}
		case 'ready':
			return { text: 'Done', tone: 'success' };
	}
}

export function showsSkipGuide(id: SetupStepId): boolean {
	return id === 'computer' || id === 'starter' || id === 'install';
}

const BYTES_PER_GIB = 1024 ** 3;

export function vramGb(machine: SetupMachine | null): number | null {
	const bytes = machine?.gpu?.vram_bytes;
	if (bytes == null || bytes <= 0) return null;
	return Math.round(bytes / BYTES_PER_GIB);
}

export function ramGb(machine: SetupMachine | null): number | null {
	const bytes = machine?.ram_total_bytes;
	if (bytes == null || bytes <= 0) return null;
	return Math.round(bytes / BYTES_PER_GIB);
}

type StarterNeeds = Pick<SetupRecipe, 'min_vram_gb' | 'recommended_vram_gb'>;

export function starterRequiredGb(recipe: StarterNeeds): number | null {
	return recipe.recommended_vram_gb ?? recipe.min_vram_gb ?? null;
}

export function firstRunStarters(recipes: SetupRecipe[]): SetupRecipe[] {
	return recipes.filter((recipe) => recipe.first_run);
}

export function recommendStarter(recipes: SetupRecipe[], machine: SetupMachine | null): string | null {
	if (!machine?.gpu || machine.gpu.supported === false) return null;
	const vram = vramGb(machine);
	if (vram == null) return null;
	let best: SetupRecipe | null = null;
	for (const recipe of firstRunStarters(recipes)) {
		const required = starterRequiredGb(recipe);
		if (required == null || vram < required) continue;
		if (best === null || isBetterStarter(recipe, best)) best = recipe;
	}
	return best?.id ?? null;
}

function isBetterStarter(candidate: SetupRecipe, current: SetupRecipe): boolean {
	const a = starterRequiredGb(candidate) ?? 0;
	const b = starterRequiredGb(current) ?? 0;
	if (a !== b) return a > b;
	const recA = candidate.recommended_vram_gb != null;
	const recB = current.recommended_vram_gb != null;
	if (recA !== recB) return recA;
	const sizeA = candidate.total_download_bytes ?? Number.POSITIVE_INFINITY;
	const sizeB = current.total_download_bytes ?? Number.POSITIVE_INFINITY;
	return sizeA < sizeB;
}

export function orderStarters(recipes: SetupRecipe[], recommendedId: string | null): SetupRecipe[] {
	return [...firstRunStarters(recipes)].sort((a, b) => {
		const rec = Number(b.id === recommendedId) - Number(a.id === recommendedId);
		if (rec !== 0) return rec;
		return (a.total_download_bytes ?? Number.POSITIVE_INFINITY) - (b.total_download_bytes ?? Number.POSITIVE_INFINITY);
	});
}

export type FitTone = 'good' | 'low' | 'dim';

export interface StarterFit {
	tone: FitTone;
	text: string;
}

export function starterFloorGb(recipe: StarterNeeds): number | null {
	return recipe.min_vram_gb ?? recipe.recommended_vram_gb ?? null;
}

export interface StarterShortfall {
	neededGb: number;
	cardGb: number;
	tested: boolean;
}

export function hasLocalCard(machine: SetupMachine | null): boolean {
	return machine !== null && !!machine.gpu && machine.gpu.supported !== false;
}

export function starterShortfall(recipe: StarterNeeds, machine: SetupMachine | null): StarterShortfall | null {
	if (!hasLocalCard(machine)) return null;
	const floor = starterFloorGb(recipe);
	const cardGb = vramGb(machine);
	if (floor == null || cardGb == null || cardGb >= floor) return null;
	return { neededGb: floor, cardGb, tested: recipe.min_vram_gb != null };
}

export function shortfallSentence(shortfall: StarterShortfall): string {
	return shortfall.tested
		? `Needs ${/^(8|11|18)/.test(String(shortfall.neededGb)) ? 'an' : 'a'} ${shortfall.neededGb} GB graphics card. Yours has ${shortfall.cardGb} GB.`
		: `Not tested below ${shortfall.neededGb} GB. Yours has ${shortfall.cardGb} GB.`;
}

export function shortfallAcknowledgement(shortfall: StarterShortfall): Record<string, unknown> {
	return {
		card_memory_gb: shortfall.cardGb,
		needed_memory_gb: shortfall.neededGb,
		below_graphics_memory: true
	};
}

export function runAcknowledgedShortfall(run: Pick<SetupRun, 'safe_input'> | null | undefined): boolean {
	return run?.safe_input?.below_graphics_memory === true;
}

export function starterFit(recipe: StarterNeeds, machine: SetupMachine | null): StarterFit {
	if (machine === null) return { tone: 'dim', text: "Couldn't check your card" };
	if (!hasLocalCard(machine)) return { tone: 'low', text: 'Needs an NVIDIA GPU' };
	if (starterFloorGb(recipe) == null) return { tone: 'dim', text: 'Not yet measured on smaller cards' };
	if (vramGb(machine) == null) return { tone: 'dim', text: "Couldn't read your card's memory" };
	const shortfall = starterShortfall(recipe, machine);
	if (shortfall) return { tone: 'low', text: shortfallSentence(shortfall) };
	return { tone: 'good', text: 'Fits your card' };
}

export function starterModelName(recipe: Pick<SetupRecipe, 'name' | 'presets'>): string {
	return recipe.presets[0]?.name ?? recipe.name.replace(/\s+Starter$/i, '');
}

export function starterSourceText(recipe: Pick<SetupRecipe, 'sources'>): string {
	return recipe.sources.length > 0 ? recipe.sources.join(', ') : 'Not stated';
}

export function reviewDownloadLabel(recipe: Pick<SetupRecipe, 'total_download_bytes'> | null): string {
	if (recipe?.total_download_bytes == null) return 'Review download';
	return `Review ${formatBytes(recipe.total_download_bytes)} download`;
}

export type VerdictKind = 'unknown' | 'no-gpu' | 'low-memory' | 'sdxl' | 'good';

export interface MachineVerdict {
	kind: VerdictKind;
	title: string;
	body: string;
}

export const MIN_SUPPORTED_VRAM_GB = 8;
export const FULL_FIT_VRAM_GB = 16;

export function machineVerdict(machine: SetupMachine | null): MachineVerdict {
	if (machine === null) {
		return {
			kind: 'unknown',
			title: "Couldn't read this computer.",
			body: 'You can continue anyway. Every model shows what it needs so you can judge for yourself.'
		};
	}
	if (!hasLocalCard(machine)) {
		return {
			kind: 'no-gpu',
			title: 'Local generation needs an NVIDIA graphics card.',
			body: 'GTX 10-series and older cards cannot run PotionUI. You can still finish setup, then connect another computer with an NVIDIA card, a ComfyUI server, or an online service from Admin → Backends.'
		};
	}
	const vram = vramGb(machine);
	if (vram == null) {
		return {
			kind: 'unknown',
			title: "Couldn't read your card's memory.",
			body: 'You can continue anyway. Every model shows what it needs so you can judge for yourself.'
		};
	}
	if (vram < MIN_SUPPORTED_VRAM_GB) {
		return {
			kind: 'low-memory',
			title: `Below the ${MIN_SUPPORTED_VRAM_GB} GB minimum.`,
			body: `PotionUI supports cards with ${MIN_SUPPORTED_VRAM_GB} GB of graphics memory or more. With ${vram} GB most models will not fit, but you can continue.`
		};
	}
	if (vram < FULL_FIT_VRAM_GB) {
		return {
			kind: 'sdxl',
			title: 'Good for SDXL.',
			body: `With ${vram} GB, SDXL is the supported choice. Larger models are listed with a fit note on the next steps.`
		};
	}
	return { kind: 'good', title: 'Good fit.', body: 'Your card has room for every starter we offer.' };
}

export type RowTone = 'success' | 'warning' | 'neutral';

export interface RowStatus {
	tone: RowTone;
	text: string;
}

export function memoryStatus(gb: number): RowStatus {
	if (gb >= 32) return { tone: 'success', text: 'Plenty' };
	if (gb >= 16) return { tone: 'neutral', text: 'Enough' };
	return { tone: 'neutral', text: 'Low' };
}

const DEFAULT_LARGEST_STARTER_BYTES = 28 * BYTES_PER_GIB;
const DISK_HEADROOM = 1.25;

export function diskStatus(freeBytes: number, recipes: SetupRecipe[]): RowStatus {
	const sizes = firstRunStarters(recipes)
		.map((r) => r.total_download_bytes)
		.filter((n): n is number => n != null);
	const largest = sizes.length > 0 ? Math.max(...sizes) : DEFAULT_LARGEST_STARTER_BYTES;
	if (freeBytes >= largest * DISK_HEADROOM) return { tone: 'success', text: 'Enough for any starter' };
	return { tone: 'warning', text: 'Tight' };
}

export interface SkipToastInput {
	mobile: boolean;
	step: SetupStepId;
	runStatus: SetupRunStatus | null;
}

export function skipToastMessage({ mobile, step, runStatus }: SkipToastInput): string {
	const where = mobile ? 'Settings, under Resume setup' : 'Resume setup';
	if (step === 'install') {
		if (runStatus === 'awaiting_consent') {
			return `Guide skipped. Nothing downloads until you approve it in ${where}.`;
		}
		if (runStatus === 'pending' || runStatus === 'running' || runStatus === 'paused') {
			return `Guide skipped. Your install keeps running, and ${where} shows its progress.`;
		}
	}
	return `Guide skipped. Pick it up again any time from ${where}.`;
}

export const SKIP_TOAST_DURATION_MS = 9000;
