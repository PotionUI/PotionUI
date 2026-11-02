import { goto } from '$app/navigation';
import { toasts } from '$lib/stores/toast';
import { rememberToolParams } from './catalog';
import { emitRunStarted } from './runEvents';
import type { AnnounceOptions, AnnounceSetting } from './announce';
import { mediaToolRuns, type MediaToolRunOutcome, type MediaToolRunsStore, type RunHandle } from './runs';
import type {
	MediaToolInputRef,
	MediaToolItem,
	MediaToolOutputMode,
	MediaToolRunSummary,
	MediaToolSource,
	MediaToolStep
} from './types';

export interface ExecuteOptions {
	label: string;
	steps: MediaToolStep[];
	sources: readonly MediaToolSource[];
	refs: MediaToolInputRef[];
	mode: MediaToolOutputMode;
	announce?: AnnounceSetting;
	runs?: MediaToolRunsStore;
}

function nameOf(item: MediaToolItem | undefined): string {
	return item ? (item.original_filename ?? item.filename) : 'The result';
}

export function announceStart(label: string, mode: MediaToolOutputMode) {
	toasts.show('info', `${label} · ${mode === 'replace' ? 'Replaces the original.' : 'Saves as a new item.'}`, {
		title: 'Edit started'
	});
}

export function summaryOf(outcome: MediaToolRunOutcome): MediaToolRunSummary | null {
	return outcome.status === 'completed' && outcome.items.length === 0 && outcome.summary ? outcome.summary : null;
}

export function announceSummary(summary: MediaToolRunSummary) {
	const link = summary.links[0];
	toasts.show('success', summary.message, {
		title: 'Done',
		action: link ? { label: link.label, onClick: () => void goto(link.href) } : undefined
	});
}

export function announceOutcome(outcome: MediaToolRunOutcome, options: AnnounceOptions = {}, quietSuccess = false) {
	if (outcome.status === 'completed') {
		const summary = summaryOf(outcome);
		if (summary) {
			announceSummary(summary);
			return;
		}
		if (quietSuccess) return;
		if (outcome.replaced) {
			toasts.show('success', `${nameOf(outcome.items[0])} was updated.`, { title: 'Original replaced' });
			return;
		}
		const message =
			outcome.items.length === 1
				? `${nameOf(outcome.items[0])} is in your library.`
				: `${outcome.items.length} items are in your library.`;
		toasts.show('success', message, {
			title: options.title ?? 'Saved as new',
			action: options.action
				? { label: options.action.label, onClick: () => void goto(options.action!.href) }
				: undefined
		});
	} else if (outcome.status === 'failed') {
		toasts.show(
			'error',
			options.retry ? `${options.label ?? 'The edit'} failed. Your settings are kept.` : outcome.message,
			{
				title: options.retry ? outcome.message : 'Edit failed',
				action: options.retry ? { label: 'Try again', onClick: options.retry } : undefined
			}
		);
	} else {
		toasts.show('info', 'Nothing was saved.', { title: 'Edit cancelled' });
	}
}

export async function executeMediaTool(options: ExecuteOptions): Promise<RunHandle> {
	const runs = options.runs ?? mediaToolRuns;
	const handle = await runs.start({
		keys: options.sources.map((source) => source.id),
		toolId: options.steps[0].tool,
		label: options.label,
		body: {
			steps: options.steps,
			inputs: options.refs,
			output: { mode: options.mode }
		}
	});
	const setting = options.announce ?? false;
	const quiet = typeof setting === 'object' && setting.quietSuccess === true;
	const full = setting !== false && setting !== 'failures' && !quiet;
	if (full) announceStart(options.label, options.mode);

	const done = handle.done.then((outcome) => {
		if (outcome.status === 'completed' && options.steps.length === 1) {
			rememberToolParams(options.steps[0].tool, options.steps[0].params);
		}
		if (outcome.status === 'failed') rememberToolParams(options.steps[0].tool, options.steps[0].params);
		if (setting !== false) {
			announceOutcome(outcome, typeof setting === 'object' ? { ...setting, label: options.label } : {}, !full);
		} else {
			const summary = summaryOf(outcome);
			if (summary) announceSummary(summary);
		}
		return outcome;
	});
	const started = { runId: handle.runId, done };
	emitRunStarted(started);
	return started;
}
