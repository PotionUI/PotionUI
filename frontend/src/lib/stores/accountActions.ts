import { goto } from '$app/navigation';
import { authStore } from '$lib/stores/auth';
import { canAddAccount, currentRegistry, refreshExpiry, switchTo } from '$lib/stores/accounts';
import { MAX_ACCOUNTS, type StoredAccount } from '$lib/stores/accountRegistry';
import { confirmDialog } from '$lib/stores/confirm';
import { toasts } from '$lib/stores/toast';

export const ADD_ACCOUNT_BLOCKED_MESSAGE = `Remove an account first. This browser holds ${MAX_ACCOUNTS} already.`;

export async function switchAccount(account: StoredAccount): Promise<void> {
	const outcome = await switchTo(account.userId);
	if (outcome.ok) return;
	if (outcome.reason === 'expired') {
		toasts.error(`${account.username}'s session has expired. Sign in again to switch.`);
	} else if (outcome.reason === 'failed') {
		toasts.error(`Could not switch to ${account.username}. Try again.`);
	}
}

export function signInAgain(account: StoredAccount): void {
	void goto(`/login?add=1&as=${encodeURIComponent(account.username)}`);
}

export function startAddAccount(): boolean {
	if (!canAddAccount()) {
		toasts.error(ADD_ACCOUNT_BLOCKED_MESSAGE);
		return false;
	}
	void goto('/login?add=1');
	return true;
}

export function logoutAccount(account: StoredAccount): void {
	authStore.logoutAccount(account.userId);
}

export function logoutActiveAccount(): void {
	authStore.logout();
}

export async function logoutEverywhere(): Promise<boolean> {
	const names = currentRegistry().accounts.map((a) => a.username);
	const confirmed = await confirmDialog({
		title: 'Log out of all accounts?',
		message: `This signs out every account on this browser (${names.join(', ')}) and clears their open tabs and unsent drafts here. Your sessions, history and library stay on the server.`,
		variant: 'danger',
		confirmLabel: 'Log out of all'
	});
	if (!confirmed) return false;
	authStore.logoutAll();
	return true;
}

export function refreshAccountList(): void {
	refreshExpiry();
}
