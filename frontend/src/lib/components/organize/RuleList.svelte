<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Alert, Badge, Menu, Switch } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
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

	function ruleMenuItems(rule: OrganizeRule, index: number): MenuItem[] {
		return [
			{ id: 'stop', label: rule.stop_after ? 'Do not stop after this rule' : 'Stop after this rule' },
			...(index === 0 ? [] : [{ id: 'up', label: 'Move up' }]),
			...(index === rules.length - 1 ? [] : [{ id: 'down', label: 'Move down' }]),
			{ id: 'dup', label: 'Duplicate' },
			{ id: 'del', label: 'Delete', danger: true }
		];
	}

	function runRuleAction(rule: OrganizeRule, index: number, key: string) {
		if (key === 'stop') onToggleStop(rule);
		else if (key === 'up') move(index, -1);
		else if (key === 'down') move(index, 1);
		else if (key === 'dup') onDuplicate(rule);
		else onDelete(rule);
	}

	function move(index: number, delta: number) {
		onReorder(moveItem(rules, index, index + delta).map((r) => r.id));
	}
</script>

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
					<span class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-xs tabular-nums text-fg-subtle">
						<span>Filed {rule.filed_count} {rule.filed_count === 1 ? 'item' : 'items'}</span>
						<span>{rule.last_run_at ? `Last run ${timeAgo(rule.last_run_at)}` : 'Not run yet'}</span>
						{#if (rule.waiting_count ?? 0) > 0}
							<span class="text-warning" data-testid="rule-waiting">{rule.waiting_count} waiting</span>
						{/if}
						{#if (rule.skipped_count ?? 0) > 0}
							<span data-testid="rule-skipped">{rule.skipped_count} skipped</span>
						{/if}
						{#if rule.updated_by_name}
							<span data-testid="rule-changed-by">Changed by {rule.updated_by_name} {timeAgo(rule.updated_at)}</span>
						{/if}
					</span>
				</button>
				<div class="relative flex-shrink-0">
					<Menu
						label="Rule actions"
						menuLabel="Rule options"
						width={210}
						items={ruleMenuItems(rule, index)}
						onselect={(key) => runRuleAction(rule, index, key)}
					/>
				</div>
			</div>
			{#if info.notice}
				<div class="px-3 pb-3">
					<Alert variant="warning" density="compact" icon>{info.notice}</Alert>
				</div>
			{:else if (rule.waiting_count ?? 0) > 0}
				<div class="px-3 pb-3">
					<Alert variant="warning" density="compact" icon>
						{rule.waiting_count} {rule.waiting_count === 1 ? 'model is' : 'models are'} waiting and will be tried again on their own.
					</Alert>
				</div>
			{/if}
		</li>
	{/each}
</ul>
