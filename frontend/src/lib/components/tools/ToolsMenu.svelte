<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { MediaTool, MediaToolGroup, MediaToolScope } from '$lib/tools/tools';

	let {
		open,
		scope,
		groups,
		onToggle,
		onClose,
		onPick
	}: {
		open: boolean;
		/** The page this menu is mounted on - `groups` is expected to already be
		 * scoped to it (via `listToolGroups`); kept as its own prop so the menu
		 * itself never has to re-derive it. */
		scope: MediaToolScope;
		groups: MediaToolGroup[];
		onToggle: () => void;
		onClose: () => void;
		onPick: (tool: MediaTool) => void;
	} = $props();

	function handleKeydown(event: KeyboardEvent) {
		if (open && event.key === 'Escape') onClose();
	}

	// Unavailable tools stay focusable and keep their native tooltip: a
	// `disabled` button swallows the pointer events `title` needs, so the
	// reason for the greying would never be readable.
	function pick(tool: MediaTool, enabled: boolean) {
		if (!enabled) return;
		onClose();
		onPick(tool);
	}

	function hintFor(tool: MediaTool, enabled: boolean, reason: string | undefined): string {
		return (enabled ? tool.description : reason) ?? '';
	}
</script>

<svelte:window on:keydown={handleKeydown} />

<div class="relative" data-tools-menu-scope={scope}>
	<button
		type="button"
		class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5"
		aria-haspopup="menu"
		aria-expanded={open}
		onclick={onToggle}
	>
		<Icon name="wand" className="w-4 h-4" />
		Tools
		<Icon name="chevron-up" className="w-3 h-3" />
	</button>

	{#if open}
		<div
			class="absolute bottom-full mb-2 right-0 min-w-[16rem] max-h-80 overflow-y-auto bg-surface-1 border border-line-strong rounded-lg shadow-overlay p-1 flex flex-col"
			role="menu"
			data-tools-menu
		>
			{#each groups as group (group.category.id)}
				<div role="group" aria-label={group.category.label} class="flex flex-col">
					<span class="px-3 pt-2 pb-1 text-2xs uppercase tracking-[0.07em] text-fg-subtle">
						{group.category.label}
					</span>
					{#each group.tools as { tool, availability } (tool.id)}
						<button
							type="button"
							role="menuitem"
							class="px-3 py-1.5 text-sm text-left rounded transition-colors flex items-center gap-2 {availability.enabled
								? 'text-fg-muted hover:text-fg hover:bg-surface-2'
								: 'text-fg-muted opacity-40 cursor-not-allowed'}"
							aria-disabled={!availability.enabled}
							title={hintFor(tool, availability.enabled, availability.reason)}
							data-tool={tool.id}
							onclick={() => pick(tool, availability.enabled)}
						>
							<Icon name={tool.icon} className="w-4 h-4 flex-shrink-0" />
							{tool.label}
						</button>
					{/each}
				</div>
			{/each}

			{#if groups.length === 0}
				<p class="px-3 py-2 text-sm text-fg-subtle">No tools for this selection.</p>
			{/if}
		</div>
	{/if}
</div>
