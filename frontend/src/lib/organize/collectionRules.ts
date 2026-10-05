import { organizeHref, ORGANIZE_SUBJECTS } from './subjects';
import type { OrganizeCollectionRuleRef, OrganizeCollectionScope } from '$lib/types/organize';

export function filledByLabel(count: number): string {
	return count === 1 ? 'Filled by 1 rule' : `Filled by ${count} rules`;
}

export function collectionRulesHref(
	scope: OrganizeCollectionScope,
	rules: readonly OrganizeCollectionRuleRef[]
): string {
	const subject = ORGANIZE_SUBJECTS.find((s) => s.scope === scope)?.subject ?? 'generation';
	return organizeHref(subject, rules.length === 1 ? { rule: rules[0].id } : {});
}
