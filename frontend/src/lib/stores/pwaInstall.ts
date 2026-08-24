import { writable } from 'svelte/store';
import { browser } from '$app/environment';

interface BeforeInstallPromptEvent extends Event {
	prompt(): Promise<void>;
	userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export const canInstall = writable(false);
let deferredPrompt: BeforeInstallPromptEvent | null = null;

export function initPwaInstall() {
	if (!browser) return;

	// Already installed as standalone PWA
	if (window.matchMedia('(display-mode: standalone)').matches) {
		return;
	}

	window.addEventListener('beforeinstallprompt', (e) => {
		e.preventDefault();
		deferredPrompt = e as BeforeInstallPromptEvent;
		canInstall.set(true);
	});

	window.addEventListener('appinstalled', () => {
		deferredPrompt = null;
		canInstall.set(false);
	});
}

export async function promptInstall(): Promise<boolean> {
	if (!deferredPrompt) return false;

	await deferredPrompt.prompt();
	const { outcome } = await deferredPrompt.userChoice;

	if (outcome === 'accepted') {
		deferredPrompt = null;
		canInstall.set(false);
		return true;
	}

	return false;
}
