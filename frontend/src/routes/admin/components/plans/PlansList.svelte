<script lang="ts">
	import { Badge, Card, EmptyState, Input, Switch } from '$lib/components/ui';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { formatLimitChip, formatLimitValue, kindIcon } from '$lib/plans/format';
	import type { LimitKindDescriptor, Plan, PlansSettings } from '$lib/plans/types';

	let {
		plans,
		kinds,
		settings,
		onOpen,
		onSettings
	}: {
		plans: readonly Plan[];
		kinds: readonly LimitKindDescriptor[];
		settings: PlansSettings;
		onOpen: (plan: Plan) => void;
		onSettings: (patch: Partial<PlansSettings>) => void;
	} = $props();

	let contactLine = $state('');
	let timezone = $state('');

	$effect(() => {
		contactLine = settings.contact_line;
		timezone = settings.day_timezone;
	});

	const defaultOptions = $derived(plans.map((plan) => ({ value: plan.id, label: plan.name })));

	function chips(plan: Plan) {
		return plan.limits.flatMap((limit) => {
			const kind = kinds.find((k) => k.key === limit.kind);
			return kind ? [{ kind, text: formatLimitChip(kind, limit.value) }] : [];
		});
	}

	function usageTotals(plan: Plan): string {
		return (plan.usage?.kinds ?? [])
			.flatMap((entry) => {
				const kind = kinds.find((k) => k.key === entry.kind);
				return kind ?[`${formatLimitValue(kind, entry.used_total)}${kind.window === 'day' ? ' today' : ''}`] : [];
			})
			.join(' - ');
	}

	function plural(count: number, noun: string): string {
		return `${count} ${noun}${count === 1 ? '' : 's'}`;
	}
</script>

<div class="flex flex-col gap-4 p-4" data-plans-list>
	<Card>
		<div class="grid gap-4 lg:grid-cols-[1fr_auto] lg:items-start">
			<div class="min-w-0">
				<h3 class="text-sm font-semibold text-fg">Default plan</h3>
				<p class="mt-1 max-w-xl text-sm text-fg-muted">
					Used for everyone who has no plan from another group and no personal override. It is the plan on the built-in
					<span class="font-semibold text-fg">All users</span> group.
				</p>
			</div>
			<div class="w-full min-w-[14rem] lg:w-72" data-default-plan>
				<CustomSelect
					size="sm"
					value={settings.default_plan_id ?? ''}
					options={[{ value: '', label: 'No plan (unlimited)' }, ...defaultOptions]}
					on:change={(e) => onSettings({ default_plan_id: e.detail === '' ? null : String(e.detail) })}
				/>
			</div>
		</div>
		<div class="mt-4 grid gap-4 border-t border-line pt-4 lg:grid-cols-[auto_auto_1fr] lg:items-center">
			<div class="flex items-center gap-2.5">
				<Switch
					checked={settings.exempt_admins}
					label="Admins are exempt"
					onchange={(next) => onSettings({ exempt_admins: next })}
				/>
				<span class="text-sm text-fg">Admins are exempt</span>
			</div>
			<label class="flex items-center gap-2 text-sm text-fg-muted">
				Day resets at midnight
				<Input
					class="w-28 font-mono"
					type="text"
					aria-label="Day timezone"
					bind:value={timezone}
					onchange={() => timezone.trim() && onSettings({ day_timezone: timezone.trim() })}
				/>
			</label>
			<label class="flex items-center gap-2 text-sm text-fg-muted lg:justify-end">
				<span class="flex-shrink-0">When a limit is reached, add</span>
				<Input
					class="min-w-0 flex-1 lg:max-w-xs"
					type="text"
					aria-label="Contact line"
					bind:value={contactLine}
					onchange={() => onSettings({ contact_line: contactLine })}
				/>
			</label>
		</div>
	</Card>

	{#if plans.length === 0}
		<EmptyState icon="layers" title="No plans yet" description="A plan is a named list of limits. Create one, then assign it to a group." compact />
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
			{#each plans as plan (plan.id)}
				<button
					type="button"
					class="rounded-lg border border-line bg-surface-1 p-4 text-left shadow-raised transition-colors hover:border-line-hover"
					data-plan-card={plan.id}
					onclick={() => onOpen(plan)}
				>
					<div class="flex items-center justify-between gap-2">
						<span class="truncate text-sm font-semibold text-fg">{plan.name}</span>
						{#if plan.is_default}
							<Badge size="sm" variant="signal" class="font-mono">Default</Badge>
						{:else if plan.is_system}
							<Badge size="sm" variant="neutral" class="font-mono">built-in</Badge>
						{/if}
					</div>
					<div class="mt-2 flex flex-wrap gap-1.5">
						{#each chips(plan) as chip (chip.kind.key)}
							<Badge size="sm" class="font-mono">
								<Icon name={kindIcon(chip.kind)} className="w-3 h-3 mr-1" />
								{chip.text}
							</Badge>
						{:else}
							<p class="text-sm text-fg-muted">No limits - everything unlimited</p>
						{/each}
					</div>
					<p class="mt-3 text-sm text-fg-muted">
						Assigned to
						<span class="font-mono text-fg">{plural(plan.assigned?.groups ?? 0, 'group')} - {plural(plan.assigned?.users ?? 0, 'user')}</span>
					</p>
					<div class="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3 font-mono text-xs tabular-nums text-fg-muted">
						<span>{plural(plan.usage?.members ?? 0, 'member')}</span>
						<span class="truncate">{usageTotals(plan)}</span>
					</div>
				</button>
			{/each}
		</div>
	{/if}

	<p class="max-w-xl text-sm text-fg-subtle">
		A plan lists the limits it has; a limit that is not listed is unlimited. With no plan assigned nothing is limited and users see no usage rows.	</p>
</div>
