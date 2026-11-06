<script lang="ts">
	import Menu from '$lib/components/ui/Menu.svelte';
	import Kbd from '$lib/components/ui/Kbd.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import { toolKinds } from './registry';
	import type { ToolKindDefinition, ToolKindMenuComponent } from './types';

	let {
		onNewGeneration,
		onOpenTool,
		triggerClass = ''
	}: {
		onNewGeneration: () => void;
		onOpenTool: (tool: string) => void;
		triggerClass?: string;
	} = $props();

	let open = $state(false);
	let newTabKeys = $derived($shortcutLabels['new_tab']);
	let drill = $state.raw<{ kind: ToolKindDefinition; component: ToolKindMenuComponent } | null>(null);

	function pick(close: (restoreFocus?: boolean) => void, action: () => void) {
		close(false);
		action();
	}

	async function choose(close: (restoreFocus?: boolean) => void, kind: ToolKindDefinition) {
		if (kind.menu) {
			const loaded = await kind.menu().catch(() => null);
			const component = loaded && 'default' in loaded ? loaded.default : loaded;
			if (component && open) {
				drill = { kind, component };
				return;
			}
		}
		pick(close, () => onOpenTool(kind.tool));
	}

	function back(): boolean {
		if (!drill) return false;
		drill = null;
		return true;
	}

	$effect(() => {
		if (!open) drill = null;
	});
</script>

<Menu
	label="Add new tab"
	menuLabel={drill ? `Open ${drill.kind.label.toLowerCase()}` : 'New tab'}
	bind:open
	width={drill ? 392 : 340}
	align="left"
	menuAttrs={{ 'data-testid': 'new-tab-menu' }}
	onescape={back}
>
	{#snippet trigger({ open: isOpen, toggle })}
		<Tooltip text="New tab" kbd={newTabKeys} position="bottom" delay={150}>
			<button
				type="button"
				class={triggerClass}
				aria-label="Add new tab"
				aria-haspopup="menu"
				aria-expanded={isOpen}
				onclick={toggle}
			>
				<Icon name="plus" className="h-4 w-4" />
			</button>
		</Tooltip>
	{/snippet}
	{#snippet children({ close })}
		{#if drill}
			{@const Picker = drill.component}
			<Picker close={() => close(false)} back={back} />
		{:else}
			<button type="button" role="menuitem" class="menu-row" onclick={() => pick(close, onNewGeneration)}>
				<Icon name="sparkles" className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-muted" />
				<span class="min-w-0 flex-1">
					<span class="block text-sm text-fg">New generation</span>
					<span class="block text-sm text-fg-subtle">Preset, prompt and Workbench</span>
				</span>
				{#if newTabKeys}<Kbd keys={newTabKeys} />{/if}
			</button>
			{#each $toolKinds as kind (kind.tool)}
				<button
					type="button"
					role="menuitem"
					class="menu-row"
					data-tool={kind.tool}
					aria-haspopup={kind.menu ? 'menu' : undefined}
					onclick={() => choose(close, kind)}
				>
					<Icon name={kind.icon} className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-muted" />
					<span class="min-w-0 flex-1">
						<span class="block text-sm text-fg">{kind.label}</span>
						{#if kind.description}<span class="block text-sm text-fg-subtle">{kind.description}</span>{/if}
					</span>
					{#if kind.menu}<Icon name="chevron-right" className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-subtle" />{/if}
				</button>
			{/each}
			{#if newTabKeys}
				<div class="my-1 h-px bg-line" role="separator"></div>
				<div class="flex items-center gap-2 px-2.5 py-1.5 text-sm text-fg-subtle">
					<Icon name="info" className="h-3.5 w-3.5 flex-shrink-0" />
					<span>{newTabKeys} opens a generation tab directly</span>
				</div>
			{/if}
		{/if}
	{/snippet}
</Menu>

<style>
	.menu-row {
		display: flex;
		width: 100%;
		align-items: flex-start;
		gap: 0.625rem;
		border-radius: 0.25rem;
		padding: 0.5rem 0.625rem;
		text-align: left;
	}

	button.menu-row:hover,
	button.menu-row:focus-visible {
		background: rgb(var(--surface-3));
		outline: none;
	}
</style>
