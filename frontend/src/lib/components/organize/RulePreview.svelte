<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Alert, Spinner, Switch } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import {
		actionTargetFilled,
		conditionValueFilled,
		draftActions,
		draftConditions,
		effectiveKind,
		type RuleDraft
	} from '$lib/organize/draft';
	import { subjectIcon } from '$lib/organize/icons';
	import { subjectNoun } from '$lib/organize/sentence';
	import type { OrganizeCatalog, OrganizePreview } from '$lib/types/organize';

	let {
		draft,
		catalog,
		applyExisting = $bindable(false),
		debounceMs = 500,
		onpreview
	}: {
		draft: RuleDraft;
		catalog: OrganizeCatalog;
		applyExisting?: boolean;
		debounceMs?: number;
		onpreview?: (preview: OrganizePreview | null) => void;
	} = $props();

	let preview = $state<OrganizePreview | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let sequence = 0;

	const ready = $derived(
		draft.conditions.every((c) =>
			conditionValueFilled(effectiveKind(catalog.facts.find((f) => f.key === c.fact)), c.value)
		)
	);
	const noun = $derived(subjectNoun(draft.subject));
	const filledActions = $derived(draftActions(draft).filter((a) => actionTargetFilled({ uid: '', ...a })));
	const signature = $derived(
		JSON.stringify([draft.id, draft.subject, draft.match, draftConditions(draft), filledActions])
	);

	$effect(() => {
		void signature;
		clearTimeout(timer);
		if (!ready) {
			preview = null;
			onpreview?.(null);
			return;
		}
		loading = true;
		const mine = ++sequence;
		const request = {
			subject: draft.subject,
			match: draft.match,
			conditions: draftConditions(draft),
			actions: filledActions,
			rule_id: draft.id
		};
		timer = setTimeout(async () => {
			try {
				const response = await api.previewOrganizeRule(request);
				if (mine !== sequence) return;
				failed = !response.success;
				preview = response.success ? (response.data ?? null) : null;
			} catch {
				if (mine !== sequence) return;
				failed = true;
				preview = null;
			}
			loading = false;
			onpreview?.(preview);
		}, debounceMs);
		return () => clearTimeout(timer);
	});

	const change = $derived(preview?.would_change ?? 0);
</script>

<div class="space-y-3" data-testid="rule-preview">
	{#if !ready}
		<p class="flex items-center gap-2 text-sm text-fg-muted">
			<Icon name="eye-off" className="w-3.5 h-3.5 flex-shrink-0" />
			Finish the conditions to see what the rule would catch.
		</p>
	{:else if failed}
		<p class="flex items-center gap-2 text-sm text-fg-muted">
			<Icon name="warning" className="w-3.5 h-3.5 flex-shrink-0" />
			The count is not available right now.
		</p>
	{:else if preview}
		<div>
			<div class="flex items-baseline gap-2">
				<span class="text-3xl font-semibold tabular-nums text-fg" data-testid="preview-matched">
					{preview.approximate ? 'About ' : ''}{preview.matched}
				</span>
				{#if loading}<Spinner size="sm" />{/if}
			</div>
			<p class="flex items-center gap-2 text-sm text-fg-muted">
				<Icon name={subjectIcon(draft.subject)} className="w-3.5 h-3.5 flex-shrink-0" />
				existing {preview.matched === 1 ? noun.singular : noun.plural} match
			</p>
		</div>
		{#if preview.already_handled > 0}
			<p class="text-xs text-fg-subtle">{preview.already_handled} already handled by this rule.</p>
		{/if}
		{#if preview.duplicates.length > 0}
			<Alert variant="warning" icon density="compact">
				{preview.duplicates.length === 1
					? `Another rule, "${preview.duplicates[0].name}", already has the same conditions.`
					: 'Other rules already have the same conditions.'}
			</Alert>
		{/if}
		{#if change > 0}
			<div class="flex items-start gap-3 rounded-lg border border-line bg-surface-2 p-3">
				<Switch
					size="sm"
					class="mt-0.5"
					testid="apply-existing"
					label="Also add the {change} existing {change === 1 ? noun.singular : noun.plural} when I save"
					bind:checked={applyExisting}
				/>
				<div class="min-w-0">
					<p class="text-sm text-fg">
						Also add the {change} existing {change === 1 ? noun.singular : noun.plural} when I save
					</p>
					<p class="mt-0.5 text-xs text-fg-subtle">Rules only add. You can undo any run from Activity.</p>
				</div>
			</div>
		{:else}
			<p class="flex items-center gap-2 text-xs text-fg-subtle">
				<Icon name="check-circle" className="w-3.5 h-3.5 flex-shrink-0" />
				Nothing existing would change.
			</p>
			<p class="text-xs text-fg-subtle">Rules only add. You can undo any run from Activity.</p>
		{/if}
	{:else}
		<div class="flex items-center gap-2 text-sm text-fg-muted"><Spinner size="sm" /> Counting</div>
	{/if}
</div>
