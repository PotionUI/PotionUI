<script lang="ts">
	import { tick, untrack, type Snippet } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { editorsRevision } from '$lib/editors/registry';
	import { buildMenu, menuTabs, type EditorMenuContext, type MenuChoice, type MenuRow } from '$lib/editors/menuItems';

	let {
		ctx,
		onopenmenu,
		childFooter
	}: {
		ctx: EditorMenuContext;
		onopenmenu?: (id: string) => void;
		childFooter?: Snippet<[{ id: string; close: () => void }]>;
	} = $props();

	const WIDTHS: Record<string, number> = { file: 264, edit: 272, view: 264, tools: 240 };
	const EDGE = 8;

	let barEl = $state<HTMLElement>();
	let menuEl = $state<HTMLElement>();
	let subEl = $state<HTMLElement>();
	let open = $state<string | null>(null);
	let sub = $state<string | null>(null);
	let pos = $state<{ left: number; top: number; maxHeight: number; seamLeft: number; seamWidth: number } | null>(null);
	let subPos = $state<{ left: number; top: number; maxHeight: number } | null>(null);

	let tabs = $derived.by(() => {
		void $editorsRevision;
		return menuTabs(ctx.editor);
	});
	let model = $derived.by(() => {
		void $editorsRevision;
		const current = ctx;
		const id = open;
		return id ? untrack(() => buildMenu(id, current)) : null;
	});
	let subRow = $derived(model && sub ? (model.groups.flat().find((row) => row.id === sub) ?? null) : null);
	let width = $derived(open ? (WIDTHS[open] ?? 240) : 240);

	function labelEl(id: string): HTMLElement | null {
		return barEl ? (Array.from(barEl.querySelectorAll<HTMLElement>('[data-menu-tab]')).find((el) => el.dataset.menuTab === id) ?? null) : null;
	}

	function rowEl(id: string): HTMLElement | null {
		return menuEl ? (Array.from(menuEl.querySelectorAll<HTMLElement>('[data-menu-row]')).find((el) => el.dataset.rowId === id) ?? null) : null;
	}

	function rowEls(root: HTMLElement | undefined): HTMLElement[] {
		return root ? Array.from(root.querySelectorAll<HTMLElement>('[data-menu-row]')) : [];
	}

	function focusAt(root: HTMLElement | undefined, which: 'first' | 'last' | number) {
		const rows = rowEls(root);
		if (!rows.length) return;
		const index = which === 'first' ? 0 : which === 'last' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, which));
		rows[index].focus({ preventScroll: true });
	}

	function openMenu(id: string, focus: 'first' | 'last' | null = 'first') {
		open = id;
		sub = null;
		subPos = null;
		onopenmenu?.(id);
		if (focus) void tick().then(() => void tick().then(() => focusAt(menuEl, focus)));
	}

	function closeAll(restore: boolean) {
		const was = open;
		open = null;
		sub = null;
		pos = null;
		subPos = null;
		if (restore && was) void tick().then(() => labelEl(was)?.focus());
	}

	function closeSub(restore: boolean) {
		const was = sub;
		sub = null;
		subPos = null;
		if (restore && was) void tick().then(() => rowEl(was)?.focus());
	}

	function openSub(row: MenuRow, focus: boolean) {
		if (!row.children) return;
		sub = row.id;
		if (focus) void tick().then(() => void tick().then(() => focusAt(subEl, 'first')));
	}

	function neighbour(id: string, step: number): string {
		const index = tabs.findIndex((tab) => tab.id === id);
		return tabs[(index + step + tabs.length) % tabs.length].id;
	}

	function place() {
		if (!open || !menuEl) return;
		const label = labelEl(open);
		if (!label) return;
		const rect = label.getBoundingClientRect();
		const left = Math.max(EDGE, Math.min(rect.left, window.innerWidth - menuEl.offsetWidth - EDGE));
		pos = {
			left,
			top: rect.bottom,
			maxHeight: Math.max(160, window.innerHeight - rect.bottom - 16),
			seamLeft: rect.left - left + 1,
			seamWidth: Math.max(0, rect.width - 2)
		};
	}

	function placeSub() {
		if (!sub || !menuEl || !subEl) return;
		const row = rowEl(sub);
		if (!row) return;
		const parent = menuEl.getBoundingClientRect();
		const rect = row.getBoundingClientRect();
		const w = subEl.offsetWidth;
		const h = subEl.offsetHeight;
		let left = parent.right + 2;
		if (left + w > window.innerWidth - EDGE) left = Math.max(EDGE, parent.left - w - 2);
		subPos = {
			left,
			top: Math.max(EDGE, Math.min(rect.top - 5, window.innerHeight - h - EDGE)),
			maxHeight: window.innerHeight - 2 * EDGE
		};
	}

	$effect(() => {
		if (!open || !menuEl) return;
		void model;
		untrack(place);
		const onResize = () => untrack(place);
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	$effect(() => {
		if (!sub || !subEl) return;
		void subRow;
		untrack(placeSub);
	});

	function activate(row: MenuRow) {
		if (row.children) {
			openSub(row, true);
			return;
		}
		if (row.reason) return;
		closeAll(true);
		row.run();
	}

	function choose(child: MenuChoice) {
		if (child.disabled) return;
		closeAll(true);
		child.run();
	}

	function hover(row: MenuRow, element: HTMLElement) {
		element.focus({ preventScroll: true });
		if (row.children) openSub(row, false);
		else if (sub) closeSub(false);
	}

	function hoverChild(element: HTMLElement) {
		element.focus({ preventScroll: true });
	}

	function currentRow(root: HTMLElement | undefined): { rows: HTMLElement[]; index: number } {
		const rows = rowEls(root);
		return { rows, index: rows.indexOf(document.activeElement as HTMLElement) };
	}

	function onMenuKey(event: KeyboardEvent) {
		const { rows, index } = currentRow(menuEl);
		const key = event.key;
		if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
			event.preventDefault();
			if (sub) closeSub(false);
			const next = key === 'Home' ? 0 : key === 'End' ? rows.length - 1 : key === 'ArrowDown' ? (index + 1) % rows.length : (index - 1 + rows.length) % rows.length;
			rows[next]?.focus({ preventScroll: true });
		} else if (key === 'ArrowRight') {
			event.preventDefault();
			const id = rows[index]?.dataset.rowId;
			const row = model?.groups.flat().find((candidate) => candidate.id === id);
			if (row?.children) openSub(row, true);
			else if (open) openMenu(neighbour(open, 1));
		} else if (key === 'ArrowLeft') {
			event.preventDefault();
			if (open) openMenu(neighbour(open, -1));
		} else if (key === 'Tab') {
			closeAll(false);
		}
	}

	function onSubKey(event: KeyboardEvent) {
		const { rows, index } = currentRow(subEl);
		const key = event.key;
		if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
			event.preventDefault();
			event.stopPropagation();
			const next = key === 'Home' ? 0 : key === 'End' ? rows.length - 1 : key === 'ArrowDown' ? (index + 1) % rows.length : (index - 1 + rows.length) % rows.length;
			rows[next]?.focus({ preventScroll: true });
		} else if (key === 'ArrowLeft') {
			event.preventDefault();
			event.stopPropagation();
			closeSub(true);
		} else if (key === 'ArrowRight') {
			event.preventDefault();
			event.stopPropagation();
		} else if (key === 'Tab') {
			closeAll(false);
		}
	}

	function onBarKey(event: KeyboardEvent, id: string) {
		if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			openMenu(id, 'first');
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			openMenu(id, 'last');
		} else if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
			event.preventDefault();
			const next = neighbour(id, event.key === 'ArrowRight' ? 1 : -1);
			if (open) openMenu(next, null);
			labelEl(next)?.focus();
		}
	}

	function clickTab(id: string) {
		if (open === id) closeAll(false);
		else openMenu(id, null);
	}

	function enterTab(id: string) {
		if (open && open !== id) openMenu(id, null);
	}

	function menuDismiss(reason: 'outside' | 'escape') {
		if (reason === 'escape') closeAll(true);
		else closeAll(false);
	}

	function subDismiss(reason: 'outside' | 'escape') {
		if (reason === 'escape') closeSub(true);
		else if (open) closeSub(false);
	}

	function roleOf(row: MenuRow): string {
		if (row.checked === null) return 'menuitem';
		return row.id.startsWith('tool:') ? 'menuitemradio' : 'menuitemcheckbox';
	}

	const rowClass =
		'flex min-h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-base text-fg hover:bg-surface-3 focus:bg-surface-3 focus:outline-none aria-expanded:bg-surface-3 aria-disabled:cursor-not-allowed aria-disabled:opacity-50';
</script>

{#snippet rowButton(row: MenuRow)}
	<button
		type="button"
		role={roleOf(row)}
		aria-checked={row.checked === null ? undefined : row.checked}
		aria-haspopup={row.children ? 'menu' : undefined}
		aria-expanded={row.children ? sub === row.id : undefined}
		aria-disabled={row.reason ? 'true' : undefined}
		tabindex="-1"
		class="{rowClass} {row.danger ? 'text-danger' : row.checked ? 'text-signal' : ''}"
		data-menu-row
		data-row-id={row.id}
		data-menu-level="0"
		onclick={() => activate(row)}
		onpointerenter={(event) => hover(row, event.currentTarget)}
	>
		<span class="grid h-4 w-4 flex-none place-items-center {row.danger ? 'text-danger' : row.checked ? 'text-signal' : 'text-fg-muted'}">
			<Icon name={row.icon} className="h-4 w-4" />
		</span>
		<span class="min-w-0 flex-1 truncate">{row.label}</span>
		{#if row.reason}<span class="sr-only">, {row.reason}</span>{/if}
		{#if row.children}
			<Icon name="chevron-right" className="h-3.5 w-3.5 flex-none text-fg-muted" />
		{:else if row.keybind}
			<span class="flex-none pl-4 font-mono text-sm tabular-nums text-fg-subtle">{row.keybind}</span>
		{:else if row.checked}
			<Icon name="check" className="h-3.5 w-3.5 flex-none text-signal" />
		{/if}
	</button>
{/snippet}

{#snippet childButton(child: MenuChoice)}
	<button
		type="button"
		role={child.checked === undefined ? 'menuitem' : 'menuitemradio'}
		aria-checked={child.checked === undefined ? undefined : child.checked}
		aria-disabled={child.disabled ? 'true' : undefined}
		tabindex="-1"
		class="{rowClass} {child.sub ? 'py-1.5' : ''} {child.checked ? 'text-signal' : ''}"
		data-menu-row
		data-row-id={child.id}
		data-menu-level="1"
		onclick={() => choose(child)}
		onpointerenter={(event) => hoverChild(event.currentTarget)}
	>
		<span class="grid h-4 w-4 flex-none place-items-center text-fg-muted">
			{#if child.icon}<Icon name={child.icon} className="h-4 w-4" />{/if}
		</span>
		<span class="min-w-0 flex-1">
			<span class="block truncate">{child.label}</span>
			{#if child.sub}<span class="block truncate text-sm text-fg-subtle">{child.sub}</span>{/if}
		</span>
		{#if child.disabled}<span class="sr-only">, {child.disabled}</span>{/if}
		{#if child.badge}
			<span class="flex-none rounded bg-surface-3 px-1.5 font-mono text-sm text-fg-muted">{child.badge}</span>
		{:else if child.dot}
			<span class="h-[7px] w-[7px] flex-none rounded-full" style="background: {child.dot}" aria-hidden="true" data-menu-applied></span>
		{/if}
		{#if child.checked}<Icon name="check" className="h-3.5 w-3.5 flex-none text-signal" />{/if}
	</button>
{/snippet}

<div bind:this={barEl} class="flex flex-none items-stretch pl-1" role="menubar" aria-label="Editor menus" data-editor-menubar>
	{#each tabs as tab, i (tab.id)}
		<button
			type="button"
			role="menuitem"
			aria-haspopup="menu"
			aria-expanded={open === tab.id}
			tabindex={open ? (open === tab.id ? 0 : -1) : i === 0 ? 0 : -1}
			class="relative -mb-px inline-flex items-center whitespace-nowrap rounded-t border border-b-0 border-transparent px-3 text-base text-fg transition-colors duration-100 hover:bg-surface-3/60 aria-expanded:z-[2] aria-expanded:border-line-strong aria-expanded:bg-surface-2"
			data-menu-tab={tab.id}
			onclick={() => clickTab(tab.id)}
			onpointerenter={() => enterTab(tab.id)}
			onkeydown={(event) => onBarKey(event, tab.id)}
		>
			{tab.label}
		</button>
	{/each}
</div>

{#if open && model}
	<div
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: menuDismiss, ignore: [barEl] }}
		class="fixed"
		style={pos ? `left: ${pos.left}px; top: ${pos.top}px; width: ${width}px;` : `left: 0; top: 0; width: ${width}px; visibility: hidden;`}
		data-editor-menu-wrap
	>
		{#if pos}
			<span class="pointer-events-none absolute top-0 z-[1] h-px bg-surface-2" style="left: {pos.seamLeft}px; width: {pos.seamWidth}px;" aria-hidden="true"></span>
		{/if}
		<div
			bind:this={menuEl}
			role="menu"
			aria-label={model.label}
			tabindex="-1"
			class="overflow-y-auto rounded-xl rounded-tl-none border border-line-strong bg-surface-2 p-1 shadow-overlay"
			style={pos ? `max-height: ${pos.maxHeight}px;` : ''}
			onkeydown={onMenuKey}
			data-editor-menu={model.id}
		>
			{#each model.groups as group, g (g)}
				{#if g > 0}<div class="my-1 h-px bg-line" role="separator"></div>{/if}
				{#each group as row (row.id)}
					{#if row.reason}
						<Tooltip text={row.reason} position="right" delay={100} wrapperClass="flex w-full">
							{@render rowButton(row)}
						</Tooltip>
					{:else}
						{@render rowButton(row)}
					{/if}
				{/each}
			{/each}
		</div>
	</div>
{/if}

{#if open && subRow?.children}
	<div
		bind:this={subEl}
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: subDismiss }}
		role="menu"
		aria-label={subRow.label}
		tabindex="-1"
		class="fixed overflow-y-auto rounded-xl border border-line-strong bg-surface-2 p-1 shadow-overlay"
		style={subPos
			? `left: ${subPos.left}px; top: ${subPos.top}px; width: ${subRow.childrenWidth}px; max-height: ${subPos.maxHeight}px;`
			: `left: 0; top: 0; width: ${subRow.childrenWidth}px; visibility: hidden;`}
		onkeydown={onSubKey}
		data-editor-submenu={subRow.id}
	>
		{#each subRow.children as child (child.id)}
			{#if 'separator' in child}
				<div class="my-1 h-px bg-line" role="separator"></div>
			{:else if 'heading' in child}
				<div class="px-2.5 pb-1 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">{child.heading}</div>
			{:else if child.disabled}
				<Tooltip text={child.disabled} position="right" delay={100} wrapperClass="flex w-full">
					{@render childButton(child)}
				</Tooltip>
			{:else}
				{@render childButton(child)}
			{/if}
		{/each}
		{#if childFooter}
			{@render childFooter({ id: subRow.id, close: () => closeAll(true) })}
		{/if}
	</div>
{/if}
