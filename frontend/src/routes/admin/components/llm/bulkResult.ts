export interface BulkOutcome {
	total: number;
	succeeded: number;
	failed: number;
}

export function summarizeBulkOutcome(results: readonly PromiseSettledResult<unknown>[]): BulkOutcome {
	const total = results.length;
	const failed = results.filter((result) => result.status === 'rejected').length;
	return { total, succeeded: total - failed, failed };
}

export function bulkOutcomeMessage(
	outcome: BulkOutcome,
	verb: string,
	noun: string
): { ok: boolean; text: string } {
	const plural = (n: number) => `${n} ${noun}${n === 1 ? '' : 's'}`;
	if (outcome.total === 0) return { ok: true, text: '' };
	if (outcome.failed === 0) return { ok: true, text: `${plural(outcome.total)} ${verb}` };
	if (outcome.succeeded === 0) return { ok: false, text: `${plural(outcome.total)} could not be ${verb}` };
	return { ok: false, text: `${plural(outcome.succeeded)} ${verb}, ${outcome.failed} failed` };
}
