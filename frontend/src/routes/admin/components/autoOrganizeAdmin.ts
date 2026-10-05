import type { OrganizeAdminOverview, OrganizeAdminUser } from '$lib/types/organize';

export const RULE_CAP_MAX = 1000;
export const HOURLY_LIMIT_MAX = 100000;

export interface ControlsDraft {
	defaultRuleCap: string;
	hourlyLimit: string;
}

export interface ControlsPayload {
	default_rule_cap?: number;
	hourly_limit?: number;
}

export function controlsDraftFrom(overview: Pick<OrganizeAdminOverview, 'default_rule_cap' | 'hourly_limit'>): ControlsDraft {
	return {
		defaultRuleCap: String(overview.default_rule_cap),
		hourlyLimit: String(overview.hourly_limit)
	};
}

function parseWhole(text: string): number | null {
	const trimmed = text.trim();
	if (!/^\d+$/.test(trimmed)) return null;
	return Number(trimmed);
}

export interface ControlsErrors {
	defaultRuleCap?: string;
	hourlyLimit?: string;
}

export function validateControls(draft: ControlsDraft): ControlsErrors {
	const errors: ControlsErrors = {};
	const cap = parseWhole(draft.defaultRuleCap);
	if (cap === null || cap > RULE_CAP_MAX) {
		errors.defaultRuleCap = `Enter a whole number from 0 to ${RULE_CAP_MAX}.`;
	}
	const hourly = parseWhole(draft.hourlyLimit);
	if (hourly === null || hourly < 1 || hourly > HOURLY_LIMIT_MAX) {
		errors.hourlyLimit = `Enter a whole number from 1 to ${HOURLY_LIMIT_MAX}.`;
	}
	return errors;
}

export function controlsPayload(
	overview: Pick<OrganizeAdminOverview, 'default_rule_cap' | 'hourly_limit'>,
	draft: ControlsDraft
): ControlsPayload {
	const payload: ControlsPayload = {};
	const cap = parseWhole(draft.defaultRuleCap);
	const hourly = parseWhole(draft.hourlyLimit);
	if (cap !== null && cap !== overview.default_rule_cap) payload.default_rule_cap = cap;
	if (hourly !== null && hourly !== overview.hourly_limit) payload.hourly_limit = hourly;
	return payload;
}

export function controlsDirty(
	overview: Pick<OrganizeAdminOverview, 'default_rule_cap' | 'hourly_limit'>,
	draft: ControlsDraft
): boolean {
	return Object.keys(controlsPayload(overview, draft)).length > 0;
}

export type UserCapResult = { ok: true; value: number | null } | { ok: false; message: string };

export function parseUserCap(text: string): UserCapResult {
	const trimmed = text.trim();
	if (trimmed === '') return { ok: true, value: null };
	const value = parseWhole(trimmed);
	if (value === null || value > RULE_CAP_MAX) {
		return { ok: false, message: `Enter a whole number from 0 to ${RULE_CAP_MAX}, or leave it empty for the default.` };
	}
	return { ok: true, value };
}

export function userCapText(user: Pick<OrganizeAdminUser, 'rule_cap'>): string {
	return user.rule_cap === null ? '' : String(user.rule_cap);
}

export function replaceUser(overview: OrganizeAdminOverview, updated: OrganizeAdminUser): OrganizeAdminOverview {
	return {
		...overview,
		users: overview.users.map((user) => (user.user_id === updated.user_id ? updated : user))
	};
}

export function isForbidden(error: unknown): boolean {
	const status = (error as { response?: { status?: number } } | null)?.response?.status;
	return status === 403;
}
