<script lang="ts">
	import { Badge, Button, SegmentedControl, Spinner } from '$lib/components/ui';
	import type { FieldIndex } from '$lib/formulas/fieldIndex';
	import { formatValue } from '$lib/formulas/format';
	import { loraRowsOf, type ApplyPlan } from '$lib/formulas/planApply';
	import type { Formula, LoraMode, PlanChange } from '$lib/formulas/types';

	const LORA_MODES = [
		{ id: 'replace', label: 'Replace list' },
		{ id: 'add', label: 'Add to list' }
	];

	let {
		formula,
		plan,
		loading,
		error,
		index,
		groupOrder,
		presetName,
		modeLabel,
		loraMode,
		selected,
		ready,
		onToggle,
		onLoraMode,
		onApply,
		onCancel
	}: {
		formula: Formula;
		plan: ApplyPlan | null;
		loading: boolean;
		error: string | null;
		index: FieldIndex;
		groupOrder: string[];
		presetName: string;
		modeLabel: string;
		loraMode: LoraMode;
		selected: Set<string>;
		ready: boolean;
		onToggle: (field: string) => void;
		onLoraMode: (mode: LoraMode) => void;
		onApply: () => void;
		onCancel: () => void;
	} = $props();

	let visible = $derived((plan?.changes ?? []).filter((change) => !change.companionOf));

	let sections = $derived.by(() => {
		const byGroup = new Map<string, { label: string; changes: PlanChange[] }>();
		for (const change of visible) {
			const section = byGroup.get(change.group) ?? { label: change.groupLabel, changes: [] };
			section.changes.push(change);
			byGroup.set(change.group, section);
		}
		const order = (id: string) => {
			const position = groupOrder.indexOf(id);
			return position < 0 ? groupOrder.length : position;
		};
		return [...byGroup.entries()].sort((a, b) => order(a[0]) - order(b[0])).map(([id, section]) => ({ id, ...section }));
	});

	let chosen = $derived(visible.filter((change) => selected.has(change.field)).length);
	let hasLora = $derived((plan?.changes ?? []).some((change) => isLora(change)) || formula.groups.some((g) => g.fields.some((f) => index.get(f)?.type === 'lora_picker')));
	let sameCount = $derived((plan?.same ?? []).filter((item) => index.has(item.field)).length);
	let nothingToDo = $derived(!!plan && visible.length === 0);

	function isLora(change: PlanChange): boolean {
		return (change.type ?? index.get(change.field)?.type) === 'lora_picker';
	}

	function isAdvanced(change: PlanChange): boolean {
		return change.advanced ?? index.get(change.field)?.advanced ?? false;
	}

	function show(change: PlanChange, value: unknown): string {
		return formatValue(index.get(change.field) ?? { type: change.type }, value);
	}

	function strength(value: number | null): string {
		return value === null ? '' : value.toFixed(2);
	}
</script>

<div class="flex-shrink-0 border-b border-line px-4 py-3">
	<div class="truncate text-md font-semibold text-fg">{formula.name}</div>
	<div class="truncate font-mono text-xs text-fg-subtle">into the current form · {presetName} · {modeLabel}</div>
</div>

<div class="drawer-scroll min-h-0 flex-1 overflow-y-auto pb-3" data-testid="formula-apply-scroll">
	{#if loading && !plan}
		<div class="flex justify-center py-8"><Spinner size="sm" /></div>
	{:else if error}
		<p class="px-4 py-4 text-sm text-danger" role="alert">{error}</p>
	{:else if plan}
		<div class="flex flex-wrap gap-1.5 px-4 pb-1 pt-3" data-testid="formula-plan-chips">
			<Badge variant="signal" size="sm"><span class="font-mono tabular-nums">{visible.length}</span>&nbsp;will change</Badge>
			<Badge variant="neutral" size="sm"><span class="font-mono tabular-nums">{sameCount}</span>&nbsp;already match</Badge>
			{#if plan.skips.length > 0}
				<Badge variant="warning" size="sm"><span class="font-mono tabular-nums">{plan.skips.length}</span>&nbsp;skipped</Badge>
			{/if}
		</div>

		{#if hasLora}
			<div class="flex items-center justify-between gap-3 px-4 pt-3">
				<span class="text-xs text-fg-muted">LoRAs</span>
				<SegmentedControl
					variant="toggle"
					ariaLabel="LoRA apply mode"
					items={LORA_MODES}
					selected={loraMode}
					onSelect={(id) => onLoraMode(id as LoraMode)}
				/>
			</div>
		{/if}

		{#if nothingToDo}
			<p class="px-4 py-6 text-center text-sm text-fg-muted">The form already matches this formula.</p>
		{/if}

		{#each sections as section (section.id)}
			<div class="section-head">{section.label}<span>{section.changes.length}</span></div>
			<ul class="m-0 list-none p-0">
				{#each section.changes as change (change.field)}
					<li class="px-4 py-2" data-change-field={change.field}>
						<div class="flex items-center gap-3">
							<input
								type="checkbox"
								class="h-4 w-4 flex-shrink-0"
								checked={selected.has(change.field)}
								aria-label={`Apply ${change.label}`}
								onchange={() => onToggle(change.field)}
							/>
							<span class="min-w-0 flex-1 truncate text-sm text-fg">{change.label}</span>
							{#if isAdvanced(change)}
								<span class="flex-shrink-0 rounded-sm border border-line-strong bg-surface-2 px-1.5 font-mono text-xs text-fg-muted">Advanced view</span>
							{/if}
						</div>
						{#if isLora(change)}
							<ul class="m-0 mt-1.5 list-none pl-7">
								{#each loraRowsOf(change) as row (row.key)}
									<li class="flex items-baseline gap-2 py-0.5 font-mono text-xs tabular-nums {row.status === 'removed' ? 'text-fg-subtle line-through' : 'text-fg'}">
										<span class="w-3 flex-shrink-0 {row.status === 'added' ? 'text-success' : row.status === 'removed' ? 'text-danger' : 'text-fg-subtle'}">
											{row.status === 'added' ? '+' : row.status === 'removed' ? '−' : row.status === 'changed' ? '~' : '='}
										</span>
										<span class="min-w-0 flex-1 truncate">{row.name}</span>
										<span class="flex-shrink-0">
											{#if row.status === 'changed'}{strength(row.oldStrength)} → {strength(row.newStrength)}{:else}{strength(row.status === 'removed' ? row.oldStrength : row.newStrength)}{/if}
										</span>
									</li>
								{/each}
							</ul>
						{:else}
							<div class="mt-0.5 flex min-w-0 items-baseline gap-1.5 pl-7 font-mono text-xs tabular-nums">
								<span class="max-w-[45%] truncate text-fg-subtle">{show(change, change.old)}</span>
								<span class="text-signal" aria-hidden="true">→</span>
								<span class="min-w-0 truncate text-fg">{show(change, change.new)}</span>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/each}

		{#if plan.skips.length > 0}
			<div class="section-head">Skipped<span>{plan.skips.length}</span></div>
			<ul class="m-0 flex list-none flex-col gap-2 px-4 pb-1 pt-1" data-testid="formula-skips">
				{#each plan.skips as skip, i (`${skip.field}-${skip.row ?? ''}-${i}`)}
					<li class="rounded-lg border border-warning/25 bg-warning/10 px-3 py-2">
						<div class="flex items-baseline justify-between gap-2">
							<span class="min-w-0 truncate font-mono text-xs text-fg">{skip.label}{#if skip.field && skip.field !== skip.label && !skip.row}<span class="text-fg-subtle"> ({skip.field})</span>{/if}</span>
							{#if skip.library}
								<a class="flex-shrink-0 text-xs text-signal hover:underline" href="/models" target="_blank" rel="noopener">Find in library</a>
							{/if}
						</div>
						<p class="mt-0.5 text-xs text-fg-muted">{skip.reason}</p>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</div>

<footer class="flex min-h-[48px] flex-shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-2">
	<span class="min-w-0 text-xs text-fg-muted">Prompt, media and seed stay as they are. You can undo right after.</span>
	<div class="flex flex-shrink-0 gap-2">
		<Button variant="secondary" size="sm" onclick={onCancel}>Cancel</Button>
		<Button variant="primary" size="sm" disabled={chosen === 0 || loading || !!error || !ready} onclick={onApply}>
			Apply <span class="font-mono tabular-nums">{chosen}</span> {chosen === 1 ? 'change' : 'changes'}
		</Button>
	</div>
</footer>

<style>
	.section-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 28px;
		margin-top: 8px;
		padding: 0 16px;
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 12px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}

	.section-head span {
		color: rgb(var(--fg-disabled));
		font-variant-numeric: tabular-nums;
	}
</style>
