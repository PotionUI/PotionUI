import { goto } from '$app/navigation';
import type { OrganizeAction, OrganizeCondition, OrganizeMatch, OrganizeSubject } from '$lib/types/organize';
import { organizeHref } from './subjects';

export interface OrganizeStartRule {
	subject: OrganizeSubject;
	name?: string;
	match?: OrganizeMatch;
	conditions: OrganizeCondition[];
	actions?: OrganizeAction[];
}

const STORAGE_KEY = 'potionui:organize-start';

let pending: OrganizeStartRule | null = null;

export function setPendingRule(rule: OrganizeStartRule): void {
	pending = rule;
	try {
		sessionStorage.setItem(STORAGE_KEY, JSON.stringify(rule));
	} catch {
		return;
	}
}

export function takePendingRule(): OrganizeStartRule | null {
	let rule = pending;
	pending = null;
	try {
		if (!rule) {
			const raw = sessionStorage.getItem(STORAGE_KEY);
			if (raw) rule = JSON.parse(raw) as OrganizeStartRule;
		}
		sessionStorage.removeItem(STORAGE_KEY);
	} catch {
		return rule;
	}
	return rule;
}

export function startRuleFrom(rule: OrganizeStartRule): Promise<void> {
	setPendingRule(rule);
	return goto(organizeHref(rule.subject, { rule: 'new' }));
}
