<script lang="ts">
	import { tick, type Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PromptPicker from '$lib/components/PromptPicker.svelte';
	import PromptMenuRow from '$lib/components/PromptMenuRow.svelte';
	import { Menu } from '$lib/components/ui';
	import type { MenuAnchor } from '$lib/utils/menuPosition';
	import { addPromptState } from '$lib/generation/promptBatch';
	import type { PromptTabData } from '$lib/types/tabs';

	const FADE_WIDTH = 20;

	let {
		prompts,
		active,
		perPrompt,
		canBatch,
		overCap,
		maxPrompts,
		onSelect,
		onAdd,
		onDuplicate,
		onDelete,
		onRename,
		singleLabel = 'Prompt',
		trailing = undefined
	}: {
		prompts: PromptTabData[];
		active: number;
		perPrompt: number;
		canBatch: boolean;
		overCap: boolean;
		maxPrompts: number;
		onSelect: (index: number) => void;
		onAdd: () => void;
		onDuplicate: (index: number) => void;
		onDelete: (index: number) => void;
		onRename: (index: number, name: string) => void;
		singleLabel?: string;
		trailing?: Snippet;
	} = $props();

	let strip = $state<HTMLDivElement | null>(null);
	let renaming = $state<number | null>(null);
	let renameDraft = $state('');
	let menuOpen = $state(false);
	let menuAnchor = $state<MenuAnchor | null>(null);
	let fadeLeft = $state(false);
	let fadeRight = $state(false);

	const count = $derived(prompts.length);
	const multiple = $derived(count > 1);
	const add = $derived(addPromptState({ canBatch, count, maxPrompts, perPrompt }));
	const addDisabled = $derived(add.disabled);
	const addTip = $derived(add.explanation ?? 'Add prompt');
	const labels = $derived(prompts.map((entry, index) => labelFor(entry, index)));

	function labelFor(entry: PromptTabData, index: number): string {
		const name = entry.name?.trim();
		if (name) return name;
		return count === 1 ? singleLabel : `Prompt ${index + 1}`;
	}

	function overflows(index: number): boolean {
		return overCap && index >= maxPrompts;
	}

	function dimmed(index: number): boolean {
		return !canBatch && index > 0;
	}

	function syncFades() {
		const el = strip;
		if (!el) return;
		fadeLeft = el.scrollLeft > 1;
		fadeRight = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
	}

	function centerActive() {
		const el = strip;
		if (!el) return;
		const tab = el.querySelector<HTMLElement>('[data-tab][data-active="true"]');
		if (tab) {
			const target = tab.offsetLeft - (el.clientWidth - tab.offsetWidth) / 2;
			el.scrollLeft = Math.max(0, Math.min(target, el.scrollWidth - el.clientWidth));
		}
		syncFades();
	}

	$effect(() => {
		void active;
		void labels.join('\u0001');
		void renaming;
		if (strip) centerActive();
	});

	$effect(() => {
		const el = strip;
		if (!el || typeof ResizeObserver === 'undefined') return;
		const observer = new ResizeObserver(() => centerActive());
		observer.observe(el);
		return () => observer.disconnect();
	});

	function focusTab(index: number) {
		void tick().then(() =>
			strip?.querySelector<HTMLElement>(`[data-tab-button="${index}"]`)?.focus()
		);
	}

	function select(index: number) {
		if (index !== active) onSelect(index);
	}

	function startRename(index: number) {
		renameDraft = prompts[index]?.name ?? '';
		renaming = index;
		void tick().then(() => {
			const input = strip?.querySelector<HTMLInputElement>('input[data-rename]');
			input?.focus();
			input?.select();
		});
	}

	function commitRename(restoreFocus: boolean) {
		if (renaming === null) return;
		const index = renaming;
		renaming = null;
		onRename(index, renameDraft);
		if (restoreFocus) focusTab(index);
	}

	function cancelRename() {
		if (renaming === null) return;
		const index = renaming;
		renaming = null;
		focusTab(index);
	}

	function handleTabKeydown(event: KeyboardEvent, index: number) {
		if (event.key === 'F2') {
			event.preventDefault();
			startRename(index);
			return;
		}
		let next = -1;
		if (event.key === 'ArrowRight') next = (index + 1) % count;
		else if (event.key === 'ArrowLeft') next = (index - 1 + count) % count;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = count - 1;
		if (next < 0) return;
		event.preventDefault();
		select(next);
		focusTab(next);
	}

	async function openContext(event: MouseEvent, index: number) {
		if ((event.target as HTMLElement).closest('input')) return;
		event.preventDefault();
		const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const pointer = event.clientX !== 0 || event.clientY !== 0;
		const x = pointer ? event.clientX : box.left;
		const y = pointer ? event.clientY : box.bottom;
		menuAnchor = {
			getBoundingClientRect: () =>
				({ x, y, left: x, right: x, top: y, bottom: y, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect
		};
		if (index !== active) {
			onSelect(index);
			await tick();
		}
		renaming = null;
		menuOpen = true;
	}
</script>

<div class="prompt-tabs" data-header-lead>
	<div
		bind:this={strip}
		class="tabstrip"
		role="tablist"
		aria-label="Prompts"
		data-header-strip
		style:--fl="{fadeLeft ? FADE_WIDTH : 0}px"
		style:--fr="{fadeRight ? FADE_WIDTH : 0}px"
		onscroll={syncFades}
	>
		{#each prompts as _entry, index (index)}
			{@const on = index === active}
			<div
				class="tab {on ? 'on text-fg' : 'text-fg-muted hover:text-fg'} {overflows(index) ? '!text-danger' : ''} {dimmed(index) ? 'opacity-50' : ''}"
				role="presentation"
				data-tab
				data-active={on}
				oncontextmenu={(event) => openContext(event, index)}
			>
				{#if renaming === index}
					<input
						data-rename
						class="ml-1.5 mr-1 h-7 w-32 flex-shrink-0 rounded border border-signal bg-surface-1 px-2 text-base text-fg outline-none"
						type="text"
						aria-label="Prompt name"
						placeholder="Prompt name"
						bind:value={renameDraft}
						onkeydown={(event) => {
							if (event.key === 'Enter') {
								event.preventDefault();
								commitRename(true);
							} else if (event.key === 'Escape') {
								event.preventDefault();
								event.stopPropagation();
								cancelRename();
							}
						}}
						onblur={() => commitRename(false)}
					/>
				{:else}
					<Tooltip text={labels[index]} position="bottom" truncateOnly wrapperClass="flex h-full min-w-0 items-center">
						<button
							type="button"
							role="tab"
							class="tab-button focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-signal {on ? 'font-semibold' : 'font-medium'} {overflows(index) ? 'line-through' : ''}"
							aria-selected={on}
							tabindex={on ? 0 : -1}
							data-tab-button={index}
							onclick={() => select(index)}
							onkeydown={(event) => handleTabKeydown(event, index)}
						>
							<span class="truncate">{labels[index]}</span>
							{#if overflows(index)}
								<span class="sr-only">, will not run</span>
							{/if}
						</button>
					</Tooltip>
					{#if on}
						<Menu
							label="Prompt actions"
							menuLabel="Actions for {labels[index]}"
							width={272}
							align="left"
							anchor={menuAnchor}
							bind:open={menuOpen}
							onclose={() => (menuAnchor = null)}
						>
							{#snippet trigger(menu)}
								<Tooltip text="Prompt actions" position="top">
									<button
										type="button"
										class="mr-1 grid h-6 w-6 flex-shrink-0 place-items-center rounded text-fg-subtle transition-colors hover:bg-control-fill-hover hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-signal {menu.open
											? 'bg-control-fill-hover text-fg'
											: ''}"
										aria-label="Prompt actions"
										aria-haspopup="menu"
										aria-expanded={menu.open}
										onclick={menu.toggle}
									>
										<Icon name="chevron-down" className="h-[13px] w-[13px] transition-transform {menu.open ? 'rotate-180' : ''}" />
									</button>
								</Tooltip>
							{/snippet}
							{#snippet children(menu)}
								<PromptMenuRow
									icon="composer-pencil"
									label="Rename"
									onclick={() => {
										menu.close(false);
										startRename(index);
									}}
								/>
								<PromptMenuRow
									icon="composer-copy"
									label="Duplicate"
									description={add.explanation ?? undefined}
									disabled={addDisabled}
									onclick={() => {
										menu.close(true);
										onDuplicate(index);
									}}
								/>
								{#if multiple}
									<div class="mx-2 my-1 h-px bg-line" role="separator"></div>
									<PromptMenuRow
										icon="composer-trash"
										label="Delete"
										danger
										onclick={() => {
											menu.close(false);
											onDelete(index);
										}}
									/>
								{/if}
							{/snippet}
						</Menu>
					{/if}
				{/if}
			</div>
		{/each}
	</div>
	<Tooltip text={addTip} position="top">
		<button
			type="button"
			class="tab-tool first-tool focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
			aria-label="Add prompt"
			disabled={addDisabled}
			onclick={onAdd}
		>
			<Icon name="composer-plus" className="h-3.5 w-3.5" />
		</button>
	</Tooltip>
	{#if multiple}
		<PromptPicker
			{prompts}
			{active}
			{perPrompt}
			{canBatch}
			{overCap}
			{maxPrompts}
			{onSelect}
			{onAdd}
			{onDuplicate}
			{onDelete}
			{onRename}
		/>
	{/if}
	{#if trailing}
		{@render trailing()}
	{/if}
</div>

<style>
	.prompt-tabs {
		display: flex;
		align-items: stretch;
		min-width: 0;
		flex: 0 1 auto;
	}

	.tabstrip {
		position: relative;
		display: flex;
		min-width: 0;
		flex: 0 1 auto;
		overflow-x: auto;
		overflow-y: hidden;
		scrollbar-width: none;
		-webkit-mask-image: linear-gradient(
			to right,
			transparent 0,
			black var(--fl, 0px),
			black calc(100% - var(--fr, 0px)),
			transparent 100%
		);
		mask-image: linear-gradient(
			to right,
			transparent 0,
			black var(--fl, 0px),
			black calc(100% - var(--fr, 0px)),
			transparent 100%
		);
	}

	.tabstrip::-webkit-scrollbar {
		display: none;
	}

	.tab {
		display: flex;
		flex: none;
		align-items: center;
		max-width: 220px;
		transition: color 0.1s;
	}

	.tab.on {
		box-shadow: inset 0 -2px 0 rgb(var(--signal));
	}

	.tab-button {
		display: inline-flex;
		align-items: center;
		height: 100%;
		min-width: 0;
		padding: 0 10px;
		font-size: 13px;
		line-height: 20px;
		white-space: nowrap;
		color: inherit;
	}

	.tab.on .tab-button {
		padding-right: 4px;
	}

	.tab-button .truncate {
		max-width: 150px;
	}

	.tab-tool {
		display: grid;
		place-items: center;
		flex: none;
		align-self: center;
		width: 28px;
		height: 28px;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		transition:
			background-color 0.1s,
			color 0.1s;
	}

	.tab-tool.first-tool {
		margin-left: 4px;
	}

	.tab-tool:hover:not(:disabled) {
		background: rgb(var(--control-fill-hover));
		color: rgb(var(--fg));
	}

	.tab-tool:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}
</style>
