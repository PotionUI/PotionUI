import type { OrganizeRun } from '$lib/types/organize';

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
	return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function runSummary(run: OrganizeRun): string[] {
	const lines: string[] = [];
	for (const c of run.changes.collections) {
		lines.push(`Added ${plural(c.count, 'item')} to ${c.name}${c.created ? ' (new collection)' : ''}`);
	}
	for (const t of run.changes.tags) {
		lines.push(`Tagged ${plural(t.count, 'item')} "${t.name}"`);
	}
	for (const o of run.changes.other) {
		lines.push(`${o.label}: ${plural(o.count, 'item')}`);
	}
	if (lines.length === 0) lines.push(run.applied === 0 ? 'Nothing needed changing' : `Changed ${plural(run.applied, 'item')}`);
	return lines;
}

export function runTitle(run: OrganizeRun): string {
	return run.rule_deleted ? `${run.rule_name} (deleted rule)` : run.rule_name;
}

export function runKindLabel(run: OrganizeRun): string {
	return run.kind === 'backfill' ? 'Existing items' : 'New items';
}

export function undoMessage(run: OrganizeRun): string {
	const parts = runSummary(run).join(', ').toLowerCase();
	return `This removes what "${runTitle(run)}" added in this run (${parts}). Anything you already removed yourself is left alone, and the collection stays.`;
}
