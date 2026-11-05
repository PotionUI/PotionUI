import type { OrganizeAction, OrganizeCatalog, OrganizeCondition, OrganizeMatch, OrganizeSubject } from '$lib/types/organize';
import { attributeLabel, describeValue, effectiveKind, operatorLabel, type ValueLabels } from './draft';

export function subjectNoun(subject: OrganizeSubject): { singular: string; plural: string } {
	if (subject === 'upload') return { singular: 'upload', plural: 'uploads' };
	if (subject === 'model') return { singular: 'model', plural: 'models' };
	return { singular: 'generation', plural: 'generations' };
}

export function awaitsTagging(conditions: { fact: string }[], catalog: OrganizeCatalog | null): boolean {
	return conditions.some((c) => {
		const triggers = catalog?.facts.find((f) => f.key === c.fact)?.triggers;
		return !!triggers && !triggers.includes('item_created');
	});
}

export function awaitsDigest(actions: { action: string }[], catalog: OrganizeCatalog | null): boolean {
	return actions.some((a) => catalog?.actions.find((s) => s.key === a.action)?.needs?.includes('digest'));
}

export function triggerSentence(subject: OrganizeSubject, catalog: OrganizeCatalog | null): string {
	return (
		catalog?.subjects.find((s) => s.key === subject)?.trigger_label ??
		(subject === 'upload' ? 'When I upload a file' : subject === 'model' ? 'When a model is added' : 'When a new generation completes')
	);
}

export function conditionPhrases(
	conditions: OrganizeCondition[],
	catalog: OrganizeCatalog | null,
	labels: ValueLabels
): string[] {
	return conditions.map((cond) => {
		const spec = catalog?.facts.find((f) => f.key === cond.fact);
		const value = describeValue(spec, cond.value, labels);
		if (effectiveKind(spec) === 'attribute') {
			return `${spec?.label ?? cond.fact} ${attributeLabel(cond.value)} ${operatorLabel(catalog, cond.operator)} ${value}`.trim();
		}
		const phrase = `${spec?.label ?? cond.fact} ${operatorLabel(catalog, cond.operator)} ${value}`.trim();
		return cond.ignore_separators ? `${phrase} (ignoring dashes and underscores)` : phrase;
	});
}

export function actionPhrases(
	actions: OrganizeAction[],
	catalog: OrganizeCatalog | null,
	collectionNames: Record<string, string> = {},
	groupNames: Record<string, string> = {}
): string[] {
	return actions.map((a) => {
		if (a.action === 'add_to_collection') {
			const id = a.config.collection_id as string | undefined;
			const name = (id && collectionNames[id]) || (a.config.collection_name as string) || 'a collection';
			return `add to ${name}`;
		}
		if (a.action === 'add_tags') {
			const tags = (a.config.tags as string[]) ?? [];
			return `add ${tags.length === 1 ? 'tag' : 'tags'} ${tags.join(', ')}`;
		}
		if (a.action === 'assign_model_group') {
			const id = a.config.group_id as string | undefined;
			return `give ${(id && groupNames[id]) || 'a user group'} access`;
		}
		if (a.action === 'add_model_tags') {
			const tags = (a.config.tags as string[]) ?? [];
			return `add ${tags.length === 1 ? 'tag' : 'tags'} ${tags.join(', ')}`;
		}
		if (a.action === 'set_model_type') {
			return `set the type to ${(a.config.model_type as string) || 'a type'}`;
		}
		const label = catalog?.actions.find((s) => s.key === a.action)?.label ?? a.action;
		return label.charAt(0).toLowerCase() + label.slice(1);
	});
}

function groupNamesOf(groups: { id: string | null; name: string }[] | undefined): Record<string, string> {
	const out: Record<string, string> = {};
	for (const g of groups ?? []) if (g.id && g.name) out[g.id] = g.name;
	return out;
}

export function ruleSentence(
	rule: {
		match: OrganizeMatch;
		conditions: OrganizeCondition[];
		actions: OrganizeAction[];
		subject: OrganizeSubject;
		targets?: { groups?: { id: string | null; name: string }[] };
	},
	catalog: OrganizeCatalog | null,
	labels: ValueLabels,
	collectionNames: Record<string, string> = {}
): { when: string; conditions: string[]; join: string; actions: string[] } {
	return {
		when: triggerSentence(rule.subject, catalog),
		conditions: conditionPhrases(rule.conditions, catalog, labels),
		join: rule.match === 'any' ? 'or' : 'and',
		actions: actionPhrases(rule.actions, catalog, collectionNames, groupNamesOf(rule.targets?.groups))
	};
}
