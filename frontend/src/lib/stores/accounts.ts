import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import { api } from '$lib/services/api/index';
import { storage } from '$lib/utils/storage';
import type { User } from '$lib/stores/auth';
import {
	ACCOUNTS_STORAGE_KEY,
	MAX_ACCOUNTS,
	activeAccount,
	emptyRegistry,
	findByToken,
	isExpired,
	markExpired as markRegistryExpired,
	normalizeRegistry,
	parseRegistry,
	removeAccount as removeRegistryAccount,
	saveRegistry,
	setActive,
	upsertAccount,
	type AccountProfile,
	type AccountRegistry,
	type StoredAccount
} from '$lib/stores/accountRegistry';
import {
	LAST_USER_ID_KEY,
	bootIdentity,
	readActiveIdentity,
	removeAllIdentityKeys,
	removeIdentityKeys
} from '$lib/stores/identityScopedStorage';

export const ADDING_ACCOUNT_FLAG = 'potionui_adding';
const FOLLOWER_RELOAD_DELAY_MS = 120;

export interface SwitchOverlayState {
	kind: 'initiator' | 'follower';
	username: string | null;
	role: string | null;
	step: 'verifying' | 'reloading';
}

export type AddOutcome =
	| { status: 'added'; username: string }
	| { status: 'refreshed'; username: string }
	| { status: 'duplicate'; username: string; userId: string }
	| { status: 'full' }
	| { status: 'error'; message: string };

export type SwitchOutcome = { ok: true } | { ok: false; reason: 'missing' | 'expired' | 'failed' };

export type UnauthorizedOutcome = 'active' | 'other';

export function profileOf(user: User): AccountProfile {
	return {
		userId: user.id,
		username: user.username,
		role: user.account_type,
		avatar: user.avatar_url
	};
}

function loadInitialRegistry(): AccountRegistry {
	if (!browser || typeof localStorage === 'undefined') return emptyRegistry();
	let raw: string | null = null;
	try {
		raw = localStorage.getItem(ACCOUNTS_STORAGE_KEY);
	} catch {
		return emptyRegistry();
	}
	const next = parseRegistry(raw);
	let storedIds: string[] = [];
	try {
		const parsed = raw ? JSON.parse(raw) : null;
		storedIds = Array.isArray(parsed?.accounts) ? parsed.accounts.map((a: StoredAccount) => a?.userId) : [];
	} catch {
		storedIds = [];
	}
	const keptIds = new Set(next.accounts.map((a) => a.userId));
	for (const id of storedIds) {
		if (typeof id === 'string' && !keptIds.has(id)) removeIdentityKeys(id);
	}
	if (raw !== null && JSON.stringify(next) !== raw) saveRegistry(next);
	return next;
}

const registryStore = writable<AccountRegistry>(loadInitialRegistry());
export const accountsStore = { subscribe: registryStore.subscribe };

export const switchOverlay = writable<SwitchOverlayState | null>(null);

let switching = false;
let tabActiveId: string | null = get(registryStore).activeId;

export function currentRegistry(): AccountRegistry {
	return get(registryStore);
}

export function isSwitching(): boolean {
	return switching;
}

function commit(next: AccountRegistry): void {
	saveRegistry(next);
	registryStore.set(next);
	tabActiveId = next.activeId;
	const active = activeAccount(next);
	if (active && !active.expired) storage.set('auth_token', active.token);
}

function reloadCurrentPage(): void {
	if (typeof location === 'undefined') return;
	if (location.pathname.startsWith('/login')) location.assign('/generate');
	else location.reload();
}

export function needsReloadForIdentity(): boolean {
	return readActiveIdentity() !== bootIdentity;
}

export function accountCount(): number {
	return get(registryStore).accounts.length;
}

export function canAddAccount(userId?: string): boolean {
	const registry = get(registryStore);
	if (userId && registry.accounts.some((a) => a.userId === userId)) return true;
	return registry.accounts.length < MAX_ACCOUNTS;
}

export function refreshExpiry(now: number = Date.now()): void {
	const before = get(registryStore);
	const next = normalizeRegistry(before, now);
	if (JSON.stringify(next) === JSON.stringify(before)) return;
	const keptIds = new Set(next.accounts.map((a) => a.userId));
	for (const account of before.accounts) {
		if (!keptIds.has(account.userId)) removeIdentityKeys(account.userId);
	}
	commit(next);
}

export function syncActive(user: User, token: string): 'added' | 'replaced' | 'full' {
	const registry = get(registryStore);
	const existing = registry.accounts.find((a) => a.userId === user.id);
	if (!existing && registry.accounts.length >= MAX_ACCOUNTS) return 'full';
	const profile = profileOf(user);
	if (existing && existing.token === token) {
		const refreshed: AccountRegistry = {
			...registry,
			accounts: registry.accounts.map((a) =>
				a.userId === user.id
					? {
							...a,
							username: profile.username,
							role: profile.role,
							avatar: profile.avatar,
							expired: false,
							expiredAt: undefined
						}
					: a
			)
		};
		commit(setActive(refreshed, user.id));
		return 'replaced';
	}
	const { registry: upserted, outcome } = upsertAccount(registry, profile, token);
	if (outcome === 'full') return 'full';
	commit(setActive(upserted, user.id));
	return outcome;
}

export function handleUnauthorized(requestToken: string | null | undefined): UnauthorizedOutcome {
	const registry = get(registryStore);
	const owner = findByToken(registry, requestToken);
	if (owner && owner.userId !== registry.activeId) {
		commit({ ...markRegistryExpired(registry, owner.userId), activeId: registry.activeId });
		return 'other';
	}
	const target = owner?.userId ?? registry.activeId;
	if (target) {
		commit({ ...markRegistryExpired(registry, target), activeId: registry.activeId });
	}
	return 'active';
}

export function markAccountExpired(userId: string): void {
	commit({ ...markRegistryExpired(get(registryStore), userId), activeId: get(registryStore).activeId });
}

export function removeAccount(userId: string): void {
	const registry = get(registryStore);
	const wasActive = registry.activeId === userId;
	const next = removeRegistryAccount(registry, userId);
	removeIdentityKeys(userId);
	try {
		if (localStorage.getItem(LAST_USER_ID_KEY) === userId) localStorage.removeItem(LAST_USER_ID_KEY);
	} catch {
		storage.remove(LAST_USER_ID_KEY);
	}
	if (wasActive) storage.remove('auth_token');
	saveRegistry(next);
	registryStore.set(next);
	tabActiveId = next.activeId;
}

export function clearAllAccounts(): void {
	removeAllIdentityKeys();
	storage.remove('auth_token');
	storage.remove(LAST_USER_ID_KEY);
	const next = emptyRegistry();
	saveRegistry(next);
	registryStore.set(next);
	tabActiveId = null;
}

function restoreActiveSession(): void {
	const active = activeAccount(get(registryStore));
	if (active && !active.expired) api.setAuthHeader(active.token);
	else api.clearAuth();
}

export async function restoreActiveCookie(): Promise<void> {
	restoreActiveSession();
	if (!activeAccount(get(registryStore))) return;
	try {
		await api.getCurrentUser();
	} catch {
		return;
	}
}

function is401(error: unknown): boolean {
	return (error as { response?: { status?: number } })?.response?.status === 401;
}

export async function switchTo(userId: string): Promise<SwitchOutcome> {
	const registry = get(registryStore);
	const target = registry.accounts.find((a) => a.userId === userId);
	if (!target) return { ok: false, reason: 'missing' };
	if (isExpired(target)) return { ok: false, reason: 'expired' };
	if (switching) return { ok: false, reason: 'failed' };

	switching = true;
	switchOverlay.set({ kind: 'initiator', username: target.username, role: target.role, step: 'verifying' });
	api.setAuthHeader(target.token);
	try {
		const response = await api.getCurrentUser();
		if (!response.success) throw new Error('me failed');
		const user = response.data;
		const verified = user ? withProfile(registry, user, target) : registry;
		commit(setActive(verified, userId));
		switchOverlay.set({ kind: 'initiator', username: target.username, role: target.role, step: 'reloading' });
		reloadCurrentPage();
		return { ok: true };
	} catch (error) {
		if (is401(error)) markAccountExpired(userId);
		restoreActiveSession();
		switchOverlay.set(null);
		switching = false;
		return { ok: false, reason: is401(error) ? 'expired' : 'failed' };
	}
}

function withProfile(registry: AccountRegistry, user: User, target: StoredAccount): AccountRegistry {
	const profile = profileOf(user);
	return {
		...registry,
		accounts: registry.accounts.map((a) =>
			a.userId === target.userId
				? { ...a, username: profile.username, role: profile.role, avatar: profile.avatar }
				: a
		)
	};
}

export async function addAccountFromToken(token: string): Promise<AddOutcome> {
	const previous = activeAccount(get(registryStore));
	switching = true;
	let navigating = false;
	try {
		api.setAuthHeader(token);
		const response = await api.getCurrentUser();
		if (!response.success || !response.data) throw new Error('me failed');
		const user = response.data;
		const registry = get(registryStore);
		const existing = registry.accounts.find((a) => a.userId === user.id);

		if (existing) {
			const { registry: replaced } = upsertAccount(registry, profileOf(user), token);
			const keepActive = previous ? setActive(replaced, previous.userId) : replaced;
			saveRegistry(keepActive);
			registryStore.set(keepActive);
			if (isExpired(existing)) {
				await restoreActiveCookie();
				return { status: 'refreshed', username: user.username };
			}
			return { status: 'duplicate', username: user.username, userId: user.id };
		}
		if (registry.accounts.length >= MAX_ACCOUNTS) {
			await restoreActiveCookie();
			return { status: 'full' };
		}
		const { registry: added } = upsertAccount(registry, profileOf(user), token);
		navigating = true;
		switchOverlay.set({ kind: 'initiator', username: user.username, role: user.account_type, step: 'reloading' });
		commit(setActive(added, user.id));
		if (typeof location !== 'undefined') location.assign('/generate');
		return { status: 'added', username: user.username };
	} catch (error) {
		await restoreActiveCookie();
		const message = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
		return {
			status: 'error',
			message: typeof message === 'string' ? message : 'Could not add this account. Please try again.'
		};
	} finally {
		if (!navigating) switching = false;
	}
}

export async function addAccountWithPassword(
	username: string,
	password: string,
	rememberMe: boolean
): Promise<AddOutcome> {
	let token: string;
	const previousToken = api.getToken();
	try {
		const response = await api.login({ username, password, remember_me: rememberMe });
		token = response.access_token;
		if (!token) return { status: 'error', message: 'Login did not return an access token.' };
	} catch (error) {
		if (previousToken) api.setAuthHeader(previousToken);
		const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
		const message =
			typeof detail === 'string'
				? detail
				: typeof (detail as { message?: unknown })?.message === 'string'
					? (detail as { message: string }).message
					: 'Login failed. Please try again.';
		return { status: 'error', message };
	}
	return addAccountFromToken(token);
}

export async function stayOnCurrentAccount(): Promise<void> {
	await restoreActiveCookie();
}

export function handleStorageEvent(event: { key: string | null; newValue: string | null }): void {
	if (event.key !== null && event.key !== ACCOUNTS_STORAGE_KEY) return;
	const next = event.key === null ? emptyRegistry() : parseRegistry(event.newValue);
	if (next.activeId !== tabActiveId) {
		const followed = activeAccount(next);
		switchOverlay.set({
			kind: 'follower',
			username: followed?.username ?? null,
			role: followed?.role ?? null,
			step: 'reloading'
		});
		setTimeout(() => {
			if (typeof location !== 'undefined') location.reload();
		}, FOLLOWER_RELOAD_DELAY_MS);
		return;
	}
	registryStore.set(next);
}

let crossTabInstalled = false;

export function initCrossTab(): () => void {
	if (!browser || typeof window === 'undefined' || crossTabInstalled) return () => {};
	crossTabInstalled = true;
	const listener = (event: StorageEvent) => handleStorageEvent({ key: event.key, newValue: event.newValue });
	window.addEventListener('storage', listener);
	return () => {
		window.removeEventListener('storage', listener);
		crossTabInstalled = false;
	};
}

export function resetAccountsForTests(): void {
	switching = false;
	switchOverlay.set(null);
	registryStore.set(loadInitialRegistry());
	tabActiveId = get(registryStore).activeId;
}
