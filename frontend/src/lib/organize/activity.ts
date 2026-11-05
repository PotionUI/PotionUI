import type { OrganizeRun } from '$lib/types/organize';

export interface OutcomeLine {
	code: string;
	label: string;
	count: number;
}

export interface RunOutcomes {
	updated: number;
	skipped: number;
	waiting: number;
	skippedLines: OutcomeLine[];
	waitingLines: OutcomeLine[];
}

export function outcomesOf(run: OrganizeRun): RunOutcomes {
	const lines = (outcome: 'skipped' | 'waiting'): OutcomeLine[] =>
		(run.reasons ?? [])
			.filter((r) => r.outcome === outcome)
			.map((r) => ({ code: r.code, label: r.detail || r.code, count: r.count }));
	return {
		updated: run.outcomes?.updated ?? run.applied,
		skipped: run.outcomes?.skipped ?? 0,
		waiting: run.outcomes?.waiting ?? 0,
		skippedLines: lines('skipped'),
		waitingLines: lines('waiting')
	};
}

export function hasOutcomes(run: OrganizeRun): boolean {
	const o = outcomesOf(run);
	return o.skipped + o.waiting > 0;
}

function noun(run: Pick<OrganizeRun, 'subject'>): string {
	return run.subject === 'model' ? 'model' : 'item';
}

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
	return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function runSummary(run: OrganizeRun, remaining = false): string[] {
	const countOf = (change: { count: number; live: number }) => (remaining ? change.live : change.count);
	const lines: string[] = [];
	for (const c of run.changes.collections) {
		lines.push(`Added ${plural(countOf(c), noun(run))} to ${c.name}${c.created ? ' (new collection)' : ''}`);
	}
	for (const g of run.changes.groups ?? []) {
		lines.push(`Gave ${g.name} access to ${plural(countOf(g), 'model')}`);
	}
	for (const t of run.changes.tags) {
		lines.push(`Tagged ${plural(countOf(t), noun(run))} "${t.name}"`);
	}
	for (const o of run.changes.other) {
		const label = o.done_label || o.label;
		lines.push(
			run.subject === 'model' ? `${label} for ${plural(countOf(o), 'model')}` : `${o.label}: ${plural(countOf(o), 'item')}`
		);
	}
	if (lines.length === 0) lines.push(run.applied === 0 ? 'Nothing needed changing' : `Changed ${plural(run.applied, 'item')}`);
	return lines;
}

export function runTitle(run: OrganizeRun): string {
	return run.rule_deleted ? `${run.rule_name} (deleted rule)` : run.rule_name;
}

export function runKindLabel(run: OrganizeRun): string {
	const what = run.subject === 'model' ? 'models' : 'items';
	return run.kind === 'backfill' ? `Existing ${what}` : `New ${what}`;
}

export function undoMessage(run: OrganizeRun): string {
	const parts = runSummary(run, true).join(', ').toLowerCase();
	return `This removes what "${runTitle(run)}" added in this run (${parts}). Anything you already removed yourself is left alone, and ${(run.changes.groups?.length ?? 0) > 0 ? 'the group' : 'the collection'} stays.`;
}
