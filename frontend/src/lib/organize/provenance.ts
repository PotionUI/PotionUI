import type { OrganizeProvenance } from '$lib/types/organize';

export interface ProvenanceGroup {
	ruleId: string;
	ruleName: string;
	deleted: boolean;
	targets: string[];
}

export function groupProvenance(rows: OrganizeProvenance[]): ProvenanceGroup[] {
	const groups = new Map<string, ProvenanceGroup>();
	for (const row of rows) {
		let group = groups.get(row.rule_id);
		if (!group) {
			group = { ruleId: row.rule_id, ruleName: row.rule_name, deleted: row.rule_deleted, targets: [] };
			groups.set(row.rule_id, group);
		}
		if (row.target_name && !group.targets.includes(row.target_name)) group.targets.push(row.target_name);
	}
	return [...groups.values()];
}
