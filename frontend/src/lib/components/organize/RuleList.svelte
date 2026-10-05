<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Alert, Badge, IconButton, Switch } from '$lib/components/ui';
	import { actionIcon, subjectIcon } from '$lib/organize/icons';
	import { moveItem } from '$lib/organize/reorder';
	import { ruleSentence } from '$lib/organize/sentence';
	import { ruleStatusInfo } from '$lib/organize/status';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { ValueLabels } from '$lib/organize/draft';
	import type { OrganizeCatalog, OrganizeRule } from '$lib/types/organize';

	let {
		rules,
		catalog,
		labels,
		collectionNames = {},
		busyId = null,
		onOpen,
		onToggle,
		onToggleStop,
		onDuplicate,
		onDelete,
		onReorder
	}: {
		rules: OrganizeRule[];
		catalog: OrganizeCatalog | null;
		labels: ValueLabels;
		collectionNames?: Record<string, string>;
		busyId?: string | null;
		onOpen: (rule: OrganizeRule) => void;
		onToggle: (rule: OrganizeRule, next: boolean) => void;
		onToggleStop: (rule: OrganizeRule) => void;
		onDuplicate: (rule: OrganizeRule) => void;
		onDelete: (rule: OrganizeRule) => void;
		onReorder: (ids: string[]) => void;
	} = $props();

	let menuFor = $state<string | null>(null);
	let dragIndex = $state<number | null>(null);
	let overIndex = $state<number | null>(null);

	function sentence(rule: OrganizeRule) {
		return ruleSentence(rule, catalog, labels, collectionNames);
	}

	function drop(to: number) {
		const from = dragIndex;
		dragIndex = null;
		overIndex = null;
		if (from === null || from === to) return;
		onReorder(moveItem(rules, from, to).map((r) => r.id));
	}

	function move(index: number, delta: number) {
		menuFor = null;
		onReorder(moveItem(rules, index, index + delta).map((r) => r.id));
	}
</script>

<svelte:window onclick={() => (menuFor = null)} />

<ul class="space-y-2" data-testid="rule-list">
	{#each rules as rule, index (rule.id)}
		{@const info = ruleStatusInfo(rule)}
		{@const parts = sentence(rule)}
		<li
			class="relative rounded-lg border bg-surface-1 shadow-raised transition-colors {info.tone === 'warning'
				? 'border-warning/40'
				: 'border-line'} {overIndex === index && dragIndex !== null ? 'border-signal' : ''} {rule.enabled ? '' : 'opacity-75'}"
			data-testid="rule-card"
			data-rule-id={rule.id}
			ondragover={(event) => {
				event.preventDefault();
				overIndex = index;
			}}
			ondrop={(event) => {
				event.preventDefault();
				drop(index);
			}}
		>
			<div class="flex items-start gap-3 p-3">
				<span
					class="mt-1 flex-shrink-0 cursor-grab text-fg-subtle"
					role="button"
					tabindex="-1"
					aria-label="Drag to reorder"
					draggable="true"
					ondragstart={(event) => {
						dragIndex = index;
						event.dataTransfer?.setData('text/plain', rule.id);
					}}
					ondragend={() => {
						dragIndex = null;
						overIndex = null;
					}}
				>
					<Icon name="grip" className="h-4 w-4" />
				</span>
				<div class="mt-0.5 flex-shrink-0">
					<Switch
						size="sm"
						label={rule.enabled ? `Switch off ${rule.name}` : `Switch on ${rule.name}`}
						checked={rule.enabled}
						busy={busyId === rule.id}
						onchange={(next) => onToggle(rule, next)}
					/>
				</div>
				<button type="button" class="min-w-0 flex-1 text-left" onclick={() => onOpen(rule)}>
					<span class="flex flex-wrap items-center gap-2">
						<span class="truncate text-sm font-semibold text-fg">{rule.name}</span>
						{#if info.label}
							<Badge size="sm" variant="warning">{info.label}</Badge>
						{/if}
						{#if rule.stop_after}
							<Badge size="sm" variant="neutral">Stops after</Badge>
						{/if}
					</span>
					<span class="mt-1 flex items-start gap-1.5 text-xs text-fg-muted">
						<Icon name={subjectIcon(rule.subject)} className="mt-0.5 w-3.5 h-3.5 flex-shrink-0 text-fg-subtle" />
						<span class="min-w-0">
							{#if parts.conditions.length > 0}
								<span class="text-fg-subtle">If</span>
								{parts.conditions.join(` ${parts.join} `)}
							{:else}
								<span class="text-fg-subtle">Every new item</span>
							{/if}
							<span class="text-fg-subtle">then</span>
							{#if rule.actions[0]}
								<Icon name={actionIcon(rule.actions[0].action)} className="inline w-3.5 h-3.5 align-text-bottom text-fg-subtle" />
							{/if}
							{parts.actions.join(' and ')}
						</span>
					</span>
					<span class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-2xs tabular-nums text-fg-subtle">
						<span>Filed {rule.filed_count} {rule.filed_count === 1 ? 'item' : 'items'}</span>
						<span>{rule.last_run_at ? `Last run ${timeAgo(rule.last_run_at)}` : 'Not run yet'}</span>
					</span>
				</button>
				<div class="relative flex-shrink-0">
					<IconButton
						icon="more"
						label="Rule actions"
						ariaExpanded={menuFor === rule.id}
						onclick={(event) => {
							event.stopPropagation();
							menuFor = menuFor === rule.id ? null : rule.id;
						}}
					/>
					{#if menuFor === rule.id}
						<div
							class="absolute right-0 top-full z-30 mt-1 min-w-[210px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
							role="menu"
						>
							{#each [{ key: 'stop', label: rule.stop_after ? 'Do not stop after this rule' : 'Stop after this rule' }, { key: 'up', label: 'Move up' }, { key: 'down', label: 'Move down' }, { key: 'dup', label: 'Duplicate' }, { key: 'del', label: 'Delete' }] as item (item.key)}
								{#if !(item.key === 'up' && index === 0) && !(item.key === 'down' && index === rules.length - 1)}
									<button
										type="button"
										role="menuitem"
										class="flex w-full items-center px-3 py-2 text-left text-sm hover:bg-surface-3 {item.key === 'del' ? 'text-danger' : 'text-fg'}"
										onclick={(event) => {
											event.stopPropagation();
											menuFor = null;
											if (item.key === 'stop') onToggleStop(rule);
											else if (item.key === 'up') move(index, -1);
											else if (item.key === 'down') move(index, 1);
											else if (item.key === 'dup') onDuplicate(rule);
											else onDelete(rule);
										}}
									>
										{item.label}
									</button>
								{/if}
							{/each}
						</div>
					{/if}
				</div>
			</div>
			{#if info.notice}
				<div class="px-3 pb-3">
					<Alert variant="warning" density="compact" icon>{info.notice}</Alert>
				</div>
			{/if}
		</li>
	{/each}
</ul>
