<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { pluginStore, frontendHooks, type PluginHook } from '$lib/stores/plugins';
	import { api } from '$lib/services/api/index';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import { contributionsForSlot, type SlotContribution } from '$lib/extensions/extensionSlots';

	// Props
	export let hookName: string;
	export let position: string | undefined = undefined;
	export let context: Record<string, any> = {};

	// State for loaded components
	let loadedComponents: {
		hook: PluginHook;
		component: any;
	}[] = [];
	let loading = true;
	let error: string | null = null;

	/** Adapts an extension-slot contribution to the `PluginHook` shape so it flows through the same load/mount path as a `hooks.frontend` entry. */
	function contributionToHook(c: SlotContribution): PluginHook {
		return {
			id: -1,
			plugin_id: c.plugin_id,
			hook_name: hookName,
			hook_type: 'frontend',
			component_path: c.component,
			position: undefined,
			sort_order: c.order
		};
	}

	// Both sources - manifest `hooks.frontend` entries and `contributions:` extension-slot
	// entries - merge here, ordered together. Filter hooks by position if provided (slot
	// contributions don't use `position`, so they're unaffected by that filter).
	$: slotContributions = contributionsForSlot(hookName);
	$: relevantHooks = [
		...($frontendHooks[hookName]?.filter((hook) => !position || hook.position === position) || []),
		...$slotContributions.map(contributionToHook)
	].sort((a, b) => a.sort_order - b.sort_order);

	// Dynamic component loading
	async function loadComponent(hook: PluginHook) {
		if (!hook.component_path) return null;

		try {
			// Check if this is a Svelte component (.svelte or .js compiled)
			const isSvelteComponent = hook.component_path.endsWith('.svelte') ||
			                         hook.component_path.endsWith('.js') ||
			                         hook.component_path.endsWith('.mjs');

			if (isSvelteComponent) {
				const component = await resolvePluginComponent(hook.plugin_id, hook.component_path);
				if (!component) return null;

				return {
					type: 'svelte-component',
					component,
					metadata: {
						plugin_id: hook.plugin_id,
						hook_name: hook.hook_name
					}
				};
			} else {
				// Fallback to JSON-based simple components (legacy behavior)
				const response = await fetch(
					`${api.getBaseURL()}/api/plugins/${hook.plugin_id}/component?path=${encodeURIComponent(hook.component_path)}`,
					{
						credentials: 'include',
						headers: {
							'Content-Type': 'application/json',
							...(api.getToken() ? { Authorization: `Bearer ${api.getToken()}` } : {})
						}
					}
				);

				if (!response.ok) {
					logger.warn(
						`Failed to load component for hook ${hook.hook_name} from plugin ${hook.plugin_id}`
					);
					return null;
				}

				const result = await response.json();
				return result.success ? result.data : null;
			}
		} catch (e) {
			logger.error(`Error loading component for hook ${hook.hook_name}:`, e);
			return null;
		}
	}

	// Load all components for this slot
	async function loadAllComponents() {
		loading = true;
		error = null;

		const newComponents = [];

		for (const hook of relevantHooks) {
			const component = await loadComponent(hook);
			if (component) {
				newComponents.push({ hook, component });
			}
		}

		loadedComponents = newComponents;
		loading = false;
	}

	// Reactive loading when hooks change
	$: if (relevantHooks.length > 0) {
		loadAllComponents();
	} else {
		loading = false;
		loadedComponents = [];
	}

	onMount(async () => {
		// Ensure hooks are loaded
		if (Object.keys($frontendHooks).length === 0) {
			await pluginStore.loadFrontendHooks();
		}
	});

	// Handle action button click
	function handleActionClick(component: any, hook: PluginHook) {
		if (component.onClick) {
			try {
				component.onClick(context);
			} catch (e) {
				logger.error(`Error executing plugin action for ${hook.plugin_id}:`, e);
			}
		}
	}
</script>

{#if loading && relevantHooks.length > 0}
	<slot name="loading">
		<!-- Default loading indicator -->
		<span class="plugin-slot-loading">Loading plugins...</span>
	</slot>
{:else if error}
	<slot name="error" {error}>
		<span class="plugin-slot-error">{error}</span>
	</slot>
{:else if loadedComponents.length > 0}
	<div class="plugin-slot" data-hook={hookName} data-position={position}>
		{#each loadedComponents as { hook, component }}
			<div class="plugin-component" data-plugin={hook.plugin_id}>
				<!-- Svelte component (dynamically loaded) -->
				{#if component.type === 'svelte-component'}
					<div class="svelte-component-container">
						<svelte:component this={component.component} {context} {hookName} pluginId={hook.plugin_id} />
					</div>
				{:else if component.type === 'action-button'}
					<!-- For action buttons/simple components -->
					<button
						class="plugin-action-button"
						on:click={() => handleActionClick(component, hook)}
						title={component.tooltip || ''}
						disabled={component.disabled || false}
					>
						{#if component.icon}
							<span class="icon">{component.icon}</span>
						{/if}
						{#if component.label}
							<span class="label">{component.label}</span>
						{/if}
					</button>
				{:else if component.type === 'custom'}
					<!-- Render custom HTML/content -->
					{@html component.html || ''}
				{:else if component.type === 'link'}
					<a
						href={component.href || '#'}
						class="plugin-link"
						target={component.target || '_self'}
						title={component.tooltip || ''}
					>
						{#if component.icon}
							<span class="icon">{component.icon}</span>
						{/if}
						{#if component.label}
							<span class="label">{component.label}</span>
						{/if}
					</a>
				{:else}
					<!-- Fallback for unknown component types -->
					<div class="plugin-unknown">
						<span class="text-sm text-fg-subtle">
							Unknown plugin component type: {component.type}
						</span>
					</div>
				{/if}
			</div>
		{/each}
	</div>
{:else}
	<!-- No plugins for this slot - render nothing or slot content -->
	<slot name="empty"></slot>
{/if}

<style>
	.plugin-slot {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}

	.plugin-slot-loading,
	.plugin-slot-error {
		font-size: 0.75rem;
		opacity: 0.7;
	}

	.plugin-slot-error {
		color: var(--error-color, #ef4444);
	}

	.plugin-action-button,
	.plugin-link {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--border-color, #d1d5db);
		border-radius: 0.375rem;
		background: var(--button-bg, transparent);
		cursor: pointer;
		transition: all 0.15s ease;
		text-decoration: none;
		color: inherit;
	}

	.plugin-action-button:hover,
	.plugin-link:hover {
		background: var(--button-hover-bg, rgba(0, 0, 0, 0.05));
	}

	.plugin-action-button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.plugin-action-button:disabled:hover {
		background: var(--button-bg, transparent);
	}

	.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
	}

	.label {
		font-size: 0.875rem;
		font-weight: 500;
	}

	.plugin-unknown {
		padding: 0.5rem;
		background: var(--warning-bg, #fef3c7);
		border: 1px solid var(--warning-border, #fcd34d);
		border-radius: 0.375rem;
	}
</style>
