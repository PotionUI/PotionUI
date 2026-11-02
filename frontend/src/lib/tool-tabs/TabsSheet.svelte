<script lang="ts">
	import { onMount } from 'svelte';
	import StudioSheet from '$lib/components/StudioSheet.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { tabSessionDirty } from '$lib/stores/tabSessionDirty';
	import { tabShowsUnsavedChanges } from '$lib/tabs/tabDirty';
	import { requestCloseTab } from '$lib/tabs/closeConfirm';
	import { loadPresetNameMap } from '$lib/stores/presetsCatalog';
	import { isToolTab, type Tab } from '$lib/types/tabs';
	import { toolKinds } from './registry';
	import type { ToolKindDefinition } from './types';

	export let onClose: () => void;

	let presetNames: Record<string, string> = {};

	onMount(() => {
		loadPresetNameMap()
			.then((names) => (presetNames = names))
			.catch(() => {});
	});

	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;

	function toolIconFor(tool: string, kinds: ToolKindDefinition[]): string {
		return kinds.find((kind) => kind.tool === tool)?.icon ?? 'extension';
	}

	function statusLine(tab: Tab, names: Record<string, string>, live: Record<string, boolean>, kinds: ToolKindDefinition[]): string {
		if (isToolTab(tab)) {
			const parts = [kinds.find((kind) => kind.tool === tab.tool)?.label ?? 'Tool'];
			if (tab.run) parts.push(`saving ${Math.round(tab.run.progress ?? 0)}%`);
			const edits = tab.dirty ?? 0;
			if (edits > 0) parts.push(`${edits} ${edits === 1 ? 'edit' : 'edits'}, unsaved`);
			else if (!tab.source) parts.push('empty');
			return parts.join(' · ');
		}
		const preset = tab.selectedPreset ? (names[tab.selectedPreset] ?? null) : null;
		const progress = tab.generation.currentProgress?.progress;
		const state = tab.generation.isGenerating
			? typeof progress === 'number'
				? `generating ${Math.round(progress)}%`
				: 'generating'
			: tabShowsUnsavedChanges(tab, live)
				? 'generation, unsaved'
				: 'generation';
		return preset ? `${preset} · ${state}` : state;
	}

	function select(id: string) {
		tabsStore.setActiveTab(id);
		onClose();
	}

	function newGeneration() {
		tabsStore.addTab();
		onClose();
	}

	function newTool(tool: string) {
		tabsStore.openToolTab(tool, null);
		onClose();
	}
</script>

<StudioSheet maxHeight="88%" ariaLabel="Tabs" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2 pb-1">
			<span class="text-lg font-semibold text-fg">Tabs</span>
			<span class="font-mono text-sm tabular-nums text-fg-subtle">{tabs.length}</span>
		</div>
	</svelte:fragment>

	<div class="flex flex-col gap-1 pb-4" data-testid="tabs-sheet">
		<ul class="flex flex-col gap-1" aria-label="Open tabs">
			{#each tabs as tab (tab.id)}
				{@const active = tab.id === activeTabId}
				<li
					class="tab-row flex items-center gap-3 rounded-lg border-l-2 py-1.5 pl-2 pr-1 {active
						? 'border-signal bg-surface-2'
						: 'border-transparent'}"
					data-testid="tabs-sheet-row"
					data-kind={tab.kind}
				>
					<button
						type="button"
						class="flex min-w-0 flex-1 items-center gap-3 text-left"
						aria-current={active ? 'page' : undefined}
						onclick={() => select(tab.id)}
					>
						<span class="flex h-9 w-9 flex-shrink-0 items-center justify-center overflow-hidden rounded border border-line bg-surface-2 text-fg-muted">
							{#if isToolTab(tab)}
								{#if tab.thumbnail}
									<img src={tab.thumbnail} alt="" class="h-full w-full object-cover" />
								{:else}
									<Icon name={toolIconFor(tab.tool, $toolKinds)} className="h-4 w-4" />
								{/if}
							{:else if tab.color}
								<span class="h-2 w-2 rounded-full" style="background-color: {tab.color};"></span>
							{:else}
								<Icon name="sparkles" className="h-4 w-4" />
							{/if}
						</span>
						<span class="min-w-0 flex-1">
							<span class="block truncate text-sm font-medium text-fg">{tab.name}</span>
							<span class="block truncate text-sm text-fg-muted">{statusLine(tab, presetNames, $tabSessionDirty, $toolKinds)}</span>
						</span>
					</button>
					{#if tabs.length > 1}
						<button
							type="button"
							class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded text-fg-subtle hover:bg-surface-3 hover:text-fg"
							aria-label="Close {tab.name}"
							onclick={() => requestCloseTab(tab.id)}
						>
							<Icon name="close" className="h-4 w-4" />
						</button>
					{/if}
				</li>
			{/each}
		</ul>

		<p class="mt-4 mb-2 font-mono text-xs uppercase tracking-wider text-fg-subtle">New tab</p>
		<div class="grid grid-cols-3 gap-2" data-testid="tabs-sheet-new">
			<button type="button" class="new-kind" onclick={newGeneration}>
				<Icon name="sparkles" className="h-4 w-4" />
				<span>Generation</span>
			</button>
			{#each $toolKinds as kind (kind.tool)}
				<button type="button" class="new-kind" data-tool={kind.tool} onclick={() => newTool(kind.tool)}>
					<Icon name={kind.icon} className="h-4 w-4" />
					<span class="truncate">{kind.label}</span>
				</button>
			{/each}
		</div>
	</div>
</StudioSheet>

<style>
	.new-kind {
		display: flex;
		min-width: 0;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.375rem;
		height: 3.75rem;
		padding: 0 0.5rem;
		border: 1px solid rgb(var(--line));
		border-radius: 0.25rem;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
		font-size: 0.8125rem;
	}

	.new-kind:hover {
		background: rgb(var(--surface-3));
		border-color: rgb(var(--line-hover));
	}
</style>
