<script lang="ts">
	import { DetailHeader, DetailBody, DetailFooter, DetailLayout, DetailSection } from '$lib/components/detail';
	import { Alert, Input, Switch } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import {
		draftFromRule,
		draftProblems,
		draftSignature,
		draftToInput,
		type RuleDraft
	} from '$lib/organize/draft';
	import { parseOrganizeError, problemMessage } from '$lib/organize/errors';
	import { triggerSentence } from '$lib/organize/sentence';
	import type { OrganizeCatalog, OrganizeJob, OrganizePreview, OrganizeRule } from '$lib/types/organize';
	import ActionBuilder from './ActionBuilder.svelte';
	import ConditionBuilder from './ConditionBuilder.svelte';
	import JobProgress from './JobProgress.svelte';
	import RulePreview from './RulePreview.svelte';

	let {
		initial,
		catalog,
		onBack,
		onSaved,
		onViewActivity
	}: {
		initial: RuleDraft;
		catalog: OrganizeCatalog;
		onBack: () => void;
		onSaved?: (rule: OrganizeRule) => void;
		onViewActivity?: () => void;
	} = $props();

	let draft = $state<RuleDraft>(structuredClone($state.snapshot(initial)));
	let baseline = $state(draftSignature(initial));
	let savedSnapshot = $state<RuleDraft>(structuredClone($state.snapshot(initial)));
	let saving = $state(false);
	let serverError = $state<string | null>(null);
	let applyExisting = $state(false);
	let preview = $state<OrganizePreview | null>(null);
	let job = $state<OrganizeJob | null>(null);

	const dirty = $derived(draftSignature(draft) !== baseline);
	const problems = $derived(draftProblems(draft, catalog));
	const isNew = $derived(draft.id === null);

	async function handleSave() {
		if (problems.length > 0) {
			serverError = problems[0];
			return;
		}
		serverError = null;
		saving = true;
		try {
			const input = draftToInput(draft);
			const response = draft.id
				? await api.updateOrganizeRule(draft.id, input)
				: await api.createOrganizeRule(input);
			if (!response.success || !response.data) {
				serverError = response.message || 'The rule could not be saved.';
				return;
			}
			const rule = response.data;
			const fresh = draftFromRule(rule);
			draft = fresh;
			savedSnapshot = structuredClone($state.snapshot(fresh));
			baseline = draftSignature(fresh);
			onSaved?.(rule);
			toasts.success(`Saved "${rule.name}"`);
			if (applyExisting && (preview?.would_change ?? 0) > 0) {
				applyExisting = false;
				await startBackfill(rule);
			}
		} catch (err) {
			serverError = problemMessage(parseOrganizeError(err, 'The rule could not be saved.'));
		} finally {
			saving = false;
		}
	}

	async function startBackfill(rule: OrganizeRule) {
		try {
			const response = await api.applyOrganizeRuleToExisting(rule.id);
			if (response.success && response.data) job = response.data;
		} catch (err) {
			const info = parseOrganizeError(err, 'The existing items could not be added.');
			if (info.code === 'job_running' && info.jobId) {
				const existing = await api.getOrganizeJob(info.jobId);
				if (existing.success && existing.data) job = existing.data;
				return;
			}
			toasts.error(info.message);
		}
	}

	async function handleApplyNow() {
		if (!draft.id || dirty) return;
		await startBackfill({ id: draft.id, name: draft.name } as OrganizeRule);
	}

	function handleDiscard() {
		draft = structuredClone($state.snapshot(savedSnapshot));
		baseline = draftSignature(savedSnapshot);
		serverError = null;
	}

	async function handleBack() {
		if (dirty) {
			const leave = await confirmDialog({
				title: 'Leave without saving?',
				message: 'Your changes to this rule will be lost.',
				variant: 'warning'
			});
			if (!leave) return;
		}
		onBack();
	}
</script>

<div class="flex h-full min-h-0 flex-col">
	<DetailHeader title={draft.name.trim() || 'New rule'} icon="wand" backLabel="All rules" onBack={handleBack}>
		{#snippet enabledSwitch()}
			<div class="flex items-center gap-2 text-xs text-fg-muted">
				<Switch label="Rule enabled" checked={draft.enabled} onchange={(next) => (draft.enabled = next)} />
				<span>Enabled</span>
			</div>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				<DetailSection label="Name">
					<Input
						value={draft.name}
						placeholder="For example: Krea landscapes"
						aria-label="Rule name"
						maxlength="120"
						oninput={(event: Event) => (draft.name = (event.currentTarget as HTMLInputElement).value)}
					/>
				</DetailSection>

				<DetailSection label="When">
					<p class="text-sm text-fg">{triggerSentence(draft.subject, catalog)}</p>
				</DetailSection>

				<DetailSection label="If">
					<ConditionBuilder bind:draft {catalog} />
				</DetailSection>

				<DetailSection label="Then">
					<ActionBuilder bind:draft {catalog} />
				</DetailSection>

				<DetailSection label="Options">
					<div class="flex items-center gap-2">
						<Switch
							label="Stop after this rule"
							checked={draft.stop_after}
							onchange={(next) => (draft.stop_after = next)}
						/>
						<span class="text-sm text-fg">Stop after this rule</span>
					</div>
					<p class="mt-1 text-xs text-fg-subtle">
						When this rule matches an item, the rules below it skip that item.
					</p>
				</DetailSection>

				{#if serverError}
					<Alert variant="danger" icon>{serverError}</Alert>
				{/if}
			{/snippet}

			{#snippet aside()}
				<DetailSection label="Preview">
					<RulePreview {draft} {catalog} bind:applyExisting onpreview={(next) => (preview = next)} />
					{#if !isNew && !dirty && !job && (preview?.would_change ?? 0) > 0}
						<button
							type="button"
							class="mt-3 text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline"
							onclick={handleApplyNow}
						>
							Add the existing items now
						</button>
					{/if}
				</DetailSection>
				{#if job}
					<DetailSection label="Existing items">
						<JobProgress {job} onviewactivity={onViewActivity} />
					</DetailSection>
				{/if}
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter
		dirtyCount={dirty ? 1 : 0}
		mode={isNew ? 'create' : 'edit'}
		{saving}
		canSave={true}
		onSave={handleSave}
		onDiscard={handleDiscard}
	/>
</div>
