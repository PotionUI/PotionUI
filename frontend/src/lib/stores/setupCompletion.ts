import { writable } from 'svelte/store';

/**
 * A plain "something finished" ping the /setup page fires once a guided
 * setup run reaches `completed`, so `Sidebar.svelte`'s "Resume setup" nudge
 * (fetched once per session — see its own comment) can re-check readiness
 * right away instead of lingering until the next full page load.
 *
 * Deliberately just a counter, not the run itself — the only thing a
 * subscriber needs to know is "re-check now", not what completed.
 */
export const setupCompletionPing = writable(0);

export function notifySetupCompleted(): void {
	setupCompletionPing.update((n) => n + 1);
}
