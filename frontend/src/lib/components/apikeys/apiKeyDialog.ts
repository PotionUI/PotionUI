import type { ApiKey, ApiKeyCreateRequest, ApiKeyExpiryDays, ApiKeyLimits, ApiKeyScope, ApiKeyUpdateRequest } from '$lib/services/api/apiKeys';
import { parseServerDate } from '$lib/utils/relativeTime';
import { formatKeyDate } from './apiKeyView';

export const ADMIN_MAX_DAYS = 90;
export const SCOPE_ORDER: readonly ApiKeyScope[] = ['read', 'generate', 'content', 'admin', 'mcp'];
export const INCLUDED_WITH_ADMIN: readonly ApiKeyScope[] = ['read', 'generate', 'content'];

export interface ScopeDef {
	id: ApiKeyScope;
	label: string;
	description: string;
}

export const SCOPE_DEFS: Record<ApiKeyScope, ScopeDef> = {
	read: { id: 'read', label: 'Can read', description: 'Gallery, prompts, library, presets.' },
	generate: { id: 'generate', label: 'Can generate', description: 'Start and cancel generations.' },
	content: { id: 'content', label: 'Can change and delete your content', description: 'Edit and delete your gallery, prompts and library.' },
	admin: { id: 'admin', label: 'Full administration', description: 'Users, settings, plugins and backups.' },
	mcp: { id: 'mcp', label: 'Use with MCP clients', description: 'Lets an MCP client connect with this key.' }
};

export interface ExpiryOption {
	days: ApiKeyExpiryDays;
	label: string;
	disabled: boolean;
}

const EXPIRY_CHOICES: readonly { days: ApiKeyExpiryDays; label: string }[] = [
	{ days: null, label: 'Never' },
	{ days: 30, label: '30 days' },
	{ days: 90, label: '90 days' },
	{ days: 365, label: '1 year' }
];

export interface DialogContext {
	isAdmin: boolean;
	limits: ApiKeyLimits;
}

export interface KeyDraft {
	name: string;
	scopes: Record<ApiKeyScope, boolean>;
	days: ApiKeyExpiryDays;
}

export type ScopeLock = 'ceiling' | 'mcp_off' | 'needs_expiry' | null;

export interface ScopeRowState {
	scope: ApiKeyScope;
	on: boolean;
	disabled: boolean;
	description: string;
	lock: ScopeLock;
	lockNote: string | null;
}

export function adminAllowed(ctx: DialogContext): boolean {
	return ctx.isAdmin && ctx.limits.scopes_allowed.includes('admin');
}

export function mcpAllowed(ctx: DialogContext): boolean {
	return ctx.limits.mcp_enabled && ctx.limits.scopes_allowed.includes('mcp');
}

export function adminTicked(draft: KeyDraft, ctx: DialogContext): boolean {
	return adminAllowed(ctx) && draft.scopes.admin;
}

export function expiryOptions(draft: KeyDraft, ctx: DialogContext): ExpiryOption[] {
	const max = ctx.limits.max_lifetime_days;
	const capped = adminTicked(draft, ctx);
	return EXPIRY_CHOICES.filter((choice) => max === null || (choice.days !== null && choice.days <= max)).map((choice) => ({
		...choice,
		disabled: capped && (choice.days === null || choice.days > ADMIN_MAX_DAYS)
	}));
}

export function allowedDays(draft: KeyDraft, ctx: DialogContext): ApiKeyExpiryDays {
	const options = expiryOptions(draft, ctx).filter((option) => !option.disabled);
	if (options.some((option) => option.days === draft.days)) return draft.days;
	const fallback = options.find((option) => option.days === ADMIN_MAX_DAYS) ?? options[options.length - 1];
	return fallback ? fallback.days : draft.days;
}

export function expiryNote(draft: KeyDraft, ctx: DialogContext, now: Date = new Date()): { text: string; tone: 'warning' | 'subtle' } | null {
	if (adminTicked(draft, ctx)) return { text: 'Full administration keys last 90 days at most.', tone: 'warning' };
	const days = allowedDays(draft, ctx);
	if (days === null) return null;
	const stops = new Date(now.getTime() + days * 86_400_000).toISOString();
	return { text: `Stops working ${formatKeyDate(stops, now)}.`, tone: 'subtle' };
}

export function initialDraft(ctx: DialogContext): KeyDraft {
	const draft: KeyDraft = {
		name: '',
		scopes: { read: true, generate: false, content: false, admin: false, mcp: mcpAllowed(ctx) },
		days: null
	};
	return { ...draft, days: allowedDays(draft, ctx) };
}

export function draftFromKey(key: ApiKey): KeyDraft {
	return {
		name: key.name,
		scopes: {
			read: key.scopes.includes('read'),
			generate: key.scopes.includes('generate'),
			content: key.scopes.includes('content'),
			admin: key.scopes.includes('admin'),
			mcp: key.scopes.includes('mcp')
		},
		days: null
	};
}

export function setScope(draft: KeyDraft, scope: ApiKeyScope, on: boolean, ctx: DialogContext): KeyDraft {
	const next: KeyDraft = { ...draft, scopes: { ...draft.scopes, [scope]: on } };
	return { ...next, days: allowedDays(next, ctx) };
}

export function setDays(draft: KeyDraft, days: ApiKeyExpiryDays): KeyDraft {
	return { ...draft, days };
}

export function scopeRows(draft: KeyDraft, ctx: DialogContext, opts: { adminBlockedByExpiry?: boolean } = {}): ScopeRowState[] {
	const adminOn = adminTicked(draft, ctx);
	const rows: ScopeRowState[] = [];
	for (const scope of ['read', 'generate', 'content'] as const) {
		const outside = !ctx.limits.scopes_allowed.includes(scope);
		if (adminOn) {
			rows.push({ scope, on: true, disabled: true, description: 'Included with full administration.', lock: null, lockNote: null });
		} else if (outside && scope !== 'read') {
			rows.push({ scope, on: false, disabled: true, description: SCOPE_DEFS[scope].description, lock: 'ceiling', lockNote: 'Not allowed on this instance.' });
		} else {
			rows.push({ scope, on: draft.scopes[scope], disabled: false, description: SCOPE_DEFS[scope].description, lock: null, lockNote: null });
		}
	}
	if (ctx.isAdmin) {
		if (!ctx.limits.scopes_allowed.includes('admin')) {
			rows.push({ scope: 'admin', on: false, disabled: true, description: SCOPE_DEFS.admin.description, lock: 'ceiling', lockNote: 'Not allowed on this instance.' });
		} else if (opts.adminBlockedByExpiry && !draft.scopes.admin) {
			rows.push({ scope: 'admin', on: false, disabled: true, description: SCOPE_DEFS.admin.description, lock: 'needs_expiry', lockNote: 'Keys that last longer than 90 days can’t get full administration. Create a new key instead.' });
		} else {
			rows.push({ scope: 'admin', on: draft.scopes.admin, disabled: false, description: SCOPE_DEFS.admin.description, lock: null, lockNote: null });
		}
	}
	return rows;
}

export function mcpRow(draft: KeyDraft, ctx: DialogContext): ScopeRowState {
	if (!mcpAllowed(ctx)) {
		return { scope: 'mcp', on: false, disabled: true, description: SCOPE_DEFS.mcp.description, lock: 'mcp_off', lockNote: 'Turned off by your administrator.' };
	}
	return { scope: 'mcp', on: draft.scopes.mcp, disabled: false, description: SCOPE_DEFS.mcp.description, lock: null, lockNote: null };
}

export function requestedScopes(draft: KeyDraft, ctx: DialogContext): ApiKeyScope[] {
	const adminOn = adminTicked(draft, ctx);
	return SCOPE_ORDER.filter((scope) => {
		if (scope === 'mcp') return draft.scopes.mcp && mcpAllowed(ctx);
		if (scope === 'admin') return adminOn;
		if (adminOn && INCLUDED_WITH_ADMIN.includes(scope)) return true;
		return draft.scopes[scope];
	});
}

export function canSubmit(draft: KeyDraft, ctx: DialogContext): boolean {
	return draft.name.trim().length > 0 && requestedScopes(draft, ctx).length > 0;
}

export function buildCreateRequest(draft: KeyDraft, ctx: DialogContext): ApiKeyCreateRequest {
	return {
		name: draft.name.trim(),
		scopes: requestedScopes(draft, ctx),
		expires_in_days: allowedDays(draft, ctx)
	};
}

export function buildUpdateRequest(key: ApiKey, draft: KeyDraft, ctx: DialogContext): ApiKeyUpdateRequest {
	const request: ApiKeyUpdateRequest = {};
	const name = draft.name.trim();
	if (name !== key.name) request.name = name;
	const scopes = requestedScopes(draft, ctx);
	const same = scopes.length === key.scopes.length && scopes.every((scope) => key.scopes.includes(scope));
	if (!same) request.scopes = scopes;
	return request;
}

export function hasChanges(request: ApiKeyUpdateRequest): boolean {
	return request.name !== undefined || request.scopes !== undefined;
}

export type SecretStage = 'showing' | 'guarding' | 'closed';

export interface SecretState {
	stage: SecretStage;
	copied: boolean;
}

export const initialSecretState: SecretState = { stage: 'showing', copied: false };

export type SecretEvent = 'copy' | 'requestClose' | 'keepOpen' | 'closeAnyway' | 'done';

export function secretReducer(state: SecretState, event: SecretEvent): SecretState {
	switch (event) {
		case 'copy':
			return { ...state, copied: true };
		case 'requestClose':
			return state.copied ? { ...state, stage: 'closed' } : { ...state, stage: 'guarding' };
		case 'keepOpen':
			return { ...state, stage: 'showing' };
		case 'closeAnyway':
			return { ...state, stage: 'closed' };
		case 'done':
			return { ...state, stage: 'closed' };
	}
}

export function adminBlockedByExpiry(expiresAt: string | null, now: Date = new Date()): boolean {
	if (!expiresAt) return true;
	const at = parseServerDate(expiresAt)?.getTime();
	return at === undefined || at - now.getTime() > ADMIN_MAX_DAYS * 86_400_000;
}
