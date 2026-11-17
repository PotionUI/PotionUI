<script lang="ts">
	import { untrack } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import FrameSheet from './FrameSheet.svelte';
	import { foldGroups } from '$lib/editors/railFold';
	import { lastInGroups, rememberInGroup, shownInGroup } from '$lib/editors/groupMemory';
	import type { RailGroup, RailTool } from '$lib/tool-tabs/frame/types';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';
	import { RAIL_ICON_WIDTH } from '$lib/tool-tabs/frame/layout';

	let {
		groups,
		active = null,
		pending = [],
		applied = [],
		variant = 'rail',
		label = 'Tools',
		memoryKey = null,
		groupedStrip = false,
		onselect
	}: {
		groups: RailGroup[];
		active?: string | null;
		pending?: string[];
		applied?: string[];
		variant?: 'rail' | 'icons' | 'strip';
		label?: string;
		memoryKey?: string | null;
		groupedStrip?: boolean;
		onselect: (id: string) => void;
	} = $props();

	const HOVER_OPEN_MS = 220;
	const HOVER_CLOSE_MS = 160;
	const EDGE = 8;

	let root = $state<HTMLElement>();
	let railHeight = $state(0);
	let last = $state<Record<string, string>>(untrack(() => (memoryKey ? lastInGroups(memoryKey) : {})));
	let flyGroup = $state<string | null>(null);
	let flyHi = $state<string | null>(null);
	let flyPos = $state<{ left: number; top: number } | null>(null);
	let flyEl = $state<HTMLElement>();
	let pinned = false;
	let sheetGroup = $state<string | null>(null);
	let openTimer: ReturnType<typeof setTimeout> | null = null;
	let closeTimer: ReturnType<typeof setTimeout> | null = null;

	let strip = $derived(variant === 'strip');
	let icons = $derived(variant === 'icons');
	const frame = getFrameContext();
	let joined = $derived(icons && frame?.panelSide === 'left');
	let folded = $derived(
		icons ? new Set(foldGroups(groups.map((group) => ({ id: group.id, count: group.tools.length, fold: group.fold })), railHeight)) : new Set<string>()
	);
	let flyEntry = $derived(flyGroup ? (groups.find((group) => group.id === flyGroup) ?? null) : null);
	let sheetEntry = $derived(sheetGroup ? (groups.find((group) => group.id === sheetGroup) ?? null) : null);

	$effect(() => {
		const node = root;
		if (!node || !icons) return;
		const read = () => {
			const next = Math.round(node.clientHeight);
			if (Math.abs(next - untrack(() => railHeight)) >= 1) railHeight = next;
		};
		read();
		const observer = new ResizeObserver(read);
		observer.observe(node);
		return () => observer.disconnect();
	});

	$effect(() => {
		const id = active;
		if (!id) return;
		untrack(() => {
			const group = groups.find((entry) => entry.tools.some((tool) => tool.id === id));
			if (group) remember(group.id, id);
		});
	});

	$effect(() => {
		if (flyGroup && !folded.has(flyGroup) && !strip) closeFly(false);
	});

	$effect(() => () => {
		if (openTimer) clearTimeout(openTimer);
		if (closeTimer) clearTimeout(closeTimer);
	});

	function remember(group: string, id: string) {
		if (last[group] === id) return;
		last = { ...last, [group]: id };
		if (memoryKey) rememberInGroup(memoryKey, group, id);
	}

	function unavailable(tool: RailTool): boolean {
		return !!tool.disabled || !!tool.later;
	}

	function reason(tool: RailTool): string | null {
		if (tool.reason) return tool.reason;
		return tool.later ? 'Later' : null;
	}

	function ariaLabel(tool: RailTool): string {
		const parts = [tool.label];
		if (applied.includes(tool.id)) parts.push('applied');
		if (pending.includes(tool.id)) parts.push('pending');
		const why = unavailable(tool) ? reason(tool) : null;
		if (why) parts.push(why);
		return parts.join(', ');
	}

	function tip(tool: RailTool) {
		if (unavailable(tool)) return { text: reason(tool), position: strip ? ('top' as const) : ('right' as const) };
		if (!icons) return null;
		return { text: tool.label, position: 'right' as const, kbd: tool.keys };
	}

	function shownTool(group: RailGroup): RailTool {
		const id = shownInGroup(
			group.tools.map((tool) => tool.id),
			active,
			last[group.id]
		);
		return group.tools.find((tool) => tool.id === id) ?? group.tools[0];
	}

	function groupApplied(group: RailGroup): boolean {
		return group.tools.some((tool) => applied.includes(tool.id));
	}

	function groupPending(group: RailGroup): boolean {
		return group.tools.some((tool) => pending.includes(tool.id));
	}

	function pick(tool: RailTool, group?: RailGroup) {
		if (unavailable(tool)) return;
		if (group) remember(group.id, tool.id);
		onselect(tool.id);
	}

	function place(button: HTMLElement) {
		const rect = button.getBoundingClientRect();
		flyPos = { left: rect.right + 6, top: rect.top };
	}

	function openFly(group: RailGroup, button: HTMLElement, focusFirst: boolean) {
		if (openTimer) clearTimeout(openTimer);
		if (closeTimer) clearTimeout(closeTimer);
		openTimer = null;
		closeTimer = null;
		flyGroup = group.id;
		flyHi = shownTool(group).id;
		place(button);
		if (focusFirst) {
			queueMicrotask(() => {
				flyEl?.querySelector<HTMLButtonElement>(`[data-flyout-item="${flyHi}"]`)?.focus();
			});
		}
	}

	function closeFly(refocus: boolean) {
		const group = flyGroup;
		flyGroup = null;
		flyHi = null;
		flyPos = null;
		pinned = false;
		if (openTimer) clearTimeout(openTimer);
		if (closeTimer) clearTimeout(closeTimer);
		openTimer = null;
		closeTimer = null;
		if (refocus && group) root?.querySelector<HTMLButtonElement>(`button[data-group-button="${group}"]`)?.focus();
	}

	function onGroupClick(group: RailGroup, event: MouseEvent) {
		const button = event.currentTarget as HTMLElement;
		if (strip) {
			sheetGroup = group.id;
			return;
		}
		pinned = true;
		if (flyGroup === group.id && event.detail > 0) {
			closeFly(false);
			return;
		}
		openFly(group, button, event.detail === 0);
	}

	function onGroupEnter(group: RailGroup, event: PointerEvent) {
		if (event.pointerType === 'touch' || strip) return;
		const button = event.currentTarget as HTMLElement;
		if (closeTimer) clearTimeout(closeTimer);
		closeTimer = null;
		if (flyGroup && flyGroup !== group.id && !pinned) {
			openFly(group, button, false);
			return;
		}
		if (flyGroup === group.id) return;
		if (openTimer) clearTimeout(openTimer);
		openTimer = setTimeout(() => {
			openTimer = null;
			pinned = false;
			openFly(group, button, false);
		}, HOVER_OPEN_MS);
	}

	function onGroupLeave(event: PointerEvent) {
		if (event.pointerType === 'touch') return;
		if (openTimer) clearTimeout(openTimer);
		openTimer = null;
		scheduleClose();
	}

	function scheduleClose() {
		if (pinned || !flyGroup) return;
		if (closeTimer) clearTimeout(closeTimer);
		closeTimer = setTimeout(() => {
			closeTimer = null;
			if (!pinned) closeFly(false);
		}, HOVER_CLOSE_MS);
	}

	function holdFly() {
		if (closeTimer) clearTimeout(closeTimer);
		closeTimer = null;
	}

	function onGroupKey(group: RailGroup, event: KeyboardEvent) {
		if (strip) return;
		if (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			event.stopPropagation();
			pinned = true;
			openFly(group, event.currentTarget as HTMLElement, true);
		}
	}

	function onFlyKey(event: KeyboardEvent) {
		if (!flyEntry) return;
		const items = Array.from(flyEl?.querySelectorAll<HTMLButtonElement>('button[data-flyout-item]') ?? []);
		const at = items.indexOf(document.activeElement as HTMLButtonElement);
		const move = (index: number) => {
			event.preventDefault();
			event.stopPropagation();
			items[index]?.focus();
			flyHi = items[index]?.dataset.flyoutItem ?? flyHi;
		};
		if (event.key === 'ArrowDown') move((at + 1) % items.length);
		else if (event.key === 'ArrowUp') move((at - 1 + items.length) % items.length);
		else if (event.key === 'Home') move(0);
		else if (event.key === 'End') move(items.length - 1);
		else if (event.key === 'ArrowLeft') {
			event.preventDefault();
			event.stopPropagation();
			closeFly(true);
		} else if (event.key === 'Tab') closeFly(false);
	}

	function chooseFromFly(tool: RailTool) {
		const group = flyEntry;
		if (unavailable(tool) || !group) return;
		closeFly(false);
		pick(tool, group);
		queueMicrotask(() => root?.querySelector<HTMLButtonElement>(`button[data-group-button="${group.id}"]`)?.focus());
	}

	function chooseFromSheet(tool: RailTool) {
		const group = sheetEntry;
		if (unavailable(tool) || !group) return;
		sheetGroup = null;
		pick(tool, group);
	}

	function onkeydown(event: KeyboardEvent) {
		const keys = strip ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown'];
		if (![...keys, 'Home', 'End'].includes(event.key) || !root) return;
		const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('button[data-tool], button[data-group-button]'));
		const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
		if (at < 0) return;
		event.preventDefault();
		const next =
			event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : event.key === keys[0] ? Math.max(0, at - 1) : Math.min(buttons.length - 1, at + 1);
		buttons[next].focus();
	}

	$effect(() => {
		if (!strip || !root || !active) return;
		const target = active;
		Array.from(root.querySelectorAll<HTMLElement>('button[data-tool], button[data-group-button]'))
			.find((button) => button.dataset.tool === target || button.dataset.groupMembers?.split(' ').includes(target))
			?.scrollIntoView({ block: 'nearest', inline: 'center' });
	});

	function flyStyle(pos: { left: number; top: number } | null): string {
		if (!pos) return 'left: 0; top: 0; visibility: hidden;';
		const height = flyEl?.offsetHeight ?? 0;
		const top = Math.max(EDGE, Math.min(pos.top - 6, window.innerHeight - height - EDGE));
		return `left: ${pos.left}px; top: ${top}px;`;
	}
</script>

{#snippet dot(id: string, on: boolean)}
	{#if pending.includes(id)}
		<span class="absolute right-1.5 top-1 h-1.5 w-1.5 rounded-full bg-warning-solid" aria-hidden="true" data-tool-pending></span>
	{:else if on}
		<span class="absolute right-1.5 top-1 h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" data-tool-applied></span>
	{/if}
{/snippet}


<div
	bind:this={root}
	class={strip
		? 'strip flex h-16 flex-none gap-0.5 overflow-x-auto border-t border-line bg-surface-1 px-1.5 py-1'
		: icons
			? joined
				? 'rail flex min-h-0 flex-none flex-col items-center gap-0.5 overflow-hidden border-r-2 border-line bg-surface-1/50 px-[3px] py-2'
				: 'rail flex min-h-0 flex-none flex-col items-center gap-0.5 overflow-hidden border-r border-line bg-surface-1 px-1.5 py-2'
			: 'rail flex w-[76px] flex-none flex-col gap-0.5 overflow-y-auto border-r border-line bg-surface-1 px-1.5 py-2'}
	style={icons ? `width: ${RAIL_ICON_WIDTH}px` : undefined}
	role="toolbar"
	aria-orientation={strip ? 'horizontal' : 'vertical'}
	aria-label={label}
	tabindex="-1"
	{onkeydown}
	data-tool-rail={variant}
	data-rail-joined={joined || undefined}
	data-rail-folded={icons ? [...folded].join(' ') : undefined}
>
	{#each groups as group, g (group.id)}
		{#if icons}
			{#if g > 0}
				<div class="my-1.5 h-px w-6 flex-none bg-line" role="separator" aria-hidden="true"></div>
			{/if}
		{:else if !strip}
			<div
				class="text-center font-mono text-sm uppercase tracking-[0.06em] text-fg-subtle {g === 0 ? 'pb-1 pt-0.5' : 'pb-1 pt-2.5'}"
				role="presentation"
			>
				{group.label}
			</div>
		{/if}
		{#if (icons && folded.has(group.id)) || (strip && groupedStrip && group.tools.length > 1)}
			{@const shown = shownTool(group)}
			{@const inside = group.tools.some((tool) => tool.id === active)}
			<button
				type="button"
				class="tool relative flex items-center rounded text-center text-sm leading-[14px] {icons
					? `h-10 flex-none justify-center ${joined ? 'w-11' : 'w-10'}`
					: 'min-w-16 flex-none flex-col justify-center gap-[3px] px-1.5 pb-1.5 pt-[7px]'} {shown.ai
					? `ai-ring ${inside ? 'ai-ring--on text-fg' : 'text-fg-muted hover:text-fg'}`
					: inside
						? `is-on text-signal ${joined ? 'bg-surface-1' : 'bg-signal/10'}`
						: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
				aria-haspopup={strip ? 'dialog' : 'menu'}
				aria-expanded={strip ? sheetGroup === group.id : flyGroup === group.id}
				aria-pressed={inside}
				aria-label="{group.label}, {shown.label}{groupApplied(group) ? ', applied' : ''}"
				data-group-button={group.id}
				data-group-members={group.tools.map((tool) => tool.id).join(' ')}
				data-shown={shown.id}
				use:tooltip={icons ? { text: group.label, position: 'right' } : null}
				onclick={(event) => onGroupClick(group, event)}
				onkeydown={(event) => onGroupKey(group, event)}
				onpointerenter={(event) => onGroupEnter(group, event)}
				onpointerleave={onGroupLeave}
			>
				<Icon name={shown.icon} className="h-[18px] w-[18px] flex-none" strokeWidth={1.7} />
				{#if !icons}
					<span class="max-w-full whitespace-nowrap">{group.label}</span>
				{/if}
				<span class="corner" aria-hidden="true"></span>
				{#if groupPending(group)}
					<span class="absolute right-1.5 top-1 h-1.5 w-1.5 rounded-full bg-warning-solid" aria-hidden="true" data-tool-pending></span>
				{:else if groupApplied(group)}
					<span class="absolute right-1.5 top-1 h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" data-tool-applied></span>
				{/if}
			</button>
		{:else}
			{#each group.tools as tool (tool.id)}
				{@const on = tool.id === active}
				{@const off = unavailable(tool)}
				<button
					type="button"
					class="tool relative flex items-center rounded text-center text-sm leading-[14px] {icons
						? `h-10 flex-none justify-center ${joined ? 'w-11' : 'w-10'}`
						: `flex-col gap-[3px] px-0.5 pb-1.5 pt-[7px] ${strip ? 'min-w-16 flex-none justify-center px-1.5' : ''}`} {tool.ai && !off
						? `ai-ring ${on ? 'ai-ring--on text-fg' : 'text-fg-muted hover:text-fg'}`
						: on
						? `is-on text-signal ${joined ? 'bg-surface-1' : 'bg-signal/10'}`
						: off
							? 'cursor-not-allowed text-fg-disabled'
							: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					aria-pressed={on}
					aria-disabled={off || undefined}
					aria-label={ariaLabel(tool)}
					data-tool={tool.id}
					use:tooltip={tip(tool)}
					onclick={() => pick(tool, group)}
				>
					<Icon name={tool.icon} className="h-[18px] w-[18px] flex-none" strokeWidth={1.7} />
					{#if !icons}
						<span class="max-w-full {strip ? 'whitespace-nowrap' : 'truncate'}">{tool.label}</span>
					{/if}
					{@render dot(tool.id, applied.includes(tool.id))}
				</button>
			{/each}
		{/if}
	{/each}
</div>

{#if flyEntry}
	<div
		bind:this={flyEl}
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: () => closeFly(true), ignore: [root] }}
		role="menu"
		aria-label={flyEntry.label}
		tabindex="-1"
		class="fixed w-[252px] rounded-xl border border-line-strong bg-surface-2 p-1 shadow-overlay"
		style={flyStyle(flyPos)}
		onkeydown={onFlyKey}
		onpointerenter={holdFly}
		onpointerleave={scheduleClose}
		data-rail-flyout={flyEntry.id}
	>
		<div class="px-2.5 pb-1 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">{flyEntry.label}</div>
		{#each flyEntry.tools as tool (tool.id)}
			{@const off = unavailable(tool)}
			<button
				type="button"
				role="menuitemradio"
				aria-checked={tool.id === active}
				aria-disabled={off || undefined}
				tabindex="-1"
				class="flex min-h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-base hover:bg-surface-3 focus:bg-surface-3 focus:outline-none focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal {flyHi === tool.id ? 'bg-surface-3' : ''} {off ? 'cursor-not-allowed text-fg-disabled' : tool.id === active ? 'text-signal' : 'text-fg'}"
				data-flyout-item={tool.id}
				onclick={() => chooseFromFly(tool)}
			>
				{#if tool.ai}
					<span class="ai-mark grid h-6 w-6 flex-none place-items-center rounded" data-ai-mark><Icon name={tool.icon} className="h-4 w-4 flex-none {tool.id === active ? 'text-signal' : 'text-fg-muted'}" /></span>
				{:else}
					<Icon name={tool.icon} className="h-4 w-4 flex-none {tool.id === active ? 'text-signal' : 'text-fg-muted'}" />
				{/if}
				<span class="min-w-0 flex-1 truncate">{tool.label}</span>
				{#if off && reason(tool)}<span class="sr-only">, {reason(tool)}</span>{/if}
				{#if pending.includes(tool.id)}
					<span class="h-1.5 w-1.5 flex-none rounded-full bg-warning-solid" aria-hidden="true"></span>
				{:else if applied.includes(tool.id)}
					<span class="h-1.5 w-1.5 flex-none rounded-full bg-signal" aria-label="Applied" data-flyout-applied></span>
				{/if}
			</button>
		{/each}
	</div>
{/if}

{#if sheetEntry}
	<FrameSheet label={sheetEntry.label} onclose={() => (sheetGroup = null)}>
		{#snippet header()}
			<span class="text-lg font-semibold text-fg">{sheetEntry.label}</span>
			<span class="flex-1"></span>
			<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{sheetEntry.tools.length} tools</span>
		{/snippet}
		<div role="menu" aria-label={sheetEntry.label} class="flex flex-col" data-rail-sheet={sheetEntry.id}>
			{#each sheetEntry.tools as tool (tool.id)}
				{@const off = unavailable(tool)}
				<button
					type="button"
					role="menuitemradio"
					aria-checked={tool.id === active}
					aria-disabled={off || undefined}
					class="flex min-h-14 w-full items-center gap-3 rounded px-2 text-left text-lg hover:bg-surface-2 {off ? 'text-fg-disabled' : tool.id === active ? 'text-signal' : 'text-fg'}"
					data-sheet-item={tool.id}
					onclick={() => chooseFromSheet(tool)}
				>
					<span class="grid h-11 w-11 flex-none place-items-center rounded text-fg-muted {tool.ai ? 'ai-mark' : 'bg-surface-2'}" data-ai-mark={tool.ai || undefined}><Icon name={tool.icon} className="h-5 w-5" /></span>
					<span class="min-w-0 flex-1 truncate">{tool.label}</span>
					{#if applied.includes(tool.id)}<span class="h-2 w-2 flex-none rounded-full bg-signal" aria-label="Applied"></span>{/if}
				</button>
			{/each}
		</div>
	</FrameSheet>
{/if}

<style>
	.rail,
	.strip {
		scrollbar-width: none;
	}

	.strip {
		mask-image: linear-gradient(90deg, rgb(var(--scrim)) 85%, transparent);
	}

	.rail .is-on::before {
		content: '';
		position: absolute;
		left: -6px;
		top: 8px;
		bottom: 8px;
		width: 2px;
		background: rgb(var(--signal));
	}

	.rail[data-rail-joined] .is-on::before {
		left: -3px;
	}

	.corner {
		position: absolute;
		right: 3px;
		bottom: 3px;
		width: 0;
		height: 0;
		border-left: 5px solid transparent;
		border-bottom: 5px solid currentColor;
		opacity: 0.7;
	}

	.strip .is-on::before {
		content: '';
		position: absolute;
		left: 10px;
		right: 10px;
		bottom: -4px;
		height: 2px;
		background: rgb(var(--signal));
	}
</style>
