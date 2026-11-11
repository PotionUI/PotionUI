import { describe, it, expect } from 'vitest';
import {
	adminBlockedByExpiry,
	allowedDays,
	buildCreateRequest,
	buildUpdateRequest,
	canSubmit,
	expiryNote,
	expiryOptions,
	hasChanges,
	initialDraft,
	initialSecretState,
	mcpRow,
	scopeRows,
	secretReducer,
	setScope
} from './apiKeyDialog';
import { LIMITS, makeKey, NOW } from './fixtures';

const user = { isAdmin: false, limits: LIMITS };
const admin = { isAdmin: true, limits: { ...LIMITS, scopes_allowed: [...LIMITS.scopes_allowed, 'admin' as const] } };
const adminLocked = { isAdmin: true, limits: LIMITS };

describe('expiry options', () => {
	it('offers Never, 30 days, 90 days and 1 year, with Never preselected', () => {
		const draft = initialDraft(user);
		expect(expiryOptions(draft, user).map((o) => o.label)).toEqual(['Never', '30 days', '90 days', '1 year']);
		expect(draft.days).toBeNull();
	});

	it('hides options past the instance limit, and Never with them', () => {
		const ctx = { isAdmin: false, limits: { ...LIMITS, max_lifetime_days: 90 } };
		const draft = initialDraft(ctx);
		expect(expiryOptions(draft, ctx).map((o) => o.label)).toEqual(['30 days', '90 days']);
		expect(draft.days).toBe(90);
	});

	it('disables Never and 1 year and picks 90 days when administration is ticked', () => {
		const next = setScope(initialDraft(admin), 'admin', true, admin);
		const options = expiryOptions(next, admin);
		expect(options.filter((o) => o.disabled).map((o) => o.label)).toEqual(['Never', '1 year']);
		expect(next.days).toBe(90);
	});

	it('keeps a still-valid choice and releases the cap again', () => {
		const picked = { ...initialDraft(admin), days: 30 as const };
		const on = setScope(picked, 'admin', true, admin);
		expect(on.days).toBe(30);
		const off = setScope(on, 'admin', false, admin);
		expect(expiryOptions(off, admin).some((o) => o.disabled)).toBe(false);
	});

	it('does not cap anything when the ceiling forbids administration', () => {
		const draft = { ...initialDraft(adminLocked), scopes: { ...initialDraft(adminLocked).scopes, admin: true } };
		expect(expiryOptions(draft, adminLocked).some((o) => o.disabled)).toBe(false);
		expect(allowedDays(draft, adminLocked)).toBeNull();
	});

	it('words the note for a date, for no date, and for the admin cap', () => {
		const draft = initialDraft(user);
		expect(expiryNote(draft, user, NOW)).toBeNull();
		expect(expiryNote({ ...draft, days: 30 }, user, NOW)).toEqual({ text: 'Stops working Dec 11.', tone: 'subtle' });
		const capped = setScope(initialDraft(admin), 'admin', true, admin);
		expect(expiryNote(capped, admin, NOW)).toEqual({ text: 'Full administration keys last 90 days at most.', tone: 'warning' });
	});
});

describe('scope rows', () => {
	it('shows no administration row to an ordinary user', () => {
		expect(scopeRows(initialDraft(user), user).map((r) => r.scope)).toEqual(['read', 'generate', 'content']);
	});

	it('shows an administrator the administration row', () => {
		const rows = scopeRows(initialDraft(admin), admin);
		expect(rows.map((r) => r.scope)).toEqual(['read', 'generate', 'content', 'admin']);
		expect(rows[3]).toMatchObject({ disabled: false, lock: null });
	});

	it('locks the administration row with the reason when the instance forbids it', () => {
		const row = scopeRows(initialDraft(adminLocked), adminLocked)[3];
		expect(row).toMatchObject({ scope: 'admin', on: false, disabled: true, lock: 'ceiling', lockNote: 'Not allowed on this instance.' });
	});

	it('marks the other three as included and locked while administration is ticked', () => {
		const on = setScope(initialDraft(admin), 'admin', true, admin);
		const rows = scopeRows(on, admin);
		expect(rows.slice(0, 3).every((r) => r.on && r.disabled && r.description === 'Included with full administration.')).toBe(true);
	});

	it('locks scopes the instance does not allow', () => {
		const ctx = { isAdmin: false, limits: { ...LIMITS, scopes_allowed: ['read' as const, 'mcp' as const] } };
		const rows = scopeRows(initialDraft(ctx), ctx);
		expect(rows[1]).toMatchObject({ scope: 'generate', disabled: true, lock: 'ceiling' });
	});

	it('will not offer full administration to a key that lasts past 90 days', () => {
		const row = scopeRows(initialDraft(admin), admin, { adminBlockedByExpiry: true })[3];
		expect(row).toMatchObject({ disabled: true, lock: 'needs_expiry' });
	});

	it('turns MCP off with the reason when the administrator switched it off', () => {
		const ctx = { isAdmin: false, limits: { ...LIMITS, mcp_enabled: false } };
		expect(mcpRow(initialDraft(ctx), ctx)).toMatchObject({ on: false, disabled: true, lockNote: 'Turned off by your administrator.' });
		expect(initialDraft(ctx).scopes.mcp).toBe(false);
	});

	it('defaults MCP on when it is available', () => {
		expect(initialDraft(user).scopes.mcp).toBe(true);
		expect(mcpRow(initialDraft(user), user)).toMatchObject({ on: true, disabled: false });
	});
});

describe('requests', () => {
	it('needs a name and at least one scope', () => {
		const draft = initialDraft(user);
		expect(canSubmit(draft, user)).toBe(false);
		expect(canSubmit({ ...draft, name: '  ' }, user)).toBe(false);
		expect(canSubmit({ ...draft, name: 'Batch' }, user)).toBe(true);
		const none = { ...draft, name: 'Batch', scopes: { ...draft.scopes, read: false, mcp: false } };
		expect(canSubmit(none, user)).toBe(false);
	});

	it('builds a create request with trimmed name, ordered scopes and null expiry for Never', () => {
		const draft = { ...initialDraft(user), name: ' Batch script ' };
		expect(buildCreateRequest(draft, user)).toEqual({ name: 'Batch script', scopes: ['read', 'mcp'], expires_in_days: null });
	});

	it('sends every included scope with administration and the 90 day expiry', () => {
		const draft = { ...setScope(initialDraft(admin), 'admin', true, admin), name: 'Ops' };
		expect(buildCreateRequest(draft, admin)).toEqual({ name: 'Ops', scopes: ['read', 'generate', 'content', 'admin', 'mcp'], expires_in_days: 90 });
	});

	it('never asks for MCP when it is off', () => {
		const ctx = { isAdmin: false, limits: { ...LIMITS, mcp_enabled: false } };
		const draft = { ...initialDraft(ctx), name: 'x' };
		expect(buildCreateRequest(draft, ctx).scopes).toEqual(['read']);
	});

	it('builds an update with only the changed fields', () => {
		const key = makeKey({ scopes: ['read', 'mcp'] });
		const same = { name: 'Claude Desktop', scopes: { read: true, generate: false, content: false, admin: false, mcp: true }, days: null };
		expect(hasChanges(buildUpdateRequest(key, same, user))).toBe(false);
		expect(buildUpdateRequest(key, { ...same, name: 'Renamed' }, user)).toEqual({ name: 'Renamed' });
		expect(buildUpdateRequest(key, { ...same, scopes: { ...same.scopes, generate: true } }, user)).toEqual({ scopes: ['read', 'generate', 'mcp'] });
	});
});

describe('secret dialog state machine', () => {
	it('closes at once after a copy', () => {
		const copied = secretReducer(initialSecretState, 'copy');
		expect(secretReducer(copied, 'requestClose').stage).toBe('closed');
	});

	it('asks before closing when nothing was copied', () => {
		expect(secretReducer(initialSecretState, 'requestClose').stage).toBe('guarding');
	});

	it('keeps the secret open or closes anyway from the guard', () => {
		const guarding = secretReducer(initialSecretState, 'requestClose');
		expect(secretReducer(guarding, 'keepOpen').stage).toBe('showing');
		expect(secretReducer(guarding, 'closeAnyway').stage).toBe('closed');
	});

	it('closes without asking when the person says they saved it', () => {
		expect(secretReducer(initialSecretState, 'done').stage).toBe('closed');
	});
});

describe('adminBlockedByExpiry', () => {
	it('blocks never and long keys, allows keys expiring within 90 days', () => {
		expect(adminBlockedByExpiry(null, NOW)).toBe(true);
		expect(adminBlockedByExpiry('2027-06-01T00:00:00Z', NOW)).toBe(true);
		expect(adminBlockedByExpiry('2026-12-01T00:00:00Z', NOW)).toBe(false);
	});
});
