import type { OrganizeRule } from '$lib/types/organize';

export type StatusTone = 'neutral' | 'warning' | 'danger' | 'success';

export interface RuleStatusInfo {
	label: string | null;
	tone: StatusTone;
	notice: string | null;
}

const PAUSE_NOTICES: Record<string, string> = {
	rate_limited: 'It filed a lot of items in an hour, so it was paused. Check it and switch it back on.',
	collection_missing: 'Its collection is gone, so it was paused. Pick a collection and switch it back on.',
	model_restricted: 'It uses a model you can no longer use, so it was paused. Change the model and switch it back on.',
	admin_paused: 'Auto-organize is paused by an administrator.'
};

export function ruleStatusInfo(rule: Pick<OrganizeRule, 'status' | 'paused_reason' | 'issues'>): RuleStatusInfo {
	if (rule.status === 'paused') {
		return {
			label: 'Paused',
			tone: 'warning',
			notice: (rule.paused_reason && PAUSE_NOTICES[rule.paused_reason]) || 'It was paused. Check it and switch it back on.'
		};
	}
	if (rule.status === 'needs_attention') {
		const blocking = rule.issues.find((issue) => issue.blocking);
		return {
			label: 'Needs attention',
			tone: 'warning',
			notice: blocking?.message ?? rule.issues[0]?.message ?? 'Something this rule needs is missing.'
		};
	}
	if (rule.status === 'off') return { label: null, tone: 'neutral', notice: null };
	const warning = rule.issues[0];
	return { label: null, tone: 'neutral', notice: warning?.message ?? null };
}

export function attentionCount(rules: Pick<OrganizeRule, 'status'>[]): number {
	return rules.filter((r) => r.status === 'paused' || r.status === 'needs_attention').length;
}
