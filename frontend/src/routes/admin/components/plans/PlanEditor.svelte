<script lang="ts">
	import { untrack } from 'svelte';
	import { Badge, Button, IconButton, Input } from '$lib/components/ui';
	import AdminOnlyMark from '$lib/components/ui/AdminOnlyMark.svelte';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, DetailFooter, DetailField } from '$lib/components/detail';
	import {
		byteUnitOptions,
		addLimit,
		availableKinds,
		draftFromPlan,
		draftToBody,
		emptyDraft,
		isDraftDirty,
		isDraftValid,
		removeLimit,
		setLimitText,
		setLimitUnit,
		type ByteUnit,
		type PlanDraft
	} from '$lib/plans/editor';
	import { EntityPicker, applyDiff, type PickerDiff } from '$lib/components/picker';
	import { groupsKind } from '$lib/components/picker/kinds/groups';
	import { usersKind } from '$lib/components/picker/kinds/users';
	import { setGroupPlan, setUserPlan } from '$lib/plans/api';
	import type { User } from '$lib/stores/auth';
	import type { UserGroup } from '$lib/services/admin-api';
	import { formatLimitChip, kindIcon, kindUnitLabel } from '$lib/plans/format';
	import type { LimitKindDescriptor, Plan, PlanBody, PlanDetail } from '$lib/plans/types';
	import { formatLimitValue, percent } from '$lib/plans/format';

	let {
		plan,
		kinds,
		detail = null,
		groups = [],
		users = [],
		onAssignmentsChanged,
		saving = false,
		onSave,
		onDelete,
		onBack
	}: {
		plan: Plan | null;
		kinds: readonly LimitKindDescriptor[];
		detail?: PlanDetail | null;
		groups?: readonly UserGroup[];
		users?: readonly User[];
		onAssignmentsChanged?: () => void | Promise<void>;
		saving?: boolean;
		onSave: (body: PlanBody) => void | Promise<void>;
		onDelete?: () => void;
		onBack: () => void;
	} = $props();

	let draft = $state<PlanDraft>(untrack(() => (plan ? draftFromPlan(plan, kinds) : emptyDraft())));
	let snapshot = $state<PlanDraft>(untrack(() => (plan ? draftFromPlan(plan, kinds) : emptyDraft())));
	let pickerOpen = $state(false);
	let groupPickerOpen = $state(false);
	let userPickerOpen = $state(false);
	let assignError = $state('');
	const assignedGroupIds = $derived(new Set((detail?.assigned_to.groups ?? []).map((g) => g.id)));
	const assignedUserIds = $derived(new Set((detail?.assigned_to.users ?? []).map((u) => u.id)));

	async function applyAssignments(diff: PickerDiff, set: (id: string, planId: string | null) => Promise<unknown>) {
		if (!plan) return { ok: [], failed: [] };
		const planId = plan.id;
		const result = await applyDiff(diff, {
			assign: (id) => set(id, planId),
			unassign: (id) => set(id, null)
		});
		await onAssignmentsChanged?.();
		return result;
	}

	const applyGroups = (diff: PickerDiff) => applyAssignments(diff, setGroupPlan);
	const applyUsers = (diff: PickerDiff) => applyAssignments(diff, setUserPlan);

	async function removeAssignment(set: (id: string, planId: string | null) => Promise<unknown>, id: string) {
		assignError = '';
		try {
			await set(id, null);
			await onAssignmentsChanged?.();
		} catch (error) {
			assignError = error instanceof Error && error.message ? error.message : 'Could not remove the assignment';
		}
	}
	const planKey = $derived(plan ? JSON.stringify([plan.id, plan.name, plan.description, plan.limits]) : '');
	let loadedKey = untrack(() => planKey);

	$effect(() => {
		const key = planKey;
		if (key === loadedKey) return;
		untrack(() => {
			loadedKey = key;
			draft = plan ? draftFromPlan(plan, kinds) : emptyDraft();
			snapshot = plan ? draftFromPlan(plan, kinds) : emptyDraft();
		});
	});

	const dirty = $derived(isDraftDirty(draft, snapshot));
	const valid = $derived(isDraftValid(draft, kinds));
	const pickable = $derived(availableKinds(kinds, draft));
	const usedKinds = $derived(draft.limits.map((limit) => ({ limit, kind: kinds.find((k) => k.key === limit.kind) })));
	const summary = $derived(plan ? plan.limits.map((l) => ({ l, k: kinds.find((k) => k.key === l.kind) })).filter((x) => x.k) : []);

	function pick(kind: LimitKindDescriptor) {
		draft = addLimit(draft, kind);
		pickerOpen = false;
	}

	function discard() {
		draft = JSON.parse(JSON.stringify(snapshot));
	}

	async function save() {
		const body = draftToBody(draft, kinds);
		if (!body) return;
		await onSave(body);
	}
</script>

<div class="flex h-full flex-col">
	<DetailHeader title={plan ? plan.name : 'New plan'} icon="layers" backLabel="Plans" {onBack}>
		{#snippet chips()}
			{#each summary as entry (entry.l.kind)}
				<Badge size="sm" class="font-mono">{formatLimitChip(entry.k!, entry.l.value)}</Badge>
			{/each}
			{#if plan?.is_system}<Badge size="sm" variant="neutral">Built-in</Badge>{/if}
		{/snippet}
		{#snippet actions()}
			{#if plan && !plan.is_system && onDelete}
				<IconButton icon="trash" label="Delete plan" class="text-danger hover:text-danger hover:bg-danger/10" onclick={onDelete} />
			{/if}
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Identity">
					<div class="space-y-4">
						<DetailField label="Name" id="plan-name">
							<Input id="plan-name" type="text" bind:value={draft.name} disabled={plan?.is_system} />
						</DetailField>
						<DetailField label="Description" id="plan-description" wide>
							<textarea
								id="plan-description"
								class="input"
								rows="2"
								bind:value={draft.description}
								placeholder="Optional description"
								disabled={plan?.is_system}
							></textarea>
						</DetailField>
					</div>
				</DetailSection>

				<DetailSection label="Limits">
					<div class="space-y-3" data-plan-limits>
						<p class="text-sm text-fg-muted">Each row is one limit. A limit that is not here is unlimited.</p>
						{#each usedKinds as entry (entry.limit.kind)}
							{@const kind = entry.kind}
							<div class="flex items-center gap-4 rounded-lg border border-line bg-surface-2 p-3" data-limit-row={entry.limit.kind}>
								<span class="flex-shrink-0 text-signal"><Icon name={kind ? kindIcon(kind) : 'layers'} className="w-4 h-4" /></span>
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2">
										<p class="text-sm font-semibold text-fg truncate">{kind?.label ?? entry.limit.kind}</p>
										{#if kind?.plugin}<Badge size="sm" variant="neutral" class="font-mono">plugin</Badge>{/if}
										{#if kind?.value_type === 'usd' || kind?.admin_only_values}<AdminOnlyMark />{/if}
									</div>
									{#if kind?.description}<p class="text-xs text-fg-subtle">{kind.description}</p>{/if}
								</div>
								{#if entry.limit.inactiveValue !== undefined}
									<Badge size="sm" variant="warning" class="font-mono">inactive</Badge>
								{:else}
								<div class="flex flex-shrink-0 items-center gap-2">
									<Input
										class="w-28 font-mono tabular-nums"
										type="text"
										inputmode="decimal"
										aria-label="{kind?.label ?? entry.limit.kind} value"
										data-limit-input={entry.limit.kind}
										value={entry.limit.text}
										oninput={(e: Event) => (draft = setLimitText(draft, entry.limit.kind, (e.currentTarget as HTMLInputElement).value))}
									/>
									{#if kind?.value_type === 'bytes'}
										<div class="w-20" data-limit-unit={entry.limit.kind}>
											<CustomSelect
												size="sm"
												value={entry.limit.unit}
												options={byteUnitOptions(kind)}
												on:change={(e) => (draft = setLimitUnit(draft, entry.limit.kind, e.detail as ByteUnit))}
											/>
										</div>
									{:else if kind}
										<span class="font-mono text-xs text-fg-subtle">{kindUnitLabel(kind)}</span>
									{/if}
								</div>
								{/if}
								<IconButton
									icon="close"
									label="Remove {kind?.label ?? entry.limit.kind}"
									onclick={() => (draft = removeLimit(draft, entry.limit.kind))}
								/>
							</div>
						{/each}

						<div class="relative">
							<span data-add-limit class="inline-flex">
								<Button
									variant="secondary"
									size="sm"
									icon="plus"
									disabled={pickable.length === 0 || plan?.is_system}
									ariaExpanded={pickerOpen}
									ariaHaspopup="menu"
									onclick={() => (pickerOpen = !pickerOpen)}
								>
									Add limit
								</Button>
							</span>
							{#if pickerOpen}
								<div
									class="absolute left-0 top-full z-20 mt-1.5 w-[22rem] max-w-full rounded-xl border border-line-strong bg-surface-2 p-1.5 shadow-overlay"
									role="menu"
									aria-label="Add a limit"
									data-kind-picker
								>
									<p class="px-2.5 pb-1 pt-1.5 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Add a limit</p>
									{#each pickable as kind (kind.key)}
										<button
											type="button"
											role="menuitem"
											class="flex w-full items-start gap-2.5 rounded px-2.5 py-2 text-left hover:bg-surface-3"
											data-kind-option={kind.key}
											onclick={() => pick(kind)}
										>
											<span class="mt-0.5 text-fg-muted"><Icon name={kindIcon(kind)} className="w-4 h-4" /></span>
											<span class="min-w-0">
												<span class="flex items-center gap-2 text-sm text-fg">
													{kind.label}
													{#if kind.plugin}<Badge size="sm" variant="neutral" class="font-mono">plugin</Badge>{/if}
												</span>
												{#if kind.description}<span class="block text-xs text-fg-subtle">{kind.description}</span>{/if}
											</span>
										</button>
									{/each}
								</div>
							{/if}
						</div>
					</div>
				</DetailSection>
			{/snippet}

				{#snippet aside()}
					{#if plan && detail}
						<DetailSection label="Assigned to">
							<div class="space-y-4" data-plan-assigned>
								<div>
									<p class="mb-1.5 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Groups</p>
									{#each detail.assigned_to.groups as group (group.id)}
										<div class="flex items-center justify-between gap-2 py-1 text-sm">
											<span class="truncate text-fg">{group.name}</span>
											<span class="flex flex-shrink-0 items-center gap-1">
												<Badge size="sm" class="font-mono">{group.members} {group.members === 1 ? 'member' : 'members'}</Badge>
												<IconButton
													icon="close"
													label={`Remove ${group.name} from this plan`}
													size="sm"
													onclick={() => removeAssignment(setGroupPlan, group.id)}
												/>
											</span>
										</div>
									{:else}
										<p class="text-sm text-fg-muted">No groups</p>
									{/each}
									<div class="mt-2">
										<Button size="xs" variant="secondary" icon="plus" onclick={() => (groupPickerOpen = true)}>Add groups</Button>
									</div>
								</div>
								<div>
									<p class="mb-1.5 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Personal overrides</p>
									{#each detail.assigned_to.users as user (user.id)}
										<div class="flex items-center justify-between gap-2 py-1 text-sm">
											<span class="truncate text-fg">{user.username}</span>
											<span class="flex flex-shrink-0 items-center gap-1">
												<span class="text-fg-subtle">override</span>
												<IconButton
													icon="close"
													label={`Remove ${user.username} from this plan`}
													size="sm"
													onclick={() => removeAssignment(setUserPlan, user.id)}
												/>
											</span>
										</div>
									{:else}
										<p class="text-sm text-fg-muted">No overrides</p>
									{/each}
									<div class="mt-2">
										<Button size="xs" variant="secondary" icon="plus" onclick={() => (userPickerOpen = true)}>Add users</Button>
									</div>
								</div>
								{#if assignError}
									<p class="text-sm text-danger" role="alert" data-plan-assign-error>{assignError}</p>
								{/if}
							</div>
						</DetailSection>
						<DetailSection label="In use, per limit">
							<div class="space-y-3" data-plan-in-use>
								{#each detail.in_use.kinds as entry (entry.kind)}
									{@const kind = kinds.find((k) => k.key === entry.kind)}
									{#if kind}
										<div>
											<div class="flex justify-between text-sm">
												<span class="text-fg-muted">{kind.short_label ?? kind.label}</span>
												<span class="font-mono text-xs tabular-nums text-fg">
													{formatLimitValue(kind, entry.used_total)}{#if entry.limit_total !== null} / {formatLimitValue(kind, entry.limit_total)}{/if}
												</span>
											</div>
											{#if entry.limit_total !== null}
												<div class="mt-1 h-1.5 overflow-hidden rounded bg-surface-3">
													<div class="h-full rounded bg-signal" style="width: {percent(entry.used_total, entry.limit_total) ?? 0}%"></div>
												</div>
											{/if}
										</div>
									{/if}
								{/each}
								<p class="text-sm text-fg-muted">
									{detail.in_use.above_warn} of {detail.in_use.people} {detail.in_use.people === 1 ? 'person is' : 'people are'} above 80 percent of a limit.
								</p>
							</div>
						</DetailSection>
					{/if}
				{/snippet}
		</DetailLayout>
	</DetailBody>

	{#if plan}
		<EntityPicker
			kind={groupsKind}
			items={groups}
			assignedIds={assignedGroupIds}
			isOpen={groupPickerOpen}
			title="Assign groups to this plan"
			subtitle={plan.name}
			onApply={applyGroups}
			onClose={() => (groupPickerOpen = false)}
		/>
		<EntityPicker
			kind={usersKind}
			items={users}
			assignedIds={assignedUserIds}
			isOpen={userPickerOpen}
			title="Assign users to this plan"
			subtitle={plan.name}
			onApply={applyUsers}
			onClose={() => (userPickerOpen = false)}
		/>
	{/if}

	<DetailFooter
		mode={plan ? 'edit' : 'create'}
		dirtyCount={dirty ? 1 : 0}
		{saving}
		canSave={valid}
		onSave={save}
		onDiscard={discard}
	/>
</div>
