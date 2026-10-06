<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
	import type { EntryMenuModel } from '$lib/tools/entryMenu';
	import type { MediaTool, MediaToolContext, MediaToolScope, ToolRunExtra } from '$lib/tools/tools';

	let {
		scope,
		getModel,
		onPick,
		onDelete,
		deleteLabel,
		showDelete = true,
		deleteInRow = true,
		chipSize = 26
	}: {
		scope: MediaToolScope;
		getModel: () => EntryMenuModel;
		onPick: (tool: MediaTool, ctx: MediaToolContext, extra?: ToolRunExtra) => void;
		onDelete: () => void;
		deleteLabel: string;
		showDelete?: boolean;
		deleteInRow?: boolean;
		chipSize?: number;
	} = $props();

	let open = $state(false);
	let sheet = $state(false);
	let anchorPoint = $state<{ x: number; y: number } | null>(null);
	let model = $state<EntryMenuModel | null>(null);

	function touchPrimary(): boolean {
		return typeof window !== 'undefined' && !!window.matchMedia?.('(hover: none)').matches;
	}

	function show(point: { x: number; y: number } | null, asSheet: boolean) {
		model = getModel();
		anchorPoint = point;
		sheet = asSheet;
		open = true;
	}

	export function openAt(x: number, y: number) {
		show({ x, y }, touchPrimary());
	}

	export function openSheet() {
		show(null, true);
	}

	export function close() {
		open = false;
	}

	function toggle() {
		if (open) {
			open = false;
			return;
		}
		show(null, touchPrimary());
	}

	function handlePick(tool: MediaTool, extra?: ToolRunExtra) {
		if (model) onPick(tool, model.ctx, extra);
	}

	let revealClass = $derived(
		open
			? 'opacity-100'
			: 'opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100'
	);
</script>

<div class="absolute top-2 right-2 z-30 flex items-center gap-1" data-entry-actions>
	{#if showDelete && deleteInRow}
		<Tooltip text={deleteLabel} position="bottom" delay={150}>
			<button
				type="button"
				class="bg-black/60 hover:bg-danger-solid text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 [@media(hover:none)]:hidden {revealClass}"
				onclick={(event) => {
					event.stopPropagation();
					event.preventDefault();
					onDelete();
				}}
				aria-label={deleteLabel}
			>
				<Icon name="trash" className="h-3.5 w-3.5" />
			</button>
		</Tooltip>
	{/if}
	<ToolsMenu
		{open}
		{scope}
		variant="entry"
		groups={model?.groups ?? []}
		ctx={model?.ctx ?? null}
		{anchorPoint}
		{sheet}
		header={model && model.selectionCount > 1 ? `${model.selectionCount} selected` : ''}
		title={model?.title ?? ''}
		subtitle={model?.subtitle ?? ''}
		rating={model?.rating ?? 0}
		collections={model?.collections ?? []}
		{chipSize}
		placement="down"
		align="right"
		triggerClass="{revealClass} [@media(hover:none)]:!w-8 [@media(hover:none)]:!h-8"
		onToggle={toggle}
		onClose={() => (open = false)}
		onPick={handlePick}
	/>
</div>
