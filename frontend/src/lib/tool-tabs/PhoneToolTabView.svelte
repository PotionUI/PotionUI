<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import type { ToolTab } from '$lib/types/tabs';
	import ToolTabHost from './ToolTabHost.svelte';
	import TabsSheet from './TabsSheet.svelte';
	import { toolTabActions } from './actions';
	import { toolKinds } from './registry';

	let { tab }: { tab: ToolTab } = $props();

	let sheetOpen = $state(false);
	let actionsStore = $derived(toolTabActions(tab.id));
	let actions = $derived($actionsStore);
	let tabCount = $derived($tabsStore.tabs.length);
	let toolIcon = $derived($toolKinds.find((kind) => kind.tool === tab.tool)?.icon ?? 'extension');
	let edits = $derived(tab.dirty ?? 0);
	let saveProgress = $derived(tab.run ? Math.round(tab.run.progress ?? 0) : null);
</script>

<div class="flex h-full min-h-0 flex-col" data-testid="phone-tool-tab">
	<div class="flex h-14 flex-shrink-0 items-center gap-2 border-b border-line px-3">
		<button
			type="button"
			class="flex min-w-0 flex-1 items-center gap-2 rounded border border-line-strong bg-surface-1 py-1.5 pl-1.5 pr-2.5 text-sm font-semibold text-fg"
			aria-label={edits > 0 ? `Open tabs, ${edits} unsaved ${edits === 1 ? 'edit' : 'edits'}` : 'Open tabs'}
			onclick={() => (sheetOpen = true)}
		>
			<span class="flex h-6 w-6 flex-shrink-0 items-center justify-center overflow-hidden rounded bg-surface-3 text-fg-muted">
				{#if tab.thumbnail}
					<img src={tab.thumbnail} alt="" class="h-full w-full object-cover" />
				{:else}
					<Icon name={toolIcon} className="h-3.5 w-3.5" />
				{/if}
			</span>
			<span class="min-w-0 truncate">{tab.name}</span>
			{#if edits > 0}
				<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning-solid" aria-hidden="true" data-testid="tab-unsaved"></span>
			{/if}
			<span class="font-mono text-sm font-normal tabular-nums text-fg-subtle">{tabCount}</span>
			<Icon name="chevron-down" className="ml-auto h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
		</button>
		<IconButton
			icon="undo"
			label={actions?.undo?.label ?? 'Undo'}
			size="md"
			disabled={!actions?.undo || actions.undo.disabled}
			onclick={() => actions?.undo?.run()}
		/>
		{#if actions?.redo}
			<IconButton
				icon="undo"
				class="-scale-x-100"
				label={actions.redo.label ?? 'Redo'}
				size="md"
				disabled={actions.redo.disabled}
				onclick={() => actions?.redo?.run()}
			/>
		{/if}
		{#if saveProgress !== null}
			<span class="flex h-9 min-w-[3.5rem] items-center justify-center font-mono text-sm tabular-nums text-signal" data-testid="phone-save-progress">{saveProgress}%</span>
		{:else}
			<Button
				variant="primary"
				size="sm"
				disabled={!actions?.save || actions.save.disabled}
				onclick={() => actions?.save?.run()}
			>
				{actions?.save?.label ?? 'Save'}
			</Button>
		{/if}
	</div>
	<div class="min-h-0 flex-1">
		<ToolTabHost {tab} phone />
	</div>
</div>

{#if sheetOpen}
	<TabsSheet onClose={() => (sheetOpen = false)} />
{/if}
