import { resolveStageStatus } from '$lib/components/generation-panel/stageLabels';
import { parseServerDate } from '$lib/utils/relativeTime';
import type { GenerationHistoryItem } from '$lib/types/history';
import type { LiveGeneration } from './liveState';

type Timed = Pick<GenerationHistoryItem, 'started_at' | 'created_at' | 'queued_seconds'>;

export function isLiveStatus(status: string | null | undefined): boolean {
	return status === 'pending' || status === 'running';
}

export function formatElapsed(totalSeconds: number | null | undefined): string {
	if (typeof totalSeconds !== 'number' || !Number.isFinite(totalSeconds) || totalSeconds < 0) return '00:00';
	const whole = Math.floor(totalSeconds);
	const hours = Math.floor(whole / 3600);
	const minutes = Math.floor((whole % 3600) / 60);
	const seconds = whole % 60;
	const pair = (value: number) => String(value).padStart(2, '0');
	return hours > 0 ? `${hours}:${pair(minutes)}:${pair(seconds)}` : `${pair(minutes)}:${pair(seconds)}`;
}

export function elapsedSeconds(generation: Timed, now: number): number | null {
	const start = parseServerDate(generation.started_at ?? generation.created_at);
	if (!start) return null;
	return Math.max(0, (now - start.getTime()) / 1000);
}

export function queuedSeconds(generation: Timed): number | null {
	if (typeof generation.queued_seconds === 'number' && generation.queued_seconds >= 0) return generation.queued_seconds;
	const created = parseServerDate(generation.created_at);
	const started = parseServerDate(generation.started_at);
	if (!created || !started) return null;
	return Math.max(0, (started.getTime() - created.getTime()) / 1000);
}

export interface LiveStepLine {
	text: string;
	stepCount: string | null;
	percent: number | null;
}

export function percentOf(progress: number | null | undefined): number | null {
	if (typeof progress !== 'number' || !Number.isFinite(progress)) return null;
	return Math.round(Math.min(1, Math.max(0, progress)) * 100);
}

export function liveStepLine(
	live: Pick<LiveGeneration, 'currentStep' | 'message' | 'pipeName' | 'pipeId' | 'itemIndex' | 'itemCount' | 'progress'>,
	options: { isAdmin: boolean; noun?: string; queued?: boolean }
): LiveStepLine {
	const percent = percentOf(live.progress);
	if (options.queued) return { text: 'Waiting in the queue', stepCount: null, percent };
	const stage = resolveStageStatus({
		currentStep: live.currentStep,
		message: live.message,
		pipeName: live.pipeName,
		pipeId: live.pipeId,
		isAdmin: options.isAdmin
	});
	const batch =
		live.itemCount && live.itemCount > 1 && live.itemIndex !== null
			? ` ${options.noun ?? 'image'} ${live.itemIndex + 1} of ${live.itemCount}`
			: '';
	const step = stage.stepCount ? ` · step ${stage.stepCount}` : '';
	return { text: `${stage.label}${batch}${step}`, stepCount: stage.stepCount ?? null, percent };
}

export function tileStepLabel(
	live: Pick<LiveGeneration, 'currentStep' | 'message' | 'pipeName' | 'pipeId'>,
	isAdmin: boolean
): string {
	const stage = resolveStageStatus({
		currentStep: live.currentStep,
		message: live.message,
		pipeName: live.pipeName,
		pipeId: live.pipeId,
		isAdmin
	});
	return stage.stepCount ? `${stage.label} · ${stage.stepCount}` : stage.label;
}

const BATCH_KEYS = ['quantity', 'batch_size', 'num_images', 'batch_count', 'images'];

export function batchTotal(formData: Record<string, unknown> | null | undefined, itemCount: number | null): number {
	if (itemCount && itemCount > 0) return itemCount;
	for (const key of BATCH_KEYS) {
		const value = Number(formData?.[key]);
		if (Number.isInteger(value) && value > 1) return Math.min(value, 64);
	}
	return 1;
}

export type SlotState = 'done' | 'active' | 'waiting' | 'failed';

export interface BatchSlot {
	index: number;
	state: SlotState;
}

export function batchSlots(total: number, finished: number, ended: 'failed' | 'cancelled' | null): BatchSlot[] {
	const slots: BatchSlot[] = [];
	for (let index = 0; index < total; index += 1) {
		let state: SlotState = 'waiting';
		if (index < finished) state = 'done';
		else if (index === finished) state = ended ? 'failed' : 'active';
		slots.push({ index, state });
	}
	return slots;
}
