import type { ApplyResult, PickerDiff } from './types';

export interface DiffHandlers {
	assign: (id: string) => Promise<unknown>;
	unassign: (id: string) => Promise<unknown>;
}

function messageOf(error: unknown): string {
	if (error instanceof Error && error.message) return error.message;
	if (typeof error === 'string' && error) return error;
	return 'Request failed';
}

export async function applyDiff(diff: PickerDiff, handlers: DiffHandlers, concurrency = 4): Promise<ApplyResult> {
	const jobs: Array<{ id: string; run: () => Promise<unknown> }> = [
		...diff.remove.map((id) => ({ id, run: () => handlers.unassign(id) })),
		...diff.add.map((id) => ({ id, run: () => handlers.assign(id) }))
	];
	const result: ApplyResult = { ok: [], failed: [] };
	for (let i = 0; i < jobs.length; i += concurrency) {
		const batch = jobs.slice(i, i + concurrency);
		const settled = await Promise.allSettled(batch.map((job) => job.run()));
		settled.forEach((outcome, index) => {
			const id = batch[index].id;
			if (outcome.status === 'fulfilled') result.ok.push(id);
			else result.failed.push({ id, message: messageOf(outcome.reason) });
		});
	}
	return result;
}

export function failureSummary(result: ApplyResult, noun: string): string {
	if (result.failed.length === 0) return '';
	const total = result.ok.length + result.failed.length;
	return `${result.failed.length} of ${total} changes failed. The other ${noun} changes were applied.`;
}
