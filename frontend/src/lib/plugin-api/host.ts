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
import { registerHostUiComponents } from '$lib/plugin-api/hostUi';
import { confirmDialog } from '$lib/stores/confirm';
import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import { registerFieldComponent } from '$lib/fields/registry';
import { registerWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
import { registerModelView } from '$lib/registries/modelViewRegistry';
import { chatToolRendererRegistry } from '$lib/registries/chatToolRendererRegistry';
import { toasts, type ToastType } from '$lib/stores/toast';
import { api } from '$lib/services/api';
import { provideContext, declareMode, onToolApplied } from '$lib/chat/pageContext';
import overlayLayer from '$lib/actions/overlayLayer';
import { acquireLayer, releaseLayer } from '$lib/actions/layerStack';
import { isEditableTarget, isTypingInField } from '$lib/utils/editableTarget';
import { prepareRequestFromSession } from '$lib/generation/prepareRequest';
import { createHostFetchApi, type PluginHostFetchApi } from './hostApi';
import type { PluginImageEditorApi } from '$lib/components/imageEditor/registries';
import { createHostImageEditorApi } from '$lib/tool-tabs/canvasSource';
import { findModelType, modelTypeDefinitions } from '$lib/stores/modelTypes.svelte';
import { modelTypePresentation, type ModelTypePresentation } from '$lib/utils/modelPresentation';
import type { ModelTypeDefinition } from '$lib/types/models';
import { holdKeyboard, releaseKeyboardHold } from '$lib/services/keyboard';
import { registerIcon } from '$lib/utils/IconLibrary';

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

export interface PluginLayersApi {
	overlayLayer: typeof overlayLayer;
	acquire: typeof acquireLayer;
	release: typeof releaseLayer;
}

export interface PluginInputApi {
	isTypingInField: typeof isTypingInField;
	isEditableTarget: typeof isEditableTarget;
}

export interface PluginChatApi {
	/**
	 * Register a provider contributing `{ [key]: provider() }` into the chat
	 * assistant's `context_metadata` on every send (skipped when the provider
	 * returns null). Returns an unregister function.
	 */
	provideContext: typeof provideContext;
	/**
	 * Take over the assistant's resolved mode (overriding the route-prefix
	 * match) for as long as this declaration is active; the most recently
	 * declared mode wins over other active declarations. Returns an
	 * unregister function that restores route-based resolution.
	 */
	declareMode: typeof declareMode;
	/**
	 * Run `handler` with the result of an approved/confirmed tool call named
	 * `toolName`, before any core hardcoded handling for that tool (which is
	 * skipped once a handler is registered). Returns an unregister function.
	 */
	onToolApplied: typeof onToolApplied;
}

export interface PluginKeyboardApi {
	suppress(): () => void;
	resume(): void;
}

export interface PluginRendererEntry {
	pluginId: string;
	asset: string;
}

export interface PluginModelTypesApi {
	list(): ModelTypeDefinition[];
	get(id: string): ModelTypeDefinition | undefined;
	presentation(id: string | null | undefined): ModelTypePresentation;
}

export interface PotionUIHostApi {
	/** Bumped on breaking changes to this surface; plugins may feature-detect against it. */
	version: 1;
	/** Core + plugin-registered mountable components (e.g. `GenerationHistoryModal`), by name. */
	components: MountableComponentRegistry;
	confirm: typeof confirmDialog;
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
	api: PluginHostFetchApi;
	/** Page-scoped hooks into the global chat assistant: context, mode takeover, tool result handling. */
	chat: PluginChatApi;
	keyboard: PluginKeyboardApi;
	layers: PluginLayersApi;
	input: PluginInputApi;
	generation: { prepareRequest: typeof prepareRequestFromSession };
	imageEditor: PluginImageEditorApi;
	modelTypes: PluginModelTypesApi;
	registerIcon: typeof registerIcon;
}

const TOAST_LEVELS: readonly ToastType[] = ['success', 'error', 'info', 'warning'];

function toToastType(level: string): ToastType {
	return (TOAST_LEVELS as readonly string[]).includes(level) ? (level as ToastType) : 'info';
}

/**
 * Assemble `window.__potionui`. Idempotent - safe to call more than once
 * (each call just re-derives the same bindings), matching the other
 * `+layout.svelte` init calls (`refreshPluginExtensions`, etc).
 */
export function initHostApi(): void {
	const w = window as unknown as { __potionui?: Partial<PotionUIHostApi> };
	registerHostUiComponents();
	const host: PotionUIHostApi = {
		version: 1,
		components: getRegistry(),
		confirm: confirmDialog,
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
		api: createHostFetchApi(api),
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
		},
		chat: {
			provideContext,
			declareMode,
			onToolApplied
		},
		keyboard: {
			suppress: () => holdKeyboard(),
			resume: () => releaseKeyboardHold()
		},
		layers: {
			overlayLayer,
			acquire: acquireLayer,
			release: releaseLayer
		},
		input: {
			isTypingInField,
			isEditableTarget
		},
		generation: {
			prepareRequest: prepareRequestFromSession
		},
		imageEditor: createHostImageEditorApi(),
		modelTypes: {
			list: () => modelTypeDefinitions().map((definition) => ({ ...definition })),
			get: (id) => {
				const definition = findModelType(id);
				return definition ? { ...definition } : undefined;
			},
			presentation: (id) => modelTypePresentation(id)
		},
		registerIcon
	};

	w.__potionui = host;
}
