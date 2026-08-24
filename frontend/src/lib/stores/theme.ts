import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';

export type ThemePref = 'dark' | 'light' | 'system';
export type ResolvedTheme = 'dark' | 'light';

const STORAGE_KEY = 'potionui-theme';
const THEME_COLORS: Record<ResolvedTheme, string> = {
	dark: '#0D0D0D',
	light: '#F7F7F7'
};

interface ThemeState {
	pref: ThemePref;
	resolved: ResolvedTheme;
}

function systemTheme(): ResolvedTheme {
	if (!browser) return 'dark';
	return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function resolve(pref: ThemePref): ResolvedTheme {
	return pref === 'system' ? systemTheme() : pref;
}

function loadPref(): ThemePref {
	if (!browser) return 'dark';
	const stored = localStorage.getItem(STORAGE_KEY);
	if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
	// Unset defaults to dark (not system) while the light theme rollout is in
	// progress — flip to 'system' once all core screens are token-migrated.
	return 'dark';
}

function apply(resolved: ResolvedTheme) {
	if (!browser) return;
	document.documentElement.dataset.theme = resolved;
	const meta = document.querySelector('meta[name="theme-color"]');
	if (meta) meta.setAttribute('content', THEME_COLORS[resolved]);
}

function createThemeStore() {
	const initialPref = loadPref();
	const { subscribe, set } = writable<ThemeState>({
		pref: initialPref,
		resolved: resolve(initialPref)
	});

	let currentPref: ThemePref = initialPref;
	let mediaQuery: MediaQueryList | null = null;

	function onSystemChange() {
		if (currentPref === 'system') {
			const resolved = systemTheme();
			apply(resolved);
			set({ pref: currentPref, resolved });
		}
	}

	return {
		subscribe,

		/** Initialize on mount: apply persisted pref, watch system changes,
		 *  and honor a dev-only ?theme= override for QA while light is gated. */
		init() {
			if (!browser) return;

			const param = new URLSearchParams(window.location.search).get('theme');
			if (param === 'light' || param === 'dark') {
				currentPref = param;
			}

			mediaQuery = window.matchMedia('(prefers-color-scheme: light)');
			mediaQuery.addEventListener('change', onSystemChange);

			const resolved = resolve(currentPref);
			apply(resolved);
			set({ pref: currentPref, resolved });
		},

		setPref(pref: ThemePref) {
			currentPref = pref;
			if (browser) localStorage.setItem(STORAGE_KEY, pref);
			const resolved = resolve(pref);
			apply(resolved);
			set({ pref, resolved });
		},

		destroy() {
			mediaQuery?.removeEventListener('change', onSystemChange);
			mediaQuery = null;
		}
	};
}

export const themeStore = createThemeStore();

export const resolvedTheme = derived(themeStore, ($t) => $t.resolved);
