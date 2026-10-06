import type { CollisionInfo, DisplayFact, EntityKind, Fact } from './types';

export const MAX_FACTS = 4;
export const MAX_FACTS_MOBILE = 3;

export function normalizeName(name: string): string {
	return name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

export function idTail(id: string, length = 6): string {
	return id.slice(-length);
}

function factValue<Row>(fact: Fact<Row>, row: Row): string {
	return (fact.value(row) ?? '').toString().trim();
}

export function buildCollisionIndex<Row>(
	rows: readonly Row[],
	kind: Pick<EntityKind<Row>, 'getId' | 'getName' | 'facts'>
): Map<string, CollisionInfo> {
	const groups = new Map<string, Row[]>();
	for (const row of rows) {
		const key = normalizeName(kind.getName(row));
		if (!key) continue;
		const group = groups.get(key);
		if (group) group.push(row);
		else groups.set(key, [row]);
	}
	const index = new Map<string, CollisionInfo>();
	for (const group of groups.values()) {
		if (group.length < 2) continue;
		const differing = new Set<string>();
		for (const fact of kind.facts) {
			const values = new Set(group.map((row) => factValue(fact, row)));
			if (values.size > 1) differing.add(fact.key);
		}
		const info: CollisionInfo = { size: group.length, differing, needsIdTail: differing.size === 0 };
		for (const row of group) index.set(kind.getId(row), info);
	}
	return index;
}

export function pickFacts<Row>(
	row: Row,
	kind: Pick<EntityKind<Row>, 'getId' | 'facts'>,
	collision: CollisionInfo | undefined,
	max = MAX_FACTS
): DisplayFact[] {
	const picked: DisplayFact[] = [];
	for (const fact of kind.facts) {
		const value = factValue(fact, row);
		if (!value) continue;
		const emphasis = !!collision && collision.differing.has(fact.key);
		const display: DisplayFact = { key: fact.key, label: fact.label, value, as: fact.as, emphasis };
		if (fact.base || emphasis) picked.push(display);
	}
	if (collision?.needsIdTail) {
		picked.push({ key: 'id', label: 'Id', value: `#${idTail(kind.getId(row))}`, as: 'text', emphasis: true });
	}
	if (picked.length <= max) return picked;
	const keep = picked.filter((fact) => fact.emphasis);
	const base = picked.filter((fact) => !fact.emphasis);
	return [...base.slice(0, Math.max(0, max - keep.length)), ...keep].slice(0, max).sort(
		(a, b) => picked.indexOf(a) - picked.indexOf(b)
	);
}
