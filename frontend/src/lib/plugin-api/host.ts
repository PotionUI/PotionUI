/**
 * The formal `window.__potionui` plugin host API surface.
 *
 * This is what every plugin-hosted Svelte component receives as its `host`
 * prop (see `FormField.svelte`, the plugin page host at
 * `routes/plugins/[pluginId]/[...path]/+page.svelte`, and `PluginSlot.svelte`)
 * and what any plugin script can reach directly via `window.__potionui`.
 * `initHostApi()` assembles it once from `+layout.svelte` on mount, replacing
 * the ad-hoc assembly that used to live inline in
 * `plugin-api/componentRegistry.ts` (`initGlobalRegistry`) - that file now
 * only owns the mountable-component map (`registerComponent`/`getRegistry`).
 */
import * as formReactions from '$lib/form/reactions';
import { getRegistry, type MountableComponentRegistry } from '$lib/plugin-api/componentRegistry';
import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import { registerFieldComponent } from '$lib/fields/registry';
import { registerWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
import { registerModelView } from '$lib/registries/modelViewRegistry';
import { chatToolRendererRegistry } from '$lib/registries/chatToolRendererRegistry';
import { toasts, type ToastType } from '$lib/stores/toast';
import { api } from '$lib/services/api';

export type RendererKind = 'history.artifact' | 'workbench.file' | 'model.view' | 'chat.tool';

export type NotificationLevel = 'success' | 'error' | 'info' | 'warning';

export interface PluginNotifyInput {
	level: NotificationLevel;
	title: string;
	message?: string;
	category?: string;
	/** Notification type key (e.g. 'generation.completed'); gates per-user preferences. */
	type?: string;
	transient?: boolean;
	showToast?: boolean;
	metadata?: Record<string, unknown> | null;
}

export interface PluginNotificationsApi {
	/** Show a local-only transient toast (no persistence, no server round-trip). */
	toast(level: NotificationLevel, message: string, duration?: number): void;
	/** Persist + push a notification to the current user via `POST /api/notifications`. */
	notify(input: PluginNotifyInput): Promise<void>;
}

export interface PluginRendererEntry {
	pluginId: string;
	asset: string;
}

export interface PotionUIHostApi {
	/** Bumped on breaking changes to this surface; plugins may feature-detect against it. */
	version: 1;
	/** Core + plugin-registered mountable components (e.g. `GenerationHistoryModal`), by name. */
	components: MountableComponentRegistry;
	/** The shared `when`/`then` form reaction engine - identical evaluation on core forms and plugin-contributed ones. */
	formReactions: typeof formReactions;
	/** Register a plugin-provided form field type component (roadmap A4). */
	registerFieldComponent: typeof registerFieldComponent;
	/** Register a plugin-provided renderer for one of the plugin-facing renderer kinds (roadmap A5). */
	registerRenderer: (kind: RendererKind, key: string, entry: PluginRendererEntry) => void;
	/** Grouped access to the individual renderer registries, for plugins that want to `.get`/`.list` instead of just registering. */
	registries: {
		generationMessage: typeof generationMessageRegistry;
		artifactRenderer: typeof artifactRendererRegistry;
		workbenchFile: { register: typeof registerWorkbenchFileRenderer };
		modelView: { register: typeof registerModelView };
		chatTool: typeof chatToolRendererRegistry;
	};
	/** User notification surface: local toasts and persisted notifications. */
	notifications: PluginNotificationsApi;
}

const TOAST_LEVELS: readonly ToastType[] = ['success', 'error', 'info', 'warning'];

function toToastType(level: string): ToastType {
	return (TOAST_LEVELS as readonly string[]).includes(level) ? (level as ToastType) : 'info';
}

/**
 * Assemble `window.__potionui`. Idempotent - safe to call more than once
 * (each call just re-derives the same bindings), matching the other
 * `+layout.svelte` init calls (`initFieldTypes`, `initExtensions`, etc).
 */
export function initHostApi(): void {
	const w = window as unknown as { __potionui?: Partial<PotionUIHostApi> };
	const host: PotionUIHostApi = {
		version: 1,
		components: getRegistry(),
		formReactions,
		registerFieldComponent,
		registerRenderer(kind, key, entry) {
			switch (kind) {
				case 'history.artifact':
					artifactRendererRegistry.register(key, entry);
					break;
				case 'workbench.file':
					registerWorkbenchFileRenderer(key, entry);
					break;
				case 'model.view':
					registerModelView(entry.pluginId, key, entry.asset);
					break;
				case 'chat.tool':
					chatToolRendererRegistry.register(key, entry);
					break;
			}
		},
		registries: {
			generationMessage: generationMessageRegistry,
			artifactRenderer: artifactRendererRegistry,
			workbenchFile: { register: registerWorkbenchFileRenderer },
			modelView: { register: registerModelView },
			chatTool: chatToolRendererRegistry
		},
		notifications: {
			toast(level, message, duration) {
				toasts.show(toToastType(level), message, { duration });
			},
			async notify(input) {
				await api.createNotification({
					level: input.level,
					title: input.title,
					message: input.message,
					category: input.category,
					type: input.type,
					transient: input.transient,
					show_toast: input.showToast,
					metadata: input.metadata
				});
			}
		}
	};

	w.__potionui = host;
}
