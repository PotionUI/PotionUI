import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import { storage } from '$lib/utils/storage';
import { chatSession } from '$lib/stores/chatSession';
import { nsfwFilterStore } from '$lib/stores/nsfwFilter';
import { LAST_USER_ID_KEY } from '$lib/stores/identityScopedStorage';
import { activeAccount } from '$lib/stores/accountRegistry';
import {
	clearAllAccounts,
	currentRegistry,
	handleUnauthorized,
	isSwitching,
	needsReloadForIdentity,
	removeAccount,
	syncActive
} from '$lib/stores/accounts';

export interface User {
	id: string;
	username: string;
	email: string;
	account_type: 'USER' | 'ADMIN';
	created_at: string | null;
	last_login: string | null;
	avatar_url: string | null;
	has_local_password: boolean;
	content_restricted?: boolean;
	effective_content_policy?: 'allowed' | 'blur' | 'blocked';
}

interface AuthState {
	isAuthenticated: boolean;
	token: string | null;
	user: User | null;
	loading: boolean;
	error: string | null;
}

// The backend's error_response() wraps failures as detail: { error, message }
// (see src/platform/http/base_controller.py), not a bare string - unwrap it
// so callers (e.g. the claim screen) see the actual "already claimed" /
// "invalid setup token" text instead of a stringified object.
function extractApiErrorMessage(error: any): string | undefined {
	const detail = error?.response?.data?.detail;
	if (typeof detail === 'string') return detail;
	if (detail && typeof detail.message === 'string') return detail.message;
	return error?.message;
}

export function applyIdentityGuard(userId: string): void {
	if (!browser) return;
	storage.set(LAST_USER_ID_KEY, userId);
}

function createAuthStore() {
	const initialState: AuthState = {
		isAuthenticated: false,
		token: null,
		user: null,
		loading: true,
		error: null
	};

	const { subscribe, set, update } = writable(initialState);

	function expireSession(requestToken: string | null = api.getToken()) {
		if (isSwitching()) return;
		if (handleUnauthorized(requestToken) === 'other') return;
		update((currentState) => {
			if (!currentState.isAuthenticated) return currentState;
			storage.remove('auth_token');
			api.clearAuth();
			void api.endMediaSession();
			goto('/login?expired=1');
			return {
				...currentState,
				isAuthenticated: false,
				token: null,
				user: null,
				loading: false,
				error: null
			};
		});
	}

	api.setOnAuthExpired((requestToken) => expireSession(requestToken));

	function hardNavigate(path: string) {
		if (typeof location !== 'undefined') location.assign(path);
	}

	const registryActive = activeAccount(currentRegistry());
	const token = registryActive ? (registryActive.expired ? null : registryActive.token) : storage.get('auth_token');
	if (token) {
		if (api.getToken() !== token) api.setAuthHeader(token);
		update((state) => ({
			...state,
			isAuthenticated: true,
			token,
			loading: true
		}));

		api.getCurrentUser()
			.then((response) => {
				if (response.success && response.data) {
					applyIdentityGuard(response.data.id);
					syncActive(response.data, token);
					if (needsReloadForIdentity() && typeof location !== 'undefined') {
						hardNavigate(location.pathname + location.search);
						return;
					}
					update((state) => ({
						...state,
						user: response.data ?? null,
						loading: false
					}));
				} else {
					storage.remove('auth_token');
					api.clearAuth();
					update((state) => ({ ...state, isAuthenticated: false, loading: false }));
				}
			})
			.catch((err) => {
				logger.error('Failed to fetch user info on init:', err);
				storage.remove('auth_token');
				api.clearAuth();
				update((state) => ({ ...state, isAuthenticated: false, loading: false }));
			});
	} else {
		if (registryActive?.expired) storage.remove('auth_token');
		update((state) => ({ ...state, loading: false }));
	}

	async function establishAuthenticatedSession(token: string) {
		api.setAuthHeader(token);
		update((state) => ({
			...state,
			isAuthenticated: true,
			token,
			user: null,
			loading: false,
			error: null
		}));

		try {
			const userResponse = await api.getCurrentUser();
			if (userResponse.success && userResponse.data) {
				if (syncActive(userResponse.data, token) === 'full') {
					api.clearAuth();
					void api.endMediaSession();
					const message = 'This browser already holds 5 accounts. Remove one first.';
					update((state) => ({
						...state,
						isAuthenticated: false,
						token: null,
						user: null,
						error: message
					}));
					return { ok: false as const, error: message };
				}
				applyIdentityGuard(userResponse.data.id);
				update((state) => ({
					...state,
					user: userResponse.data ?? null
				}));
			}
		} catch (err) {
			logger.error('Failed to fetch user info:', err);
		}
		return { ok: true as const };
	}


	function logoutActive() {
		const activeId = currentRegistry().activeId ?? storage.get(LAST_USER_ID_KEY);
		api.clearAuth();
		void api.endMediaSession();
		chatSession.reset();
		if (activeId) removeAccount(activeId);
		else storage.remove('auth_token');
		set({
			isAuthenticated: false,
			token: null,
			user: null,
			loading: false,
			error: null
		});
		hardNavigate('/login');
	}

	return {
		subscribe,

		async login(username: string, password: string, rememberMe: boolean = false) {
			update((state) => ({ ...state, loading: true, error: null }));

			try {
				const response = await api.login({ username, password, remember_me: rememberMe });

				if (response.access_token) {
					const session = await establishAuthenticatedSession(response.access_token);
					if (!session.ok) return { success: false, error: session.error };
					return { success: true };
				}
				return { success: false, error: 'Login did not return an access token.' };
			} catch (error: any) {
				const errorMessage = extractApiErrorMessage(error) || 'Login failed. Please try again.';

				update((state) => ({
					...state,
					loading: false,
					error: errorMessage
				}));

				return { success: false, error: errorMessage };
			}
		},

		async register(username: string, email: string, password: string, claimToken?: string) {
			update((state) => ({ ...state, loading: true, error: null }));

			try {
				const response = await api.register({ username, email, password, claim_token: claimToken });

				if (response.success && response.data?.access_token) {
					syncActive(response.data.user, response.data.access_token);
					applyIdentityGuard(response.data.user.id);
					update((state) => ({
						...state,
						isAuthenticated: true,
						token: response.data!.access_token,
						user: response.data!.user,
						loading: false,
						error: null
					}));

					return { success: true };
				} else {
					const errorMessage = response.message || 'Registration failed. Please try again.';
					update((state) => ({
						...state,
						loading: false,
						error: errorMessage
					}));
					return { success: false, error: errorMessage };
				}
			} catch (error: any) {
				const errorMessage = extractApiErrorMessage(error) || 'Registration failed. Please try again.';

				update((state) => ({
					...state,
					loading: false,
					error: errorMessage
				}));

				return { success: false, error: errorMessage };
			}
		},

		async adoptToken(token: string) {
			return establishAuthenticatedSession(token);
		},

		expireSession,

		logout: logoutActive,

		logoutAccount(userId: string) {
			if (userId === currentRegistry().activeId) {
				logoutActive();
				return;
			}
			removeAccount(userId);
		},

		logoutAll() {
			api.clearAuth();
			void api.endMediaSession();
			chatSession.reset();
			clearAllAccounts();
			set({
				isAuthenticated: false,
				token: null,
				user: null,
				loading: false,
				error: null
			});
			hardNavigate('/login');
		},

		finishSignIn(path: string) {
			if (needsReloadForIdentity()) hardNavigate(path);
			else void goto(path, { replaceState: true });
		},

		clearError() {
			update((state) => ({ ...state, error: null }));
		},

		// Re-fetch the current user (e.g. after an avatar change) so every
		// subscriber (UserMenu, settings page) picks up the new fields.
		async refreshUser() {
			try {
				const response = await api.getCurrentUser();
				if (response.success && response.data) {
					update((state) => ({ ...state, user: response.data ?? null }));
					return true;
				}
			} catch (err) {
				logger.error('Failed to refresh user info:', err);
			}
			return false;
		}
	};
}

export const authStore = createAuthStore();

authStore.subscribe((state) => {
	nsfwFilterStore.setRestricted(!!state.user?.content_restricted);
});
