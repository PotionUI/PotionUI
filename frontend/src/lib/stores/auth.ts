import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import { storage } from '$lib/utils/storage';
import { tabsStore } from '$lib/stores/tabs';
import { chatSession } from '$lib/stores/chatSession';
import { chatComposerDrafts } from '$lib/stores/chatComposerDrafts';
import { historyStore } from '$lib/stores/history';
import { libraryStore } from '$lib/stores/library';
import { phrasebookStore } from '$lib/stores/phrasebook';
import { nsfwFilterStore } from '$lib/stores/nsfwFilter';
import { previewGenerationStore } from '$lib/stores/previewGeneration';
import { LAST_USER_ID_KEY, isDifferentIdentity, keysToPurge } from '$lib/stores/identityScopedStorage';

export interface User {
	id: string;
	username: string;
	email: string;
	account_type: 'USER' | 'ADMIN';
	created_at: string | null;
	last_login: string | null;
	avatar_url: string | null;
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

// Tabs (prompts, presets, form data), the active chat session, the selected
// LLM config, and the phrasebook preview config are held in localStorage
// un-namespaced by user. If this browser's last known identity differs from
// the one that just authenticated, that state belongs to whoever was signed
// in before — wipe it before the new session can read or overwrite it.
// A same-user relogin (e.g. after a token expiry) must NOT trigger this.
// localStorage is not the only surface: module-scope stores that outlive a
// login/logout cycle (the chat conversation and its unsent composer draft
// live in one so the panel survives open/close, history/library/phrasebook
// keep a "selected item" so a route can remount mid-edit) hold identity
// content in memory and must
// be reset too — otherwise a route that renders its "selected" field with no
// loading gate (the details modal on History/Library, the phrasebook editor
// pane) paints the previous user's content the instant it remounts.
export function applyIdentityGuard(userId: string): void {
	if (!browser) return;
	const lastUserId = storage.get(LAST_USER_ID_KEY);
	if (isDifferentIdentity(lastUserId, userId)) {
		const allKeys: string[] = [];
		for (let i = 0; i < localStorage.length; i++) {
			const key = localStorage.key(i);
			if (key) allKeys.push(key);
		}
		for (const key of keysToPurge(allKeys)) localStorage.removeItem(key);
		tabsStore.reset();
		chatSession.reset();
		chatComposerDrafts.reset();
		historyStore.reset();
		libraryStore.reset();
		phrasebookStore.reset();
		nsfwFilterStore.reset();
		previewGenerationStore.reset();
	}
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

	// Register this for the lifetime of the store, including sessions created by
	// logging in after the app originally mounted without a token.
	api.setOnAuthExpired(() => {
		update((currentState) => {
			if (!currentState.isAuthenticated) return currentState;
			storage.remove('auth_token');
			api.clearAuth();
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
	});

	// Initialize from localStorage on browser
	const token = storage.get('auth_token');
	if (token) {
		api.setAuthHeader(token);
		update((state) => ({
			...state,
			isAuthenticated: true,
			token,
			loading: true
		}));

		// Fetch user info on initialization
		api.getCurrentUser()
			.then((response) => {
				if (response.success && response.data) {
					applyIdentityGuard(response.data.id);
					update((state) => ({
						...state,
						user: response.data ?? null,
						loading: false
					}));
				} else {
					// Token is invalid/expired - clear it
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
		update((state) => ({ ...state, loading: false }));
	}

	return {
		subscribe,

		async login(username: string, password: string, rememberMe: boolean = false) {
			update((state) => ({ ...state, loading: true, error: null }));

			try {
				const response = await api.login({ username, password, remember_me: rememberMe });

				if (response.access_token) {
					update((state) => ({
						...state,
						isAuthenticated: true,
						token: response.access_token,
						user: null,
						loading: false,
						error: null
					}));

					// Try to fetch user info
					try {
						const userResponse = await api.getCurrentUser();
						if (userResponse.success && userResponse.data) {
							applyIdentityGuard(userResponse.data.id);
							update((state) => ({
								...state,
								user: userResponse.data ?? null
							}));
						}
					} catch (err) {
						logger.error('Failed to fetch user info:', err);
					}

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

		logout() {
			storage.remove('auth_token');
			api.clearAuth();
			// Explicit logout drops the in-memory conversation; a same-user
			// relogin restores it from the backend via the persisted session id.
			chatSession.reset();
			set({
				isAuthenticated: false,
				token: null,
				user: null,
				loading: false,
				error: null
			});
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
