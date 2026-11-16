<script lang="ts">
	import { untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { editorsRevision } from '$lib/editors/registry';
	import { buildMenu, menuTabs, type EditorMenuContext, type MenuChoice, type MenuRow } from '$lib/editors/menuItems';
	import FrameSheet from './FrameSheet.svelte';

	let {
		ctx,
		fileName = null,
		onclose,
		onopenmenu
	}: {
		ctx: EditorMenuContext;
		fileName?: string | null;
		onclose: () => void;
		onopenmenu?: (id: string) => void;
	} = $props();

	let section = $state<string | null>(null);
	let nested = $state<string | null>(null);

	let tabs = $derived.by(() => {
		void $editorsRevision;
		return menuTabs(ctx.editor);
	});
	let model = $derived.by(() => {
		void $editorsRevision;
		const current = ctx;
		const id = section;
		return id ? untrack(() => buildMenu(id, current)) : null;
	});

	function toggleSection(id: string) {
		nested = null;
		if (section === id) {
			section = null;
			return;
		}
		section = id;
		onopenmenu?.(id);
	}

	function pickRow(row: MenuRow) {
		if (row.children) {
			nested = nested === row.id ? null : row.id;
			return;
		}
		if (row.reason) return;
		onclose();
		row.run();
	}

	function pickChild(child: MenuChoice) {
		if (child.disabled) return;
		onclose();
		child.run();
	}

	const rowClass = 'flex min-h-11 w-full items-center gap-2.5 rounded py-1 pl-3.5 pr-2.5 text-left text-base active:bg-surface-3';
</script>

<FrameSheet label="Menu" {onclose}>
	{#snippet header()}
		<span class="text-lg font-semibold text-fg">Menu</span>
		<span class="flex-1"></span>
		{#if fileName}
			<span class="truncate font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{fileName}</span>
		{/if}
	{/snippet}
	<div class="flex flex-col" data-editor-menu-sheet>
		{#each tabs as tab (tab.id)}
			<div class="border-t border-line first:border-t-0" data-sheet-section={tab.id}>
				<button
					type="button"
					class="flex h-11 w-full items-center rounded px-2.5 text-left text-lg font-semibold text-fg"
					aria-expanded={section === tab.id}
					onclick={() => toggleSection(tab.id)}
				>
					{tab.label}
					<Icon name="chevron-down" className="ml-auto h-3.5 w-3.5 text-fg-muted transition-transform duration-150 {section === tab.id ? 'rotate-180' : ''}" />
				</button>
				{#if section === tab.id && model}
					<div class="pb-2" role="group" aria-label={tab.label}>
						{#each model.groups as group, g (g)}
							{#if g > 0}<div class="mx-3.5 my-1 h-px bg-line" role="separator"></div>{/if}
							{#each group as row (row.id)}
								<button
									type="button"
									class="{rowClass} {row.danger ? 'text-danger' : row.checked ? 'text-signal' : 'text-fg'} {row.reason && !row.children ? 'opacity-50' : ''}"
									aria-disabled={row.reason && !row.children ? 'true' : undefined}
									aria-expanded={row.children ? nested === row.id : undefined}
									aria-checked={row.checked === null ? undefined : row.checked}
									role={row.checked === null ? undefined : 'menuitemcheckbox'}
									data-sheet-row={row.id}
									onclick={() => pickRow(row)}
								>
									<span class="grid h-4 w-4 flex-none place-items-center {row.danger ? 'text-danger' : row.checked ? 'text-signal' : 'text-fg-muted'}">
										<Icon name={row.icon} className="h-4 w-4" />
									</span>
									<span class="min-w-0 flex-1">
										<span class="block">{row.label}</span>
										{#if row.reason && !row.children}<span class="block text-sm text-fg-subtle">{row.reason}</span>{/if}
									</span>
									{#if row.children}
										<Icon name={nested === row.id ? 'chevron-up' : 'chevron-down'} className="h-3.5 w-3.5 flex-none text-fg-muted" />
									{:else if row.checked}
										<Icon name="check" className="h-3.5 w-3.5 flex-none text-signal" />
									{/if}
								</button>
								{#if row.children && nested === row.id}
									<div class="mb-1 ml-[22px] border-l border-line-strong pl-2">
										{#each row.children as child (child.id)}
											{#if 'separator' in child}
												<div class="mx-3.5 my-1 h-px bg-line" role="separator"></div>
											{:else if 'heading' in child}
												<div class="px-3.5 pb-0.5 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">{child.heading}</div>
											{:else}
												<button
													type="button"
													class="{rowClass} text-fg {child.disabled ? 'opacity-50' : ''}"
													aria-disabled={child.disabled ? 'true' : undefined}
													data-sheet-child={child.id}
													onclick={() => pickChild(child)}
												>
													<span class="grid h-4 w-4 flex-none place-items-center text-fg-muted">
														{#if child.icon}<Icon name={child.icon} className="h-4 w-4" />{/if}
													</span>
													<span class="min-w-0 flex-1">
														<span class="block">{child.label}</span>
														{#if child.disabled}
															<span class="block text-sm text-fg-subtle">{child.disabled}</span>
														{:else if child.sub}
															<span class="block text-sm text-fg-subtle">{child.sub}</span>
														{/if}
													</span>
													{#if child.badge}
														<span class="flex-none rounded bg-surface-3 px-1.5 font-mono text-sm text-fg-muted">{child.badge}</span>
													{:else if child.dot}
														<span class="h-[7px] w-[7px] flex-none rounded-full" style="background: {child.dot}" aria-hidden="true"></span>
													{/if}
												</button>
											{/if}
										{/each}
									</div>
								{/if}
							{/each}
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	</div>
</FrameSheet>
