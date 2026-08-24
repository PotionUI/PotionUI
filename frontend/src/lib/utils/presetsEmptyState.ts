import type { ReadinessCheck, ReadinessReport } from '$lib/services/api';

export type PresetsEmptyStateKind = 'owner-unconfigured' | 'user-not-assigned' | 'unknown';

export interface PresetsEmptyState {
	kind: PresetsEmptyStateKind;
	title: string;
	/** Already role-resolved by the backend - safe to render verbatim. */
	message: string;
	/** Admin-only repair instruction; always null for a regular user. */
	action: string | null;
	/** Whether to show a link into the guided /setup flow. */
	showSetupLink: boolean;
}

// Preference order when more than one facet is blocking: `content` (presets/
// models) is the direct explanation for an empty preset list, `execution` (no
// healthy backend) is the next most likely cause, `service` last since an
// unreachable DB would usually fail the presets fetch outright rather than
// return an empty list.
const BLOCKER_PRIORITY: ReadinessCheck['area'][] = ['content', 'execution', 'service'];

function pickBlocker(readiness: ReadinessReport): ReadinessCheck | null {
	for (const area of BLOCKER_PRIORITY) {
		const check = readiness.checks.find((c) => c.area === area && c.status !== 'ready');
		if (check) return check;
	}
	return null;
}

const FALLBACK_ADMIN_MESSAGE = 'This instance has no presets installed and assigned yet.';
const FALLBACK_USER_MESSAGE = "You don't have any presets yet. Ask your administrator to assign one.";

/**
 * Explains why the preset list on /generate (and the PresetPicker modal) is
 * empty, so a fresh user never lands on a bare "nothing selectable" screen.
 * `/api/readiness` already resolves `message`/`action` per the caller's role
 * (see src/features/setup/readiness.py) - this just picks which facet best
 * explains the empty list and whether a /setup link belongs on the page.
 * Never call this when `readiness` came back for a non-empty preset list;
 * it always assumes "the list is empty" is the thing being explained.
 */
export function describePresetsEmptyState(
	readiness: ReadinessReport | null,
	isAdmin: boolean
): PresetsEmptyState {
	const blocker = readiness ? pickBlocker(readiness) : null;

	if (!blocker) {
		// No readiness yet (fetch failed/pending) or readiness reports nothing
		// blocking - still don't leave the screen silent.
		return {
			kind: 'unknown',
			title: 'Nothing to generate with yet',
			message: isAdmin ? FALLBACK_ADMIN_MESSAGE : FALLBACK_USER_MESSAGE,
			action: null,
			showSetupLink: isAdmin
		};
	}

	const kind: PresetsEmptyStateKind =
		blocker.area === 'content' && blocker.code === 'NO_PRESETS_ASSIGNED'
			? 'user-not-assigned'
			: 'owner-unconfigured';

	return {
		kind,
		title: 'Nothing to generate with yet',
		message: blocker.message,
		action: isAdmin ? (blocker.action ?? null) : null,
		showSetupLink: isAdmin
	};
}
