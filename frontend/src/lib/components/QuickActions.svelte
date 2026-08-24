<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { pluginQuickActions } from '$lib/stores/plugins';
	import { authStore } from '$lib/stores/auth';
	import { keybindingsStore, shortcutLabels } from '$lib/stores/keybindings';
	import { getBackends } from '$lib/services/admin-api';
	import type { BackendQuickAction } from '$lib/services/admin-api';
	import { logger } from '$lib/utils/logger';
	import BackendActionModal from './BackendActionModal.svelte';
	import FuzzyFindModal from './modals/FuzzyFindModal.svelte';
	import Tooltip from './Tooltip.svelte';

	// Core navbar quick-actions palette. Aggregates action SOURCES into one
	// fuzzy-finder list; plugins are one source (via `/api/plugins/quick-actions`)
	// alongside core sources like the backends' admin operations below. To add a
	// new core source, give it its own `$: xItems = ...` list shaped like
	// `QuickActionItem[]` and fold it into `allItems` below.

	const BOLT_PATH = 'M13 10V3L4 14h7v7l9-11h-7z';

	let showModal = false;
	let actionModal: BackendActionModal;
	let runningId: string | null = null;

	/** One item in the palette. Every source's action is a method+endpoint pair
	 * run through the same confirm/running/done modal via `runAction`. Structurally
	 * matches FuzzyFindModal's FuzzyFindItem plus the fields needed to execute. */
	interface QuickActionItem {
		id: string;
		label: string;
		description?: string;
		icon?: string;
		badge?: { text: string; variant?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal' };
		runAction: BackendQuickAction;
		backendName?: string;
	}

	// ---- Source: backends' self-described admin quick actions ----
	// Only fetched (and only shown in the palette) for admins - these are
	// admin operations, and the /api/backends endpoint is admin-gated
	// server-side regardless.
	let backendsWithActions: { id: string; name: string; quick_actions: BackendQuickAction[] }[] = [];

	async function loadBackendSource() {
		try {
			const response = await getBackends();
			if (response.success) {
				backendsWithActions = (response.data || [])
					.filter((b) => (b.quick_actions?.length ?? 0) > 0)
					.map((b) => ({ id: b.id, name: b.name, quick_actions: b.quick_actions ?? [] }));
			}
		} catch (err: unknown) {
			logger.error('[QuickActions] Failed to load backend quick actions:', err);
		}
	}

	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: if (isAdmin) loadBackendSource();

	// Backend actions are structurally admin-only (`backendsWithActions` is only
	// populated when `isAdmin`, and GET /api/backends is admin-gated), so every
	// item here gets the same "Admin" audience badge as a restricted plugin action.
	$: backendItems = backendsWithActions.flatMap((backend): QuickActionItem[] =>
		backend.quick_actions.map((action) => ({
			id: `backend:${backend.id}:${action.id}`,
			label: action.label,
			description: backend.name,
			icon: action.icon,
			badge: { text: 'Admin', variant: action.danger ? 'danger' : 'warning' as const },
			runAction: action,
			backendName: backend.name
		}))
	);

	// ---- Source: plugin-contributed actions ----
	// Loaded via pluginStore.loadPluginQuickActions() (Sidebar.svelte's
	// onMount), fed by GET /api/plugins/quick-actions - each plugin manifest's
	// `quick_actions[]`. This is the plugin extension point; the palette
	// itself is core.
	$: visiblePluginActions = ($pluginQuickActions || []).filter(
		(action) => !action.require_role || action.require_role === $authStore.user?.account_type
	);

	// `visiblePluginActions` already drops actions the current role can't run.
	// Only ADMIN-restricted actions get an "Admin" badge so admins can tell them
	// apart in the merged palette; everything else stays unbadged.
	$: pluginItems = visiblePluginActions.map(
		(action): QuickActionItem => ({
			id: `plugin:${action.action_id}`,
			label: action.label,
			description: action.plugin_name,
			icon: action.icon || BOLT_PATH,
			badge:
				action.require_role === 'ADMIN' ? { text: 'Admin', variant: 'warning' as const } : undefined,
			runAction: {
				id: action.action_id,
				label: action.label,
				icon: action.icon || BOLT_PATH,
				endpoint: action.endpoint,
				method: action.method,
				confirm: action.confirm
			}
		})
	);

	// ---- Merged palette ----
	$: allItems = [...pluginItems, ...backendItems] as QuickActionItem[];

	// Bind the "A" shortcut (seeded in keybinding_defaults) to open the palette.
	onMount(() => {
		keybindingsStore.registerHandler('open_quick_actions', () => {
			if (allItems.length > 0) showModal = true;
		});
	});
	onDestroy(() => keybindingsStore.unregisterHandler('open_quick_actions'));

	function onSelect(item: QuickActionItem) {
		actionModal.requestAction(item.runAction, item.backendName ?? '');
	}
</script>

{#if allItems.length > 0}
	<div class="flex flex-col items-center gap-1">
		<Tooltip text="Quick actions" kbd={$shortcutLabels['open_quick_actions']} position="right">
			<button
				type="button"
				on:click={() => (showModal = true)}
				class="w-8 h-8 flex items-center justify-center rounded-lg transition-colors
					{runningId
						? 'text-fg bg-surface-2 animate-pulse'
						: 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
				aria-label="Quick actions"
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={BOLT_PATH} />
				</svg>
			</button>
		</Tooltip>
	</div>
{/if}

<FuzzyFindModal
	isOpen={showModal}
	title="Quick Actions"
	placeholder="Search quick actions…"
	emptyMessage="No quick actions available"
	items={allItems}
	showImages={false}
	size="md"
	on:select={(e) => onSelect(e.detail as QuickActionItem)}
	on:close={() => (showModal = false)}
/>

<BackendActionModal bind:this={actionModal} bind:runningId />
