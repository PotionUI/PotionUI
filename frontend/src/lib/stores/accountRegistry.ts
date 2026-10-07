export const ACCOUNTS_STORAGE_KEY = 'potionui_accounts';
export const MAX_ACCOUNTS = 5;
export const EXPIRED_PRUNE_MS = 7 * 24 * 60 * 60 * 1000;

export interface StoredAccount {
	userId: string;
	username: string;
	role: 'USER' | 'ADMIN';
	avatar: string | null;
	token: string;
	signedInAt: number;
	expired: boolean;
	expiredAt?: number;
}

export interface AccountRegistry {
	v: 1;
	activeId: string | null;
	accounts: StoredAccount[];
}

export interface AccountProfile {
	userId: string;
	username: string;
	role: 'USER' | 'ADMIN';
	avatar: string | null;
}

export type UpsertOutcome = 'added' | 'replaced' | 'full';

export function emptyRegistry(): AccountRegistry {
	return { v: 1, activeId: null, accounts: [] };
}

export function jwtExpiryMs(token: string): number | null {
	try {
		const part = token.split('.')[1];
		if (!part) return null;
		const padded = part.replace(/-/g, '+').replace(/_/g, '/');
		const json = JSON.parse(atob(padded + '='.repeat((4 - (padded.length % 4)) % 4)));
		return typeof json.exp === 'number' ? json.exp * 1000 : null;
	} catch {
		return null;
	}
}

export function isExpired(account: StoredAccount, now: number = Date.now()): boolean {
	if (account.expired) return true;
	const exp = jwtExpiryMs(account.token);
	return exp !== null && exp <= now;
}

export function normalizeRegistry(registry: AccountRegistry, now: number = Date.now()): AccountRegistry {
	const accounts: StoredAccount[] = [];
	for (const account of registry.accounts) {
		let next = account;
		if (!next.expired) {
			const exp = jwtExpiryMs(next.token);
			if (exp !== null && exp <= now) next = { ...next, expired: true, expiredAt: exp };
		} else if (next.expiredAt === undefined) {
			next = { ...next, expiredAt: now };
		}
		if (next.expired && next.expiredAt !== undefined && now - next.expiredAt > EXPIRED_PRUNE_MS) continue;
		accounts.push(next);
	}
	const activeId = accounts.some((a) => a.userId === registry.activeId) ? registry.activeId : null;
	return { v: 1, activeId, accounts };
}

export function upsertAccount(
	registry: AccountRegistry,
	profile: AccountProfile,
	token: string,
	now: number = Date.now()
): { registry: AccountRegistry; outcome: UpsertOutcome } {
	const existing = registry.accounts.find((a) => a.userId === profile.userId);
	const entry: StoredAccount = {
		userId: profile.userId,
		username: profile.username,
		role: profile.role,
		avatar: profile.avatar,
		token,
		signedInAt: now,
		expired: false
	};
	if (existing) {
		return {
			registry: {
				...registry,
				accounts: registry.accounts.map((a) => (a.userId === profile.userId ? entry : a))
			},
			outcome: 'replaced'
		};
	}
	if (registry.accounts.length >= MAX_ACCOUNTS) return { registry, outcome: 'full' };
	return { registry: { ...registry, accounts: [...registry.accounts, entry] }, outcome: 'added' };
}

export function refreshProfile(registry: AccountRegistry, profile: AccountProfile): AccountRegistry {
	return {
		...registry,
		accounts: registry.accounts.map((a) =>
			a.userId === profile.userId
				? { ...a, username: profile.username, role: profile.role, avatar: profile.avatar }
				: a
		)
	};
}

export function setActive(registry: AccountRegistry, userId: string | null): AccountRegistry {
	return { ...registry, activeId: userId };
}

export function removeAccount(registry: AccountRegistry, userId: string): AccountRegistry {
	return {
		...registry,
		activeId: registry.activeId === userId ? null : registry.activeId,
		accounts: registry.accounts.filter((a) => a.userId !== userId)
	};
}

export function markExpired(registry: AccountRegistry, userId: string, now: number = Date.now()): AccountRegistry {
	return {
		...registry,
		accounts: registry.accounts.map((a) =>
			a.userId === userId && !a.expired ? { ...a, expired: true, expiredAt: now } : a
		)
	};
}

export function findByToken(registry: AccountRegistry, token: string | null | undefined): StoredAccount | null {
	if (!token) return null;
	return registry.accounts.find((a) => a.token === token) ?? null;
}

export function activeAccount(registry: AccountRegistry): StoredAccount | null {
	return registry.accounts.find((a) => a.userId === registry.activeId) ?? null;
}

export function parseRegistry(raw: string | null, now: number = Date.now()): AccountRegistry {
	if (!raw) return emptyRegistry();
	try {
		const parsed = JSON.parse(raw);
		if (!parsed || parsed.v !== 1 || !Array.isArray(parsed.accounts)) return emptyRegistry();
		const accounts = parsed.accounts.filter(
			(a: unknown): a is StoredAccount =>
				!!a &&
				typeof (a as StoredAccount).userId === 'string' &&
				typeof (a as StoredAccount).token === 'string' &&
				typeof (a as StoredAccount).username === 'string'
		);
		return normalizeRegistry(
			{ v: 1, activeId: typeof parsed.activeId === 'string' ? parsed.activeId : null, accounts },
			now
		);
	} catch {
		return emptyRegistry();
	}
}

export function loadRegistry(now: number = Date.now()): AccountRegistry {
	if (typeof localStorage === 'undefined') return emptyRegistry();
	try {
		return parseRegistry(localStorage.getItem(ACCOUNTS_STORAGE_KEY), now);
	} catch {
		return emptyRegistry();
	}
}

export function saveRegistry(registry: AccountRegistry): void {
	if (typeof localStorage === 'undefined') return;
	try {
		if (registry.accounts.length === 0 && registry.activeId === null) {
			localStorage.removeItem(ACCOUNTS_STORAGE_KEY);
		} else {
			localStorage.setItem(ACCOUNTS_STORAGE_KEY, JSON.stringify(registry));
		}
	} catch {
		return;
	}
}
