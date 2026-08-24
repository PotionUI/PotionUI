<script lang="ts">
	import { onMount } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { UserToolPreference } from '$lib/types/llm';

	// Persistent, per-user tool opt-out (see /api/llm/toolset/preferences) —
	// distinct from the session-scoped enable/disable toggles in the Tools
	// popover: this one survives across sessions and is what a config's
	// `locked` flag overrides. Tells the caller when the list changes so the
	// Tools popover's admin-disabled filtering and lock badges stay in sync.
	// Tool governance is per LLM config (the same tool can be locked in one
	// config, not another), so `llmConfigId` scopes both the visible list and
	// what a lock/disable check is evaluated against - the composer's active
	// config.
	export let llmConfigId: string | null = null;
	export let onClose: () => void;
	export let onChanged: (() => void) | undefined = undefined;

	let tools: UserToolPreference[] = [];
	let loading = true;
	let error: string | null = null;
	let saving: Record<string, boolean> = {};

	function toolLabel(tool: UserToolPreference): string {
		return tool.label || tool.name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	onMount(loadPreferences);

	async function loadPreferences() {
		if (!llmConfigId) {
			loading = false;
			error = 'No active LLM configuration to look up tool preferences for.';
			return;
		}
		loading = true;
		error = null;
		try {
			const response = await api.getMyToolsetPreferences(llmConfigId);
			if (response.success && response.data) {
				tools = response.data;
			} else {
				error = 'Failed to load tool preferences';
			}
		} catch (err) {
			logger.error('Failed to load tool preferences:', err);
			error = 'Failed to load tool preferences';
		} finally {
			loading = false;
		}
	}

	async function toggleTool(tool: UserToolPreference) {
		if (tool.locked || !llmConfigId) return;
		const nextDisabled = !tool.disabled_by_user;
		saving = { ...saving, [tool.name]: true };
		try {
			const response = await api.updateMyToolPreference(tool.name, nextDisabled, llmConfigId);
			if (response.success) {
				tools = tools.map((t) => (t.name === tool.name ? { ...t, disabled_by_user: nextDisabled } : t));
				onChanged?.();
			}
		} catch (err) {
			logger.error(`Failed to update tool preference for ${tool.name}:`, err);
		} finally {
			saving = { ...saving, [tool.name]: false };
		}
	}
</script>

<!-- Backdrop -->
<div
	class="fixed inset-0 z-40"
	role="button"
	tabindex="-1"
	aria-label="Close my tools"
	on:click={onClose}
	on:keydown={(e) => { if (e.key === 'Escape') onClose(); }}
></div>

<!-- Slide-out panel -->
<div
	class="fixed top-2 right-2 bottom-2 z-50 w-[92vw] max-w-[400px] flex flex-col bg-surface-1 border border-line rounded-xl shadow-overlay overflow-hidden"
	role="dialog"
	aria-label="My tools"
>
	<!-- Header -->
	<div class="flex items-center gap-2 px-4 py-3 border-b border-line flex-shrink-0">
		<Icon name="shield" className="w-4 h-4 text-signal flex-shrink-0" />
		<h2 class="text-sm font-semibold text-fg">My Tools</h2>
		<button
			type="button"
			title="Close"
			class="ml-auto p-1.5 text-fg-subtle hover:text-fg-muted hover:bg-surface-2 rounded transition-colors"
			on:click={onClose}
		>
			<Icon name="close" className="w-4 h-4" />
		</button>
	</div>

	<!-- Body -->
	<div class="flex-1 overflow-y-auto p-3 space-y-1 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent">
		{#if error}
			<div class="bg-surface-2 border border-danger/25 rounded px-3 py-2 text-xs text-danger">{error}</div>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-10">
				<Spinner />
			</div>
		{:else if tools.length === 0}
			<div class="px-2 py-6 text-xs text-fg-subtle text-center">No tools available</div>
		{:else}
			<p class="text-2xs text-fg-subtle px-1 pb-2">
				Turn a tool off here and it stays off across every session, until you turn it back on.
			</p>
			{#each tools as tool (tool.name)}
				<div class="flex items-start gap-2 px-2 py-2 rounded hover:bg-surface-2 transition-colors" data-testid="my-tool-row" data-tool={tool.name}>
					<div class="flex-1 min-w-0">
						<div class="flex items-center gap-1.5 min-w-0">
							<span class="text-xs font-medium text-fg-muted truncate">{toolLabel(tool)}</span>
							{#if tool.locked}
								<span title="Enabled for everyone by the administrator">
									<Icon name="shield" className="w-3 h-3 text-fg-subtle flex-shrink-0" />
								</span>
							{/if}
						</div>
						{#if tool.user_description}
							<div class="text-[10px] text-fg-subtle mt-0.5 leading-snug">{tool.user_description}</div>
						{/if}
					</div>
					<button
						type="button"
						role="switch"
						aria-checked={!tool.disabled_by_user}
						disabled={tool.locked || !!saving[tool.name]}
						title={tool.locked ? 'Enabled for everyone by the administrator' : tool.disabled_by_user ? 'Turn on' : 'Turn off'}
						class="mt-0.5 flex-shrink-0 relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-150 {tool.disabled_by_user ? 'bg-surface-3' : 'bg-signal'} {tool.locked ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}"
						on:click={() => toggleTool(tool)}
					>
						<span
							class="inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform duration-150 {tool.disabled_by_user ? 'translate-x-0.5' : 'translate-x-4'}"
						></span>
					</button>
				</div>
			{/each}
		{/if}
	</div>
</div>
